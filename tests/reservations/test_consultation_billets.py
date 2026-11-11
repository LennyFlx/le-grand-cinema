from datetime import timedelta
from decimal import Decimal

from django.core import signing
from django.test import Client, TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from cinema.models import Film, Place, Salle, Seance
from cinema.services import annuler_seance
from paiements.models import Paiement
from reservations.models import Billet, Reservation, ReservationPlace
from reservations.services import SALT_LIEN_BILLETS, creer_url_billets, generer_billets


class ConsultationBilletsTests(TestCase):
    def setUp(self):
        salle = Salle.objects.create(nom="Salle des billets", capacite=2)
        film = Film.objects.create(titre="Le Voyage des billets", duree=90)
        debut = timezone.now() + timedelta(days=1)
        self.seance = Seance.objects.create(
            film=film, salle=salle, debut=debut,
            fin=debut + timedelta(minutes=90), prix=Decimal("12.35"),
        )
        self.reservation = Reservation.objects.create(
            seance=self.seance, email="spectateur@example.com",
            statut=Reservation.Statut.CONFIRMEE,
            date_expiration=timezone.now() + timedelta(minutes=31),
            montant_total=Decimal("24.70"),
        )
        for numero in (1, 2):
            ReservationPlace.objects.create(
                reservation=self.reservation,
                place=Place.objects.create(salle=salle, rangee="A", numero=numero),
                prix_unitaire=Decimal("12.35"),
            )
        self.paiement = Paiement.objects.create(
            reservation=self.reservation, montant=Decimal("24.70"),
            statut=Paiement.Statut.REUSSI,
        )
        generer_billets(self.reservation)
        self.url = creer_url_billets(self.reservation)
        self.jeton = resolve(self.url).kwargs["jeton"]
        self.detail = reverse("reservations:detail_reservation", args=[self.reservation.pk])

    def url_jeton(self, jeton):
        return reverse("reservations:consulter_billets", kwargs={"jeton": jeton})

    def ouvrir_session_reservation(self):
        session = self.client.session
        session["reservation_provisoire_id"] = self.reservation.pk
        session.save()

    def test_lien_permet_consultation_sans_compte_ni_session(self):
        navigateur = Client(enforce_csrf_checks=True)

        page = navigateur.get(self.url)

        self.assertEqual(page.status_code, 200)
        self.assertTemplateUsed(page, "reservations/billets.html")
        self.assertEqual(len(page.context["billets"]), 2)
        self.assertContains(page, self.seance.film.titre, count=2)
        self.assertContains(page, self.seance.salle.nom, count=2)
        self.assertContains(page, "à " + timezone.localtime(self.seance.debut).strftime("%H:%M"), count=2)
        self.assertContains(page, "Place A1")
        self.assertContains(page, "Place A2")
        for billet in Billet.objects.all():
            self.assertContains(page, str(billet.reference))
        self.assertNotContains(page, self.reservation.email)
        self.assertNotIn("reservation_provisoire_id", navigateur.session)

    def test_confirmation_propose_lien_utilisable_dans_autre_navigateur(self):
        self.ouvrir_session_reservation()

        page = self.client.get(self.detail)

        self.assertContains(page, "Voir mes billets")
        self.assertContains(page, f'href="{self.url}"')
        self.assertEqual(Client().get(self.detail).status_code, 404)
        self.assertEqual(Client().get(self.url).status_code, 200)

    def test_consultation_repetee_conserve_billets_et_lien(self):
        billets_initiaux = list(Billet.objects.order_by("pk").values())

        for _ in range(2):
            self.assertEqual(Client().get(self.url).status_code, 200)

        self.assertEqual(list(Billet.objects.order_by("pk").values()), billets_initiaux)
        self.assertEqual(creer_url_billets(self.reservation), self.url)

    def test_signature_modifiee_et_identifiant_modifie_refuses(self):
        identifiant, signature = self.jeton.rsplit(":", 1)
        autre_fin = "x" if signature[-1] != "x" else "y"
        for jeton in (
            identifiant + ":" + signature[:-1] + autre_fin,
            str(self.reservation.pk + 1) + ":" + signature,
        ):
            with self.subTest(jeton=jeton):
                self.assertEqual(self.client.get(self.url_jeton(jeton)).status_code, 404)

    def test_identifiant_seul_et_signature_autre_usage_refuses(self):
        for jeton in (
            str(self.reservation.pk),
            "lien-invalide",
            signing.Signer().sign(self.reservation.pk),
            signing.Signer(salt="autre.usage").sign(self.reservation.pk),
        ):
            with self.subTest(jeton=jeton):
                self.assertEqual(self.client.get(self.url_jeton(jeton)).status_code, 404)

    def test_identifiants_invalides_meme_signes_refuses_sans_erreur_serveur(self):
        for identifiant in ("0", "-1", str(2**63), "pas-un-nombre"):
            with self.subTest(identifiant=identifiant):
                jeton = signing.Signer(salt=SALT_LIEN_BILLETS).sign(identifiant)
                self.assertEqual(self.client.get(self.url_jeton(jeton)).status_code, 404)

    def test_reservation_inexistante_refusee(self):
        jeton = signing.Signer(salt=SALT_LIEN_BILLETS).sign(2**62)

        self.assertEqual(self.client.get(self.url_jeton(jeton)).status_code, 404)

    def test_reservation_non_confirmee_refusee_et_bouton_absent(self):
        self.ouvrir_session_reservation()
        for statut in (
            Reservation.Statut.EN_ATTENTE, Reservation.Statut.EXPIREE,
            Reservation.Statut.ANNULEE,
        ):
            with self.subTest(statut=statut):
                Reservation.objects.filter(pk=self.reservation.pk).update(statut=statut)
                self.assertEqual(self.client.get(self.url).status_code, 404)
                self.assertNotContains(self.client.get(self.detail), "Voir mes billets")

    def test_paiement_non_reussi_ou_absent_refuse_et_bouton_absent(self):
        self.ouvrir_session_reservation()
        for statut in (
            Paiement.Statut.EN_ATTENTE, Paiement.Statut.ECHOUE,
            Paiement.Statut.REMBOURSE,
        ):
            with self.subTest(statut=statut):
                Paiement.objects.filter(pk=self.paiement.pk).update(statut=statut)
                self.assertEqual(self.client.get(self.url).status_code, 404)
                self.assertNotContains(self.client.get(self.detail), "Voir mes billets")

        self.paiement.delete()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertNotContains(self.client.get(self.detail), "Voir mes billets")

    def test_seance_annulee_refusee_et_bouton_absent(self):
        self.ouvrir_session_reservation()
        Seance.objects.filter(pk=self.seance.pk).update(statut=Seance.Statut.ANNULEE)

        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertNotContains(self.client.get(self.detail), "Voir mes billets")

    def test_billet_annule_non_affiche_avec_autres_billets_valides(self):
        billet = Billet.objects.order_by("pk").first()
        Billet.objects.filter(pk=billet.pk).update(statut=Billet.Statut.ANNULE)

        page = self.client.get(self.url)

        self.assertEqual(page.status_code, 200)
        self.assertEqual(len(page.context["billets"]), 1)
        self.assertNotContains(page, str(billet.reference))
        self.assertContains(page, "Place A2")

    def test_aucun_billet_valide_refuse_consultation_et_masque_bouton(self):
        self.ouvrir_session_reservation()
        Billet.objects.update(statut=Billet.Statut.ANNULE)

        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertNotContains(self.client.get(self.detail), "Voir mes billets")

    def test_consultation_ne_genere_pas_billets_manquants(self):
        self.ouvrir_session_reservation()
        Billet.objects.all().delete()

        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertNotContains(self.client.get(self.detail), "Voir mes billets")
        self.assertFalse(Billet.objects.exists())

    def test_lien_devient_inutilisable_apres_annulation(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

        annuler_seance(self.seance)

        self.assertEqual(Client().get(self.url).status_code, 404)
        self.assertEqual(Billet.objects.filter(statut=Billet.Statut.ANNULE).count(), 2)

    def test_plusieurs_paiements_reussis_ne_dupliquent_pas_affichage(self):
        Paiement.objects.create(
            reservation=self.reservation, montant=Decimal("24.70"),
            statut=Paiement.Statut.REUSSI,
        )

        page = self.client.get(self.url)

        self.assertEqual(page.status_code, 200)
        self.assertEqual(len(page.context["billets"]), 2)
        self.assertContains(page, "Place A1", count=1)

    def test_session_autre_reservation_ne_change_pas_billets_du_lien(self):
        autre = Reservation.objects.create(
            seance=self.seance, email="autre@example.com",
            date_expiration=timezone.now() + timedelta(minutes=30), montant_total=0,
        )
        session = self.client.session
        session["reservation_provisoire_id"] = autre.pk
        session.save()

        page = self.client.get(self.url)

        self.assertEqual(page.context["reservation"].pk, self.reservation.pk)
        self.assertEqual(len(page.context["billets"]), 2)
        self.assertEqual(self.client.get(self.detail).status_code, 404)

    def test_page_privee_non_indexable_sans_transmission_du_lien(self):
        page = self.client.get(self.url)

        self.assertIn("no-store", page["Cache-Control"])
        self.assertIn("private", page["Cache-Control"])
        self.assertEqual(page["Referrer-Policy"], "no-referrer")
        self.assertEqual(page["X-Robots-Tag"], "noindex, nofollow")

    def test_consultation_autorise_get_head_et_refuse_post(self):
        self.assertEqual(self.client.head(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url).status_code, 405)
