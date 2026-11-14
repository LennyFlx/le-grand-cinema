from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import Client, TestCase
from django.urls import reverse

from cinema.models import Film, Place, Salle, Seance
from reservations.models import Reservation, ReservationPlace


class ValidationSelectionTests(TestCase):
    def setUp(self):
        self.maintenant = datetime(2030, 11, 4, 12, tzinfo=ZoneInfo("Europe/Paris"))
        horloge = patch("reservations.views.timezone.now", return_value=self.maintenant)
        horloge.start()
        self.addCleanup(horloge.stop)
        film = Film.objects.create(titre="Film sélection", duree=90)
        salle = Salle.objects.create(nom="Salle", capacite=12)
        self.places = [Place.objects.create(salle=salle, rangee="A", numero=n) for n in range(1, 13)]
        self.seance = Seance.objects.create(
            film=film, salle=salle, debut=self.maintenant + timedelta(days=1),
            fin=self.maintenant + timedelta(days=1, minutes=90), prix=Decimal("12.35"),
        )
        self.url = reverse("reservations:valider_selection", args=[self.seance.pk])

    def test_selection_valide_total_serveur_sans_reservation(self):
        for nombre in (1, 8):
            with self.subTest(nombre=nombre):
                response = self.client.post(self.url, {"places": [p.pk for p in self.places[:nombre]], "total": "0.01"})
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "reservations/selection.html")
                self.assertEqual(response.context["total"], Decimal("12.35") * nombre)
                self.assertEqual([p.pk for p in response.context["places"]], [p.pk for p in self.places[:nombre]])
                self.assertFalse(Reservation.objects.exists())
                self.assertFalse(ReservationPlace.objects.exists())
                self.assertIn("no-store", response["Cache-Control"])

    def test_selection_vide_doublon_malformee_et_limite_refusees(self):
        for valeurs in ([], [self.places[0].pk] * 2, [p.pk for p in self.places[:9]], ["invalide"], [99999999]):
            with self.subTest(valeurs=valeurs):
                response = self.client.post(self.url, {"places": valeurs})
                self.assertEqual(response.status_code, 400)
                self.assertTemplateUsed(response, "cinema/detail_seance.html")
                self.assertTrue(response.context["erreurs_selection"])
                self.assertFalse(Reservation.objects.exists())

    def test_place_autre_salle_et_place_inactive_refusees(self):
        autre_salle = Salle.objects.create(nom="Autre", capacite=1)
        autre_place = Place.objects.create(salle=autre_salle, rangee="A", numero=1)
        self.places[0].active = False
        self.places[0].save()
        for place in (self.places[0], autre_place):
            with self.subTest(place=place.pk):
                self.assertEqual(self.client.post(self.url, {"places": [place.pk]}).status_code, 400)

    def test_place_prise_apres_affichage_et_selection_restante_conservee(self):
        self.client.get(reverse("cinema:detail_seance", args=[self.seance.pk]))
        reservation = Reservation.objects.create(
            seance=self.seance, email="spectateur@example.com", statut="confirmee",
            date_expiration=self.maintenant, montant_total=Decimal("12.35"),
        )
        ReservationPlace.objects.create(reservation=reservation, place=self.places[0], prix_unitaire=Decimal("12.35"))
        response = self.client.post(self.url, {"places": [p.pk for p in self.places[:2]]})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "A1", status_code=400)
        rangee = response.context["disponibilites"]["rangees"][0]["places"]
        self.assertEqual(rangee[0]["statut"], "occupee")
        self.assertFalse(rangee[0]["selectionnee"])
        self.assertTrue(rangee[1]["selectionnee"])
        self.assertEqual(Reservation.objects.count(), 1)

    def test_blocage_en_attente_et_expiration(self):
        reservation = Reservation.objects.create(
            seance=self.seance, email="spectateur@example.com", statut="en_attente",
            date_expiration=self.maintenant + timedelta(minutes=15), montant_total=12,
        )
        ReservationPlace.objects.create(reservation=reservation, place=self.places[0], prix_unitaire=12)
        self.assertEqual(self.client.post(self.url, {"places": [self.places[0].pk]}).status_code, 400)
        reservation.date_expiration = self.maintenant
        reservation.save()
        self.assertEqual(self.client.post(self.url, {"places": [self.places[0].pk]}).status_code, 200)

    def test_seance_non_publique_ou_commencee_refusee(self):
        cas = [{"statut": "annulee"}, {"debut": self.maintenant}, {"statut": "terminee"}]
        for changements in cas:
            with self.subTest(changements=changements):
                Seance.objects.filter(pk=self.seance.pk).update(statut="programmee", debut=self.maintenant + timedelta(days=1))
                Seance.objects.filter(pk=self.seance.pk).update(**changements)
                self.assertEqual(self.client.post(self.url, {"places": [self.places[0].pk]}).status_code, 404)

    def test_post_requis_et_protection_csrf(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(self.url, {"places": [self.places[0].pk]}).status_code, 403)
        client.get(reverse("cinema:detail_seance", args=[self.seance.pk]))
        response = client.post(self.url, {"places": [self.places[0].pk], "csrfmiddlewaretoken": client.cookies["csrftoken"].value})
        self.assertEqual(response.status_code, 200)
