from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.core import signing
from django.test import Client, TestCase
from django.urls import resolve, reverse
from django.utils import timezone
from django.utils.formats import date_format
from pypdf import PdfReader

from cinema.models import Film, Place, Salle, Seance
from cinema.services import annuler_seance
from paiements.models import Paiement
from reservations.models import Billet, Reservation, ReservationPlace
from reservations.services import SALT_LIEN_BILLETS, creer_url_billets, generer_billets


class PdfBilletsTests(TestCase):
    def setUp(self):
        salle = Salle.objects.create(nom="Événementielle & modulable", capacite=2)
        film = Film.objects.create(titre="L’Été & l’Œuvre <édition spéciale>", duree=90)
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
        self.billets = generer_billets(self.reservation)
        self.consultation = creer_url_billets(self.reservation)
        self.jeton = resolve(self.consultation).kwargs["jeton"]
        self.url = self.url_pdf(self.jeton)

    def url_pdf(self, jeton):
        return reverse("reservations:telecharger_billets", kwargs={"jeton": jeton})

    def lire_pdf(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return response, PdfReader(BytesIO(response.content), strict=True)

    def test_bouton_telechargement_sur_page_billets(self):
        page = Client().get(self.consultation)

        self.assertContains(page, "Télécharger mes billets (PDF)")
        self.assertContains(page, f'href="{self.url}"')

    def test_pdf_sans_compte_avec_un_billet_et_sa_reference_par_page(self):
        response, lecteur = self.lire_pdf()

        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(
            response["Content-Disposition"],
            f'attachment; filename="billets-reservation-{self.reservation.pk}.pdf"',
        )
        self.assertEqual(len(lecteur.pages), 2)
        debut = timezone.localtime(self.seance.debut)
        for page, billet in zip(lecteur.pages, self.billets, strict=True):
            texte = " ".join(page.extract_text().split())
            self.assertIn(self.seance.film.titre, texte)
            self.assertIn(self.seance.salle.nom, texte)
            self.assertIn(date_format(debut, "l d F Y"), texte)
            self.assertIn(date_format(debut, "H:i"), texte)
            self.assertIn(f"Place A{billet.reservation_place.place.numero}", texte)
            self.assertIn(str(billet.reference), texte)
            self.assertNotIn(self.reservation.email, texte)
            self.assertNotIn(self.jeton, texte)
            for autre in self.billets:
                if autre.pk != billet.pk:
                    self.assertNotIn(str(autre.reference), texte)

    def test_retelechargement_ne_change_pas_billets(self):
        billets_initiaux = list(Billet.objects.order_by("pk").values())

        for _ in range(2):
            _, lecteur = self.lire_pdf()
            self.assertEqual(len(lecteur.pages), 2)

        self.assertEqual(list(Billet.objects.order_by("pk").values()), billets_initiaux)

    def test_lien_modifie_ou_sans_signature_refuse(self):
        identifiant, signature = self.jeton.rsplit(":", 1)
        for jeton in (
            identifiant,
            str(self.reservation.pk + 1) + ":" + signature,
            signing.Signer(salt="autre.usage").sign(self.reservation.pk),
            signing.Signer(salt=SALT_LIEN_BILLETS).sign(2**63),
        ):
            with self.subTest(jeton=jeton):
                self.assertEqual(self.client.get(self.url_pdf(jeton)).status_code, 404)

    def test_reservation_non_confirmee_refusee(self):
        for statut in (
            Reservation.Statut.EN_ATTENTE, Reservation.Statut.EXPIREE,
            Reservation.Statut.ANNULEE,
        ):
            with self.subTest(statut=statut):
                Reservation.objects.filter(pk=self.reservation.pk).update(statut=statut)
                self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_paiement_non_reussi_ou_absent_refuse(self):
        for statut in (
            Paiement.Statut.EN_ATTENTE, Paiement.Statut.ECHOUE,
            Paiement.Statut.REMBOURSE,
        ):
            with self.subTest(statut=statut):
                Paiement.objects.filter(pk=self.paiement.pk).update(statut=statut)
                self.assertEqual(self.client.get(self.url).status_code, 404)

        self.paiement.delete()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_seance_annulee_refusee(self):
        Seance.objects.filter(pk=self.seance.pk).update(statut=Seance.Statut.ANNULEE)

        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_billet_annule_exclu_du_pdf(self):
        billet = self.billets[0]
        Billet.objects.filter(pk=billet.pk).update(statut=Billet.Statut.ANNULE)

        _, lecteur = self.lire_pdf()

        self.assertEqual(len(lecteur.pages), 1)
        texte = lecteur.pages[0].extract_text()
        self.assertNotIn(str(billet.reference), texte)
        self.assertIn(str(self.billets[1].reference), texte)

    def test_aucun_billet_valide_refuse_telechargement(self):
        Billet.objects.update(statut=Billet.Statut.ANNULE)

        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_telechargement_ne_genere_pas_billets_manquants(self):
        Billet.objects.all().delete()

        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertFalse(Billet.objects.exists())

    def test_annulation_empeche_nouveau_telechargement(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

        annuler_seance(self.seance)

        self.assertEqual(Client().get(self.url).status_code, 404)

    def test_delai_provisoire_depasse_ne_bloque_pas_billets_payes(self):
        Reservation.objects.filter(pk=self.reservation.pk).update(
            date_expiration=timezone.now() - timedelta(minutes=1),
        )

        _, lecteur = self.lire_pdf()

        self.assertEqual(len(lecteur.pages), 2)

    def test_huit_billets_avec_textes_longs_restent_sur_huit_pages(self):
        film = self.seance.film
        film.titre = ("Un très long voyage & une édition exceptionnelle " * 5)[:200]
        film.save(update_fields=["titre"])
        salle = self.seance.salle
        salle.nom = ("Salle événementielle et modulable " * 4)[:100]
        salle.capacite = 8
        salle.save(update_fields=["nom", "capacite"])
        for numero in range(3, 9):
            ReservationPlace.objects.create(
                reservation=self.reservation,
                place=Place.objects.create(salle=salle, rangee="A", numero=numero),
                prix_unitaire=Decimal("12.35"),
            )
        Reservation.objects.filter(pk=self.reservation.pk).update(montant_total=Decimal("98.80"))
        Paiement.objects.filter(pk=self.paiement.pk).update(montant=Decimal("98.80"))
        generer_billets(self.reservation)

        _, lecteur = self.lire_pdf()

        self.assertEqual(len(lecteur.pages), 8)
        billets = Billet.objects.order_by("reservation_place__place__numero")
        for page, billet in zip(lecteur.pages, billets, strict=True):
            texte = "".join(page.extract_text().split())
            self.assertIn("".join(film.titre.split()), texte)
            self.assertIn("".join(salle.nom.split()), texte)
            self.assertIn(str(billet.reference), texte)

    def test_pdf_prive_non_stocke_en_cache(self):
        response, _ = self.lire_pdf()

        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")

    def test_telechargement_autorise_get_head_et_refuse_post(self):
        self.assertEqual(self.client.head(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url).status_code, 405)
