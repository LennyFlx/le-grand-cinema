from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.contrib import admin, messages
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from cinema.models import Film, Place, Salle, Seance
from paiements.models import Paiement
from paiements.services import traiter_session_checkout

from reservations.models import Billet, EnvoiBillets, Reservation, ReservationPlace
from reservations.services import (
    annuler_reservation, compter_places_disponibles, creer_reservation_provisoire,
    creer_url_billets, generer_billets, verifier_validite_billet,
)


class DonneesAnnulationReservation:
    def setUp(self):
        super().setUp()
        self.film = Film.objects.create(titre="Le Voyage", duree=90)
        self.salle = Salle.objects.create(nom="Salle annulation", capacite=3)
        debut = timezone.now() + timedelta(days=2)
        self.seance = Seance.objects.create(
            film=self.film, salle=self.salle, debut=debut,
            fin=debut + timedelta(minutes=90), prix=Decimal("12.35"),
        )
        self.places = [
            Place.objects.create(salle=self.salle, rangee="A", numero=numero, active=numero != 3)
            for numero in (1, 2, 3)
        ]
        self.reservation = creer_reservation_provisoire(
            self.seance, [self.places[0].pk], "spectateur@example.com",
        )

    def confirmer(self):
        self.paiement = Paiement.objects.create(
            reservation=self.reservation, montant=self.reservation.montant_total,
            stripe_session_id="cs_test_annulation", statut=Paiement.Statut.REUSSI,
        )
        Reservation.objects.filter(pk=self.reservation.pk).update(statut=Reservation.Statut.CONFIRMEE)
        self.reservation.refresh_from_db()
        self.billet = generer_billets(self.reservation)[0]
        EnvoiBillets.objects.create(
            reservation=self.reservation, statut=EnvoiBillets.Statut.ENVOYE,
        )

    def session_checkout(self, paiement):
        return {
            "id": paiement.stripe_session_id, "mode": "payment",
            "status": "complete", "payment_status": "paid", "currency": "eur",
            "amount_total": int(paiement.montant * 100),
            "client_reference_id": str(self.reservation.pk),
            "metadata": {"reservation_id": str(self.reservation.pk)},
            "payment_intent": "pi_test_annulation",
        }


class AnnulationReservationTests(DonneesAnnulationReservation, TestCase):
    def test_annulation_provisoire_conserve_donnees_et_libere_la_place(self):
        initiale = Reservation.objects.values().get(pk=self.reservation.pk)
        associations = list(ReservationPlace.objects.values())
        self.assertEqual(compter_places_disponibles(self.seance), 1)

        self.assertEqual(annuler_reservation(self.reservation), 0)

        actuelle = Reservation.objects.values().get(pk=self.reservation.pk)
        self.assertEqual(actuelle, {**initiale, "statut": Reservation.Statut.ANNULEE})
        self.assertEqual(list(ReservationPlace.objects.values()), associations)
        self.assertEqual(compter_places_disponibles(self.seance), 2)
        nouvelle = creer_reservation_provisoire(
            self.seance, [self.places[0].pk], "suivant@example.com",
        )
        self.assertNotEqual(nouvelle.pk, self.reservation.pk)

    def test_annulation_payee_invalide_billets_sans_modifier_paiement_ni_email(self):
        self.confirmer()
        paiement = Paiement.objects.values().get(pk=self.paiement.pk)
        envoi = EnvoiBillets.objects.values().get(reservation=self.reservation)
        reference = str(self.billet.reference)

        self.assertEqual(annuler_reservation(self.reservation), 1)

        self.reservation.refresh_from_db()
        self.billet.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.ANNULEE)
        self.assertEqual(self.billet.statut, Billet.Statut.ANNULE)
        self.assertEqual(self.billet.reference, reference)
        self.assertFalse(verifier_validite_billet(self.billet)[0])
        self.assertEqual(Paiement.objects.values().get(pk=self.paiement.pk), paiement)
        self.assertEqual(EnvoiBillets.objects.values().get(reservation=self.reservation), envoi)
        self.assertEqual(compter_places_disponibles(self.seance), 2)

    def test_deuxieme_annulation_refusee_meme_avec_instance_ancienne(self):
        annuler_reservation(self.reservation)
        with self.assertRaisesMessage(ValidationError, "déjà annulée"):
            annuler_reservation(self.reservation)
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(ReservationPlace.objects.count(), 1)

    def test_paiement_en_attente_interdit_annulation_meme_apres_echeance(self):
        paiement = Paiement.objects.create(
            reservation=self.reservation, montant=self.reservation.montant_total,
            stripe_session_id="cs_test_attente", date_expiration=timezone.now() - timedelta(minutes=1),
        )
        Reservation.objects.filter(pk=self.reservation.pk).update(
            date_expiration=timezone.now() - timedelta(minutes=1),
        )
        with self.assertRaisesMessage(ValidationError, "paiement est encore en attente"):
            annuler_reservation(self.reservation)
        self.reservation.refresh_from_db()
        paiement.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.EN_ATTENTE)
        self.assertEqual(paiement.statut, Paiement.Statut.EN_ATTENTE)
        self.assertEqual(compter_places_disponibles(self.seance), 1)

    def test_une_autre_tentative_en_attente_empeche_aussi_annulation_payee(self):
        self.confirmer()
        Paiement.objects.create(reservation=self.reservation, montant=self.reservation.montant_total)
        with self.assertRaises(ValidationError):
            annuler_reservation(self.reservation)
        self.billet.refresh_from_db()
        self.assertEqual(self.billet.statut, Billet.Statut.VALIDE)

    def test_reservation_expiree_et_tentatives_cloturees_peuvent_etre_annulees(self):
        Reservation.objects.filter(pk=self.reservation.pk).update(statut=Reservation.Statut.EXPIREE)
        for statut in (Paiement.Statut.ECHOUE, Paiement.Statut.REMBOURSE):
            Paiement.objects.create(
                reservation=self.reservation, montant=self.reservation.montant_total, statut=statut,
            )
        self.assertEqual(annuler_reservation(self.reservation), 0)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.ANNULEE)

    def test_place_inactive_reste_hors_vente_et_autre_reservation_inchangee(self):
        autre = creer_reservation_provisoire(
            self.seance, [self.places[1].pk], "autre@example.com",
        )
        Place.objects.filter(pk=self.places[0].pk).update(active=False)
        annuler_reservation(self.reservation)
        autre.refresh_from_db()
        self.assertEqual(autre.statut, Reservation.Statut.EN_ATTENTE)
        self.assertEqual(compter_places_disponibles(self.seance), 0)
        with self.assertRaisesMessage(ValidationError, "ne sont plus disponibles"):
            creer_reservation_provisoire(self.seance, [self.places[0].pk], "suivant@example.com")

    def test_echec_invalidation_billets_annule_toute_operation(self):
        self.confirmer()
        with patch(
            "reservations.services.Billet.objects.filter", side_effect=RuntimeError("Échec simulé"),
        ):
            with self.assertRaises(RuntimeError):
                annuler_reservation(self.reservation)
        self.reservation.refresh_from_db()
        self.billet.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.CONFIRMEE)
        self.assertEqual(self.billet.statut, Billet.Statut.VALIDE)
        self.assertEqual(compter_places_disponibles(self.seance), 1)

    def test_billets_page_pdf_et_qr_inaccessibles_apres_annulation(self):
        self.confirmer()
        lien = creer_url_billets(self.reservation)
        annuler_reservation(self.reservation)
        for chemin in (
            lien, lien + "pdf/",
            reverse("reservations:qr_billet", args=[self.billet.reference]),
        ):
            with self.subTest(chemin=chemin):
                self.assertEqual(self.client.get(chemin).status_code, 404)
        with self.assertRaises(ValidationError):
            generer_billets(self.reservation)

    def test_confirmation_stripe_tardive_ne_reactive_jamais_reservation_annulee(self):
        paiement = Paiement.objects.create(
            reservation=self.reservation, montant=self.reservation.montant_total,
            stripe_session_id="cs_test_tardive", statut=Paiement.Statut.ECHOUE,
        )
        annuler_reservation(self.reservation)
        traiter_session_checkout("checkout.session.completed", self.session_checkout(paiement))
        self.reservation.refresh_from_db()
        paiement.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.ANNULEE)
        self.assertEqual(paiement.statut, Paiement.Statut.REUSSI)
        self.assertFalse(Billet.objects.exists())
        self.assertFalse(EnvoiBillets.objects.exists())

    def test_webhook_repete_apres_annulation_payee_ne_regenere_pas_billet(self):
        self.confirmer()
        annuler_reservation(self.reservation)
        traiter_session_checkout("checkout.session.completed", self.session_checkout(self.paiement))
        self.billet.refresh_from_db()
        self.assertEqual(Billet.objects.count(), 1)
        self.assertEqual(self.billet.statut, Billet.Statut.ANNULE)


class AnnulationReservationAdminTests(DonneesAnnulationReservation, TestCase):
    def setUp(self):
        super().setUp()
        self.personnel = get_user_model().objects.create_user(username="gestionnaire", is_staff=True)
        self.permissions = Permission.objects.filter(
            content_type__app_label="reservations",
            codename__in=("view_reservation", "change_reservation"),
        )
        self.personnel.user_permissions.add(*self.permissions)
        self.client.force_login(self.personnel)
        self.url = reverse("admin:reservations_reservation_annuler", args=[self.reservation.pk])
        self.detail = reverse("admin:reservations_reservation_change", args=[self.reservation.pk])

    def test_lien_depuis_detail_et_confirmation_get_sans_annulation(self):
        self.assertContains(self.client.get(self.detail), self.url)
        page = self.client.get(self.url, {"confirmer": "oui"})
        for texte in (
            "Le Voyage", "Salle annulation", "spectateur@example.com", "A1",
            "12,35", "Confirmer l’annulation", "Retour sans annuler",
            date_format(timezone.localtime(self.seance.debut), "DATETIME_FORMAT"),
        ):
            self.assertContains(page, texte)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.EN_ATTENTE)
        self.assertFalse(LogEntry.objects.exists())

    def test_post_exige_confirmation_explicite(self):
        for donnees in ({}, {"confirmer": "non"}):
            with self.subTest(donnees=donnees):
                reponse = self.client.post(self.url, donnees)
                self.assertRedirects(reponse, self.url, fetch_redirect_response=False)
                self.reservation.refresh_from_db()
                self.assertEqual(self.reservation.statut, Reservation.Statut.EN_ATTENTE)
        self.assertFalse(LogEntry.objects.exists())

    def test_post_confirme_annulation_et_journal_admin(self):
        page = self.client.post(self.url, {"confirmer": "oui"}, follow=True)
        self.assertContains(page, "a été annulée")
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.ANNULEE)
        self.assertNotContains(page, self.url)
        entree = LogEntry.objects.get()
        self.assertEqual(entree.user_id, self.personnel.pk)
        self.assertEqual(entree.object_id, str(self.reservation.pk))
        self.assertEqual(entree.content_type.app_label, "reservations")
        self.assertIn("Annulation", entree.change_message)
        self.assertEqual(compter_places_disponibles(self.seance), 2)

    def test_reservation_payee_avertit_avant_et_apres_annulation(self):
        self.confirmer()
        page = self.client.get(self.url)
        self.assertContains(page, "Un remboursement peut être nécessaire")
        reponse = self.client.post(self.url, {"confirmer": "oui"})
        notifications = list(get_messages(reponse.wsgi_request))
        self.assertTrue(any(
            message.level == messages.WARNING and "remboursement peut être nécessaire" in str(message)
            for message in notifications
        ))
        self.paiement.refresh_from_db()
        self.assertEqual(self.paiement.statut, Paiement.Statut.REUSSI)

    def test_double_annulation_signale_erreur_et_ne_journalise_pas_deux_fois(self):
        self.client.post(self.url, {"confirmer": "oui"})
        reponse = self.client.post(self.url, {"confirmer": "oui"}, follow=True)
        self.assertContains(reponse, "déjà annulée")
        self.assertEqual(LogEntry.objects.count(), 1)
        page = self.client.get(self.url)
        self.assertNotContains(page, 'name="confirmer"')

    def test_paiement_en_attente_affiche_blocage_et_refuse_post(self):
        Paiement.objects.create(reservation=self.reservation, montant=self.reservation.montant_total)
        page = self.client.get(self.url)
        self.assertContains(page, "Un paiement est encore en attente")
        self.assertNotContains(page, 'name="confirmer"')
        page = self.client.post(self.url, {"confirmer": "oui"}, follow=True)
        self.assertContains(page, "Les places restent protégées")
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.EN_ATTENTE)
        self.assertFalse(LogEntry.objects.exists())

    def test_paiement_demarre_apres_ouverture_confirmation_est_reverifie(self):
        self.assertContains(self.client.get(self.url), 'name="confirmer"')
        Paiement.objects.create(reservation=self.reservation, montant=self.reservation.montant_total)
        self.client.post(self.url, {"confirmer": "oui"})
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.EN_ATTENTE)
        self.assertFalse(LogEntry.objects.exists())

    def test_permissions_consultation_et_modification_toutes_deux_requises(self):
        for code in (None, "view_reservation", "change_reservation"):
            compte = get_user_model().objects.create_user(username=f"compte-{code}", is_staff=True)
            if code:
                compte.user_permissions.add(self.permissions.get(codename=code))
            self.client.force_login(compte)
            for methode in ("get", "post"):
                with self.subTest(code=code, methode=methode):
                    self.assertEqual(
                        getattr(self.client, methode)(self.url, {"confirmer": "oui"}).status_code, 403,
                    )
            if code == "view_reservation":
                self.assertNotContains(self.client.get(self.detail), self.url)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.EN_ATTENTE)

    def test_anonyme_non_personnel_et_compte_inactif_refuses(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.post(self.url, {"confirmer": "oui"}).status_code, 302)
        for actif, personnel in ((True, False), (False, True)):
            compte = get_user_model().objects.create_user(
                username=f"compte-{actif}-{personnel}", is_active=actif, is_staff=personnel,
            )
            compte.user_permissions.add(*self.permissions)
            self.client.force_login(compte)
            self.assertEqual(self.client.get(self.url).status_code, 302)
            self.assertEqual(self.client.post(self.url, {"confirmer": "oui"}).status_code, 302)

    def test_superutilisateur_peut_annuler(self):
        compte = get_user_model().objects.create_superuser(
            username="superadmin", email="super@example.com", password=None,
        )
        self.client.force_login(compte)
        self.assertRedirects(
            self.client.post(self.url, {"confirmer": "oui"}), self.detail,
            fetch_redirect_response=False,
        )
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.ANNULEE)

    def test_csrf_requis_et_confirmation_avec_jeton_valide(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.personnel)
        self.assertEqual(client.post(self.url, {"confirmer": "oui"}).status_code, 403)
        client.get(self.url)
        jeton = client.cookies["csrftoken"].value
        self.assertEqual(client.post(self.url, {
            "confirmer": "oui", "csrfmiddlewaretoken": jeton,
        }).status_code, 302)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.ANNULEE)

    def test_reservation_inconnue_et_methode_non_autorisee(self):
        inconnue = reverse("admin:reservations_reservation_annuler", args=[self.reservation.pk + 10000])
        self.assertEqual(self.client.get(inconnue).status_code, 404)
        self.assertEqual(self.client.post(inconnue, {"confirmer": "oui"}).status_code, 404)
        self.assertEqual(self.client.delete(self.url).status_code, 405)

    def test_echec_journalisation_annule_aussi_la_modification(self):
        modele_admin = admin.site._registry[Reservation]
        with patch.object(modele_admin, "log_change", side_effect=RuntimeError("Échec simulé")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, {"confirmer": "oui"})
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.EN_ATTENTE)
        self.assertEqual(compter_places_disponibles(self.seance), 1)


class AnnulationReservationConcurrenceTests(DonneesAnnulationReservation, TransactionTestCase):
    def executer_simultanement(self, premiere, seconde):
        rendez_vous = Barrier(2, timeout=10)

        def executer(operation):
            connections.close_all()
            try:
                with connections["default"].cursor() as curseur:
                    curseur.execute("SET statement_timeout = '10s'")
                rendez_vous.wait()
                try:
                    operation()
                except ValidationError:
                    return False
                return True
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executant:
            resultats = [executant.submit(executer, operation) for operation in (premiere, seconde)]
            return [resultat.result(timeout=20) for resultat in resultats]

    def test_deux_annulations_simultanees_une_seule_reussit(self):
        resultats = self.executer_simultanement(
            lambda: annuler_reservation(self.reservation),
            lambda: annuler_reservation(self.reservation),
        )
        self.assertEqual(sorted(resultats), [False, True])
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.ANNULEE)

    def test_annulation_et_nouvelle_reservation_ne_produisent_pas_double_attribution(self):
        self.executer_simultanement(
            lambda: annuler_reservation(self.reservation),
            lambda: creer_reservation_provisoire(
                self.seance, [self.places[0].pk], "suivant@example.com",
            ),
        )
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.ANNULEE)
        self.assertLessEqual(ReservationPlace.objects.filter(
            place=self.places[0], reservation__statut=Reservation.Statut.EN_ATTENTE,
        ).count(), 1)

    def test_confirmation_stripe_concurrente_et_annulation_restent_coherentes(self):
        paiement = Paiement.objects.create(
            reservation=self.reservation, montant=self.reservation.montant_total,
            stripe_session_id="cs_test_concurrence",
        )
        with patch("paiements.services.programmer_envoi_billets"):
            self.executer_simultanement(
                lambda: annuler_reservation(self.reservation),
                lambda: traiter_session_checkout(
                    "checkout.session.completed", self.session_checkout(paiement),
                ),
            )
        self.reservation.refresh_from_db()
        paiement.refresh_from_db()
        self.assertEqual(paiement.statut, Paiement.Statut.REUSSI)
        billet = Billet.objects.get()
        if self.reservation.statut == Reservation.Statut.ANNULEE:
            self.assertEqual(billet.statut, Billet.Statut.ANNULE)
            self.assertEqual(compter_places_disponibles(self.seance), 2)
        else:
            self.assertEqual(self.reservation.statut, Reservation.Statut.CONFIRMEE)
            self.assertEqual(billet.statut, Billet.Statut.VALIDE)
            self.assertEqual(compter_places_disponibles(self.seance), 1)
