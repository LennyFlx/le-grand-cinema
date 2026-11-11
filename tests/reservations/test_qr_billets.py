from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
from xml.etree import ElementTree

import pypdfium2 as pdfium
import zxingcpp
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cinema.models import Film, Place, Salle, Seance
from cinema.services import annuler_seance
from paiements.models import Paiement
from reservations.emails import envoyer_billets
from reservations.models import Billet, Reservation, ReservationPlace
from reservations.qr_codes import creer_url_controle_billet
from reservations.services import creer_url_billets, generer_billets


@override_settings(
    SITE_URL="https://cinema.example.com",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class QrBilletsTests(TestCase):
    def setUp(self):
        self.salle = Salle.objects.create(nom="Salle de contrôle", capacite=2)
        self.film = Film.objects.create(titre="Le Voyage du QR code", duree=90)
        debut = timezone.now() + timedelta(days=1)
        self.seance = Seance.objects.create(
            film=self.film, salle=self.salle, debut=debut,
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
                place=Place.objects.create(salle=self.salle, rangee="A", numero=numero),
                prix_unitaire=Decimal("12.35"),
            )
        self.paiement = Paiement.objects.create(
            reservation=self.reservation, montant=Decimal("24.70"), statut=Paiement.Statut.REUSSI,
        )
        self.billets = generer_billets(self.reservation)
        self.billet = self.billets[0]
        self.controle = reverse(
            "reservations:controler_billet", kwargs={"reference": self.billet.reference},
        )
        self.qr = reverse("reservations:qr_billet", kwargs={"reference": self.billet.reference})
        self.consultation = creer_url_billets(self.reservation)
        self.pdf = self.consultation + "pdf/"
        self.controleur = get_user_model().objects.create_user(username="controleur", is_staff=True)
        self.permission = Permission.objects.get(
            content_type__app_label="reservations", codename="view_billet",
        )
        self.controleur.user_permissions.add(self.permission)

    def connexion_personnel(self):
        self.client.force_login(self.controleur)

    def decoder_page(self, contenu, numero):
        with pdfium.PdfDocument(contenu) as document:
            page = document[numero]
            bitmap = page.render(scale=2)
            try:
                resultats = zxingcpp.read_barcodes(
                    bitmap.to_pil(), formats=zxingcpp.BarcodeFormat.QRCode,
                )
                return [resultat.text for resultat in resultats]
            finally:
                bitmap.close()
                page.close()

    def test_page_affiche_un_qr_distinct_par_billet(self):
        page = Client().get(self.consultation)

        self.assertEqual(page.status_code, 200)
        for billet in self.billets:
            chemin = reverse("reservations:qr_billet", kwargs={"reference": billet.reference})
            self.assertContains(page, f'src="{chemin}"', count=1)
        self.assertNotEqual(
            creer_url_controle_billet(self.billets[0]),
            creer_url_controle_billet(self.billets[1]),
        )

    def test_image_qr_valide_sans_session_et_sans_donnees_personnelles(self):
        page = Client().get(self.qr)

        self.assertEqual(page.status_code, 200)
        self.assertTrue(page["Content-Type"].startswith("image/svg+xml"))
        self.assertEqual(ElementTree.fromstring(page.content).tag, "{http://www.w3.org/2000/svg}svg")
        self.assertNotContains(page, self.reservation.email)
        self.assertNotContains(page, self.film.titre)
        self.assertIn("no-store", page["Cache-Control"])
        self.assertEqual(page["Referrer-Policy"], "no-referrer")

    def test_scan_sans_connexion_redirige_vers_login_personnel_avec_retour(self):
        page = self.client.get(self.controle)

        self.assertEqual(page.status_code, 302)
        redirection = urlsplit(page.url)
        self.assertEqual(redirection.path, reverse("admin:login"))
        self.assertEqual(parse_qs(redirection.query)["next"], [self.controle])
        self.assertNotContains(page, self.film.titre, status_code=302)

    def test_compte_non_personnel_redirige_meme_avec_permission(self):
        utilisateur = get_user_model().objects.create_user(username="spectateur")
        utilisateur.user_permissions.add(self.permission)
        self.client.force_login(utilisateur)

        self.assertEqual(self.client.get(self.controle).status_code, 302)

    def test_personnel_sans_permission_ne_peut_pas_controler(self):
        utilisateur = get_user_model().objects.create_user(username="autre-personnel", is_staff=True)
        self.client.force_login(utilisateur)

        self.assertEqual(self.client.get(self.controle).status_code, 403)

    def test_personnel_inactif_refuse(self):
        get_user_model().objects.filter(pk=self.controleur.pk).update(is_active=False)
        self.client.force_login(self.controleur)

        self.assertEqual(self.client.get(self.controle).status_code, 302)

    def test_personnel_autorise_voit_validite_et_informations_du_billet_seul(self):
        self.connexion_personnel()

        page = self.client.get(self.controle)

        self.assertContains(page, "Billet valide")
        self.assertContains(page, self.film.titre)
        self.assertContains(page, self.salle.nom)
        self.assertContains(page, "Place A1")
        self.assertNotContains(page, "Place A2")
        self.assertNotContains(page, self.reservation.email)
        self.assertIn("no-store", page["Cache-Control"])
        self.assertEqual(page["X-Robots-Tag"], "noindex, nofollow")

    def test_superutilisateur_peut_controler(self):
        utilisateur = get_user_model().objects.create_user(
            username="admin-qr", is_staff=True, is_superuser=True,
        )
        self.client.force_login(utilisateur)

        self.assertContains(self.client.get(self.controle), "Billet valide")

    def test_reservation_non_confirmee_rend_controle_invalide_et_masque_image(self):
        self.connexion_personnel()
        for statut in (
            Reservation.Statut.EN_ATTENTE, Reservation.Statut.EXPIREE,
            Reservation.Statut.ANNULEE,
        ):
            with self.subTest(statut=statut):
                Reservation.objects.filter(pk=self.reservation.pk).update(statut=statut)
                self.assertContains(self.client.get(self.controle), "Billet invalide")
                self.assertEqual(Client().get(self.qr).status_code, 404)

    def test_paiement_non_reussi_ou_absent_rend_billet_invalide(self):
        self.connexion_personnel()
        for statut in (
            Paiement.Statut.EN_ATTENTE, Paiement.Statut.ECHOUE, Paiement.Statut.REMBOURSE,
        ):
            with self.subTest(statut=statut):
                Paiement.objects.filter(pk=self.paiement.pk).update(statut=statut)
                self.assertContains(self.client.get(self.controle), "Aucun paiement confirmé")
                self.assertEqual(Client().get(self.qr).status_code, 404)
        self.paiement.delete()
        self.assertContains(self.client.get(self.controle), "Billet invalide")

    def test_billet_annule_reste_controlable_avec_resultat_invalide(self):
        self.connexion_personnel()
        Billet.objects.filter(pk=self.billet.pk).update(statut=Billet.Statut.ANNULE)

        self.assertContains(self.client.get(self.controle), "Ce billet a été annulé")
        self.assertEqual(Client().get(self.qr).status_code, 404)

    def test_seance_annulee_refuse_billet(self):
        self.connexion_personnel()
        Seance.objects.filter(pk=self.seance.pk).update(statut=Seance.Statut.ANNULEE)

        self.assertContains(self.client.get(self.controle), "Cette séance a été annulée")
        self.assertEqual(Client().get(self.qr).status_code, 404)

    def test_fin_seance_et_statut_terminee_rendent_billet_invalide(self):
        self.connexion_personnel()
        with patch("reservations.services.timezone.now", return_value=self.seance.fin):
            self.assertContains(self.client.get(self.controle), "Cette séance est terminée")
        Seance.objects.filter(pk=self.seance.pk).update(statut=Seance.Statut.TERMINEE)
        self.assertContains(self.client.get(self.controle), "Billet invalide")

    def test_scan_repete_ne_modifie_pas_billet(self):
        self.connexion_personnel()
        billets = list(Billet.objects.order_by("pk").values())

        for _ in range(2):
            self.assertContains(self.client.get(self.controle), "Billet valide")

        self.assertEqual(list(Billet.objects.order_by("pk").values()), billets)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.CONFIRMEE)

    def test_reference_inconnue_affiche_resultat_invalide(self):
        self.connexion_personnel()
        chemin = reverse("reservations:controler_billet", kwargs={"reference": uuid4()})

        self.assertContains(self.client.get(chemin), "Ce billet est inconnu", status_code=404)

    def test_controle_et_image_acceptent_get_head_et_refusent_post(self):
        self.connexion_personnel()
        for chemin in (self.controle, self.qr):
            with self.subTest(chemin=chemin):
                self.assertEqual(self.client.head(chemin).status_code, 200)
                self.assertEqual(self.client.post(chemin).status_code, 405)

    def test_pdf_encode_une_url_de_controle_correcte_par_billet(self):
        contenu = Client().get(self.pdf).content

        for numero, billet in enumerate(self.billets):
            self.assertEqual(
                self.decoder_page(contenu, numero),
                [creer_url_controle_billet(billet)],
            )

    def test_qr_du_pdf_envoye_par_email_est_lisible(self):
        self.assertTrue(envoyer_billets(self.reservation.pk))
        piece = mail.outbox[0].attachments[0]

        self.assertEqual(
            self.decoder_page(piece.content, 0), [creer_url_controle_billet(self.billet)],
        )

    def test_pdf_avec_textes_longs_conserve_qr_lisibles_et_huit_pages(self):
        self.film.titre = ("Un voyage exceptionnel au bout du monde " * 6)[:200]
        self.film.save(update_fields=["titre"])
        self.salle.nom = ("Événementielle et modulable " * 5)[:100]
        self.salle.capacite = 8
        self.salle.save(update_fields=["nom", "capacite"])
        for numero in range(3, 9):
            ReservationPlace.objects.create(
                reservation=self.reservation,
                place=Place.objects.create(salle=self.salle, rangee="A", numero=numero),
                prix_unitaire=Decimal("12.35"),
            )
        Reservation.objects.filter(pk=self.reservation.pk).update(montant_total=Decimal("98.80"))
        Paiement.objects.filter(pk=self.paiement.pk).update(montant=Decimal("98.80"))
        billets = generer_billets(self.reservation)

        contenu = Client().get(self.pdf).content

        with pdfium.PdfDocument(contenu) as document:
            self.assertEqual(len(document), 8)
        for numero in (0, 7):
            self.assertEqual(
                self.decoder_page(contenu, numero),
                [creer_url_controle_billet(billets[numero])],
            )

    def test_qr_deja_imprime_signale_annulation_lors_du_scan(self):
        contenu = Client().get(self.pdf).content
        url_controle = self.decoder_page(contenu, 0)[0]
        annuler_seance(self.seance)
        self.connexion_personnel()

        self.assertContains(self.client.get(urlsplit(url_controle).path), "Billet invalide")
