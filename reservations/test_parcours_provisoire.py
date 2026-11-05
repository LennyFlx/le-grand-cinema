from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from cinema.models import Film, Place, Salle, Seance
from .models import Reservation, ReservationPlace
from .services import compter_places_disponibles


class ParcoursReservationProvisoireTests(TestCase):
    def setUp(self):
        self.maintenant = timezone.now()
        horloge = patch("reservations.views.timezone.now", return_value=self.maintenant)
        horloge.start()
        self.addCleanup(horloge.stop)
        salle = Salle.objects.create(nom="Salle", capacite=2)
        film = Film.objects.create(titre="Film", duree=90)
        self.seance = Seance.objects.create(
            film=film, salle=salle, debut=self.maintenant + timedelta(days=1),
            fin=self.maintenant + timedelta(days=1, minutes=90), prix=Decimal("12.35"),
        )
        self.place = Place.objects.create(salle=salle, rangee="A", numero=1)
        self.autre_place = Place.objects.create(salle=salle, rangee="A", numero=2)
        self.url = reverse("reservations:reserver_places", args=[self.seance.pk])
        self.donnees = {"places": [self.place.pk], "email": "spectateur@example.com"}

    def test_selection_affiche_formulaire_sans_bloquer(self):
        response = self.client.post(reverse("reservations:valider_selection", args=[self.seance.pk]), {"places": [self.place.pk]})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="email"')
        self.assertContains(response, "Bloquer mes places")
        self.assertFalse(Reservation.objects.exists())

    def test_blocage_redirection_page_privee_et_actualisation_sans_prolongation(self):
        response = self.client.post(self.url, self.donnees)
        reservation = Reservation.objects.get()
        self.assertRedirects(response, reverse("reservations:detail_reservation", args=[reservation.pk]))
        self.assertEqual(self.client.session["reservation_provisoire_id"], reservation.pk)
        self.assertEqual(reservation.montant_total, Decimal("12.35"))
        self.assertEqual(compter_places_disponibles(self.seance), 1)
        expiration = reservation.date_expiration
        for _ in range(2):
            page = self.client.get(response.url)
            self.assertContains(page, "Vos places sont temporairement réservées")
            self.assertEqual(page.context["secondes_restantes"], 1800)
            self.assertIn("no-store", page["Cache-Control"])
        reservation.refresh_from_db()
        self.assertEqual(reservation.date_expiration, expiration)
        self.assertEqual(Client().get(response.url).status_code, 404)
        self.assertEqual(self.client.get(reverse("reservations:detail_reservation", args=[reservation.pk + 1])).status_code, 404)

    def test_second_envoi_reaffiche_reservation_sans_bloquer_d_autres_places(self):
        premier = self.client.post(self.url, self.donnees)
        second = self.client.post(self.url, {**self.donnees, "places": [self.autre_place.pk]})
        self.assertEqual(second.status_code, 302)
        self.assertEqual(second.url, premier.url)
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(ReservationPlace.objects.count(), 1)

    def test_email_invalide_et_selection_invalide_sans_ecriture(self):
        response = self.client.post(self.url, {**self.donnees, "email": "invalide"})
        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, "reservations/selection.html")
        self.assertEqual(response.context["places"][0].pk, self.place.pk)
        response = self.client.post(self.url, {**self.donnees, "places": [99999999]})
        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, "cinema/detail_seance.html")
        self.assertFalse(Reservation.objects.exists())

    def test_conflit_au_blocage_reaffiche_le_plan(self):
        with patch("reservations.views.creer_reservation_provisoire", side_effect=ValidationError("La place A1 vient d’être prise.")):
            response = self.client.post(self.url, self.donnees)
        self.assertContains(response, "La place A1", status_code=400)
        self.assertTemplateUsed(response, "cinema/detail_seance.html")
        self.assertFalse(Reservation.objects.exists())

    def test_expiration_affichee_et_places_liberees(self):
        response = self.client.post(self.url, self.donnees)
        reservation = Reservation.objects.get()
        with patch("reservations.views.timezone.now", return_value=reservation.date_expiration):
            page = self.client.get(response.url)
            self.assertContains(page, "Le délai est écoulé")
            self.assertNotContains(page, "data-compte-rebours")
            self.assertEqual(compter_places_disponibles(self.seance), 2)
        reservation.refresh_from_db()
        self.assertEqual(reservation.statut, Reservation.Statut.EXPIREE)

    def test_confirmation_et_annulation_ne_sont_pas_remplacees_par_expiration(self):
        response = self.client.post(self.url, self.donnees)
        reservation = Reservation.objects.get()
        for statut in (Reservation.Statut.CONFIRMEE, Reservation.Statut.ANNULEE):
            Reservation.objects.filter(pk=reservation.pk).update(statut=statut)
            with patch("reservations.views.timezone.now", return_value=reservation.date_expiration):
                page = self.client.get(response.url)
            reservation.refresh_from_db()
            self.assertEqual(reservation.statut, statut)
            self.assertNotContains(page, "data-compte-rebours")

    def test_creation_post_uniquement_et_csrf(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(self.url, self.donnees).status_code, 403)
        self.assertFalse(Reservation.objects.exists())

    def test_lien_header_permet_reprise_depuis_programmation_meme_session(self):
        accueil = reverse("cinema:films")
        self.assertNotContains(self.client.get(accueil), "Reprendre ma réservation")
        creation = self.client.post(self.url, self.donnees)
        reservation = Reservation.objects.get()
        expiration = reservation.date_expiration
        for url in (accueil, reverse("cinema:detail_film", args=[self.seance.film_id])):
            page = self.client.get(url)
            self.assertContains(page, "Reprendre ma réservation")
            self.assertContains(page, f'href="{creation.url}"')
        self.assertNotContains(Client().get(accueil), "Reprendre ma réservation")
        self.assertContains(self.client.get(creation.url), "Vos places sont temporairement réservées")
        reservation.refresh_from_db()
        self.assertEqual(reservation.date_expiration, expiration)

    def test_lien_header_absent_pour_reservation_expiree_annulee_ou_confirmee(self):
        self.client.post(self.url, self.donnees)
        reservation = Reservation.objects.get()
        accueil = reverse("cinema:films")
        with patch("reservations.context_processors.timezone.now", return_value=reservation.date_expiration):
            self.assertNotContains(self.client.get(accueil), "Reprendre ma réservation")
        for statut in (Reservation.Statut.EXPIREE, Reservation.Statut.ANNULEE, Reservation.Statut.CONFIRMEE):
            Reservation.objects.filter(pk=reservation.pk).update(statut=statut)
            with self.subTest(statut=statut):
                self.assertNotContains(self.client.get(accueil), "Reprendre ma réservation")

    def test_reference_session_supprimee_ne_provoque_pas_erreur_header(self):
        session = self.client.session
        session["reservation_provisoire_id"] = 99999999
        session.save()
        self.assertNotContains(self.client.get(reverse("cinema:films")), "Reprendre ma réservation")
