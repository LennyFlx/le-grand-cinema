from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from stripe import APIConnectionError, InvalidRequestError

from cinema.models import Film, Place, Salle, Seance
from reservations.models import Billet, EnvoiBillets, Reservation, ReservationPlace
from reservations.services import compter_places_disponibles

from paiements.models import Paiement
from paiements.services import demarrer_paiement, traiter_session_checkout


@override_settings(STRIPE_SECRET_KEY="sk_test_exemple", STRIPE_WEBHOOK_SECRET="whsec_exemple", ALLOWED_HOSTS=["testserver", "localhost"])
class ParcoursPaiementTests(TestCase):
    def setUp(self):
        self.maintenant = timezone.now().replace(microsecond=0)
        horloge = patch("paiements.services.timezone.now", return_value=self.maintenant)
        horloge.start()
        self.addCleanup(horloge.stop)
        film = Film.objects.create(titre="Film Checkout", duree=90)
        salle = Salle.objects.create(nom="Salle Checkout", capacite=1)
        self.seance = Seance.objects.create(
            film=film, salle=salle, debut=self.maintenant + timedelta(hours=2),
            fin=self.maintenant + timedelta(hours=3, minutes=30), prix=Decimal("12.35"),
        )
        place = Place.objects.create(salle=salle, rangee="A", numero=1)
        self.reservation = Reservation.objects.create(
            seance=self.seance, email="spectateur@example.com",
            date_expiration=self.maintenant + timedelta(minutes=30),
            montant_total=Decimal("12.35"),
        )
        ReservationPlace.objects.create(reservation=self.reservation, place=place, prix_unitaire=Decimal("12.35"))
        session = self.client.session
        session["reservation_provisoire_id"] = self.reservation.pk
        session.save()
        self.url = reverse("paiements:payer_reservation", args=[self.reservation.pk])
        self.detail = reverse("reservations:detail_reservation", args=[self.reservation.pk])
        client_stripe = patch("paiements.services.StripeClient")
        self.stripe = client_stripe.start().return_value
        self.addCleanup(client_stripe.stop)
        self.creation = self.stripe.v1.checkout.sessions.create
        self.lecture = self.stripe.v1.checkout.sessions.retrieve
        self.creation.side_effect = self.creer_session
        self.lecture.side_effect = lambda identifiant: self.session_stripe()

    def session_stripe(self, statut="open"):
        donnees = {
            "id": "cs_test_parcours", "url": "https://checkout.stripe.com/c/pay/cs_test_parcours",
            "mode": "payment", "status": statut,
            "payment_status": "paid" if statut == "complete" else "unpaid",
            "payment_intent": "pi_test_parcours" if statut == "complete" else None,
            "currency": "eur", "amount_total": 1235,
            "client_reference_id": str(self.reservation.pk),
            "metadata": {"reservation_id": str(self.reservation.pk)},
        }
        session = Mock()
        session.to_dict.return_value = donnees
        return session

    def creer_session(self, parametres, options):
        paiement = Paiement.objects.get()
        self.assertIsNone(paiement.stripe_session_id)
        self.assertEqual(parametres["expires_at"], int(paiement.date_expiration.timestamp()))
        return self.session_stripe()

    def test_bouton_puis_creation_enregistree_et_redirection(self):
        self.assertContains(self.client.get(self.detail), "Payer 12,35")
        response = self.client.post(self.url, {"montant": "0.01"})
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response["Location"].startswith("https://checkout.stripe.com/"))
        paiement = Paiement.objects.get()
        self.assertEqual(paiement.stripe_session_id, "cs_test_parcours")
        self.assertEqual(paiement.montant, Decimal("12.35"))
        self.assertEqual(paiement.date_expiration, self.maintenant + timedelta(minutes=31))
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.date_expiration, paiement.date_expiration)
        self.assertEqual(self.reservation.statut, Reservation.Statut.EN_ATTENTE)
        self.assertEqual(self.creation.call_args.args[0]["line_items"][0]["price_data"]["unit_amount"], 1235)

    def test_second_clic_reprend_la_meme_session_sans_prolongation(self):
        self.client.post(self.url)
        expiration = Paiement.objects.get().date_expiration
        with patch("paiements.services.timezone.now", return_value=self.maintenant + timedelta(minutes=10)):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(Paiement.objects.count(), 1)
        self.creation.assert_called_once()
        self.lecture.assert_called_once_with("cs_test_parcours")
        self.assertEqual(Paiement.objects.get().date_expiration, expiration)

    def test_coupure_reseau_conserve_tentative_et_parametres_pour_reprise(self):
        self.creation.side_effect = APIConnectionError("Interruption")
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        paiement = Paiement.objects.get()
        premier_appel = self.creation.call_args
        self.assertIsNone(paiement.stripe_session_id)
        self.assertEqual(compter_places_disponibles(self.seance), 0)
        self.creation.side_effect = lambda *args, **kwargs: self.session_stripe()
        with patch("paiements.services.timezone.now", return_value=self.maintenant + timedelta(seconds=10)):
            self.assertEqual(self.client.post(self.url).status_code, 303)
        self.assertEqual(self.creation.call_args, premier_appel)
        self.assertEqual(Paiement.objects.count(), 1)

    def test_reprise_depuis_autre_hote_conserve_url_initiale(self):
        self.creation.side_effect = APIConnectionError("Interruption")
        self.client.post(self.url)
        premier_appel = self.creation.call_args
        self.creation.side_effect = lambda *args, **kwargs: self.session_stripe()
        self.client.post(self.url, HTTP_HOST="localhost")
        self.assertEqual(self.creation.call_args, premier_appel)

    def test_session_autre_navigateur_get_et_csrf_refuses(self):
        self.assertEqual(Client().post(self.url).status_code, 404)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(Client(enforce_csrf_checks=True).post(self.url).status_code, 403)
        self.assertFalse(Paiement.objects.exists())
        self.creation.assert_not_called()

    def test_reservations_non_payables_refusees_avant_appel_stripe(self):
        for statut in (Reservation.Statut.CONFIRMEE, Reservation.Statut.ANNULEE, Reservation.Statut.EXPIREE):
            Reservation.objects.filter(pk=self.reservation.pk).update(statut=statut)
            with self.subTest(statut=statut):
                self.assertEqual(self.client.post(self.url).status_code, 302)
        self.creation.assert_not_called()
        self.assertFalse(Paiement.objects.exists())

    def test_reservation_echue_et_seance_proche_refusees(self):
        Reservation.objects.filter(pk=self.reservation.pk).update(date_expiration=self.maintenant)
        self.client.post(self.url)
        Reservation.objects.filter(pk=self.reservation.pk).update(date_expiration=self.maintenant + timedelta(minutes=20))
        Seance.objects.filter(pk=self.seance.pk).update(debut=self.maintenant + timedelta(minutes=20))
        self.assertContains(self.client.get(self.detail), "Il ferme 31 minutes")
        self.client.post(self.url)
        self.creation.assert_not_called()
        self.assertFalse(Paiement.objects.exists())

    @override_settings(STRIPE_WEBHOOK_SECRET="")
    def test_configuration_incomplete_ne_cree_pas_de_tentative(self):
        response = self.client.post(self.url, follow=True)
        self.assertContains(response, "momentanément indisponible")
        self.assertFalse(Paiement.objects.exists())
        self.creation.assert_not_called()

    def test_retour_succes_ne_vaut_pas_preuve_de_paiement(self):
        self.assertNotContains(self.client.get(self.detail + "?retour=succes"), "Votre paiement est confirmé")
        self.lecture.assert_not_called()
        self.client.post(self.url)
        page = self.client.get(self.detail + "?retour=succes")
        self.assertContains(page, "confirmation du paiement est en cours")
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.EN_ATTENTE)
        self.assertFalse(Billet.objects.exists())

    def test_retour_verifie_le_succes_stripe_si_webhook_retarde(self):
        self.client.post(self.url)
        self.lecture.side_effect = lambda identifiant: self.session_stripe("complete")
        page = self.client.get(self.detail + "?retour=succes")
        self.assertContains(page, "Votre paiement est confirmé")
        self.assertContains(page, "Voir mes billets")
        self.assertEqual(Client().get(page.context["url_billets"]).status_code, 200)
        self.assertNotContains(page, 'action="' + self.url + '"')
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.CONFIRMEE)
        self.assertEqual(
            EnvoiBillets.objects.get(reservation=self.reservation).statut,
            EnvoiBillets.Statut.EN_ATTENTE,
        )
        billet = Billet.objects.get()
        self.assertEqual(billet.reservation_place.reservation_id, self.reservation.pk)
        self.assertEqual(billet.statut, Billet.Statut.VALIDE)
        billet_initial = (billet.pk, billet.reference, billet.date_emission)
        traiter_session_checkout("checkout.session.completed", self.session_stripe("complete").to_dict())
        billet = Billet.objects.get()
        self.assertEqual((billet.pk, billet.reference, billet.date_emission), billet_initial)

    def test_retour_abandon_permet_reprise_sans_prolonger(self):
        self.client.post(self.url)
        expiration = Paiement.objects.get().date_expiration
        page = self.client.get(self.detail + "?retour=annulation")
        self.assertContains(page, "Le paiement n’a pas été finalisé")
        self.assertContains(page, "Reprendre le paiement")
        self.assertEqual(Paiement.objects.get().date_expiration, expiration)

    def test_echeance_verifie_expiration_stripe_et_libere_places(self):
        self.client.post(self.url)
        expiration = Paiement.objects.get().date_expiration
        self.lecture.side_effect = lambda identifiant: self.session_stripe("expired")
        with patch("reservations.views.timezone.now", return_value=expiration):
            page = self.client.get(self.detail)
        self.assertContains(page, "Le délai est écoulé")
        self.assertEqual(Paiement.objects.get().statut, Paiement.Statut.ECHOUE)
        self.assertEqual(compter_places_disponibles(self.seance), 1)

    def test_panne_verification_conserve_places(self):
        self.client.post(self.url)
        expiration = Paiement.objects.get().date_expiration
        self.lecture.side_effect = APIConnectionError("Interruption")
        with patch("reservations.views.timezone.now", return_value=expiration):
            page = self.client.get(self.detail)
            self.assertEqual(compter_places_disponibles(self.seance), 0)
        self.assertContains(page, "Vérification du paiement en cours")
        self.assertEqual(Paiement.objects.get().statut, Paiement.Statut.EN_ATTENTE)

    def test_echeance_refusee_par_stripe_ne_laisse_pas_tentative_bloquee(self):
        self.creation.side_effect = InvalidRequestError("Echeance trop proche", param="expires_at")
        self.client.post(self.url)
        self.assertEqual(Paiement.objects.get().statut, Paiement.Statut.ECHOUE)


    def test_webhook_retrouve_session_dont_la_reponse_reseau_a_ete_perdue(self):
        self.creation.side_effect = APIConnectionError("Réponse perdue")
        self.client.post(self.url)
        session = self.session_stripe("expired").to_dict()
        session["metadata"] = self.creation.call_args.args[0]["metadata"]
        traiter_session_checkout("checkout.session.expired", session)
        paiement = Paiement.objects.get()
        self.assertEqual(paiement.stripe_session_id, "cs_test_parcours")
        self.assertEqual(paiement.statut, Paiement.Statut.ECHOUE)
        self.assertEqual(compter_places_disponibles(self.seance), 1)

    def test_reference_inconnue_ne_rattache_pas_session_a_tentative(self):
        self.creation.side_effect = APIConnectionError("Réponse perdue")
        self.client.post(self.url)
        session = self.session_stripe("complete").to_dict()
        session["metadata"]["reference_paiement"] = "autre-tentative"
        traiter_session_checkout("checkout.session.completed", session)
        paiement = Paiement.objects.get()
        self.assertIsNone(paiement.stripe_session_id)
        self.assertEqual(paiement.statut, Paiement.Statut.EN_ATTENTE)


    def test_refus_stripe_distinct_erreur_reseau_et_log_sans_secret(self):
        self.creation.side_effect = InvalidRequestError(
            "Ne pas afficher sk_test_secret_factice", param="allowed_payment_method_types",
        )
        with self.assertLogs("paiements.views", level="WARNING") as journal:
            page = self.client.post(self.url, follow=True)
        self.assertContains(page, "Stripe n’a pas accepté")
        self.assertNotContains(page, "Impossible de joindre Stripe")
        logs = " ".join(journal.output)
        self.assertIn("InvalidRequestError", logs)
        self.assertIn("allowed_payment_method_types", logs)
        self.assertNotIn("sk_test_", logs)

    def test_erreur_reseau_conserve_message_de_reprise(self):
        self.creation.side_effect = APIConnectionError("Connexion interrompue")
        with self.assertLogs("paiements.views", level="WARNING"):
            page = self.client.post(self.url, follow=True)
        self.assertContains(page, "Impossible de joindre Stripe")
        self.assertEqual(Paiement.objects.get().statut, Paiement.Statut.EN_ATTENTE)

    def test_annulation_pendant_creation_ferme_session_stripe(self):
        def annuler(parametres, options):
            Reservation.objects.filter(pk=self.reservation.pk).update(statut=Reservation.Statut.ANNULEE)
            return self.session_stripe()
        self.creation.side_effect = annuler
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.stripe.v1.checkout.sessions.expire.assert_called_once_with("cs_test_parcours")


@override_settings(STRIPE_SECRET_KEY="sk_test_exemple", STRIPE_WEBHOOK_SECRET="whsec_exemple")
class AppelStripeHorsTransactionTests(TransactionTestCase):
    def test_tentative_persistee_et_aucune_transaction_ouverte_pendant_appel(self):
        film = Film.objects.create(titre="Film", duree=90)
        salle = Salle.objects.create(nom="Salle", capacite=1)
        debut = timezone.now() + timedelta(days=1)
        seance = Seance.objects.create(film=film, salle=salle, debut=debut, fin=debut + timedelta(minutes=90), prix=10)
        reservation = Reservation.objects.create(
            seance=seance, email="spectateur@example.com", montant_total=10,
            date_expiration=timezone.now() + timedelta(minutes=30),
        )
        def demander(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(Paiement.objects.count(), 1)
            session = Mock()
            session.to_dict.return_value = {"id": "cs_test_sans_verrou", "status": "open", "url": "https://checkout.stripe.com/test"}
            return session
        with patch("paiements.services.creer_session_checkout", side_effect=demander):
            self.assertEqual(demarrer_paiement(reservation.pk, "http://testserver/reservations/1/"), "https://checkout.stripe.com/test")
