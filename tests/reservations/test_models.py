from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from cinema.models import Film, Place, Salle, Seance
from cinema.services import enregistrer_place, enregistrer_seance
from reservations.models import Billet, Reservation, ReservationPlace


class ReservationEtBilletTests(TestCase):
    def setUp(self):
        self.salle = Salle.objects.create(nom="Salle 1", capacite=1)
        self.film = Film.objects.create(titre="Film", duree=90)
        self.debut = timezone.now() + timedelta(days=1)

    def seance(self, **modifications):
        valeurs = {
            "film": self.film,
            "salle": self.salle,
            "debut": self.debut,
            "fin": self.debut + timedelta(minutes=90),
            "prix": 10,
        }
        valeurs.update(modifications)
        return Seance(**valeurs)

    def test_place_reservee_doit_appartenir_a_la_salle(self):
        reservation = Reservation.objects.create(
            seance=enregistrer_seance(self.seance()),
            email="test@example.com",
            date_expiration=self.debut,
            montant_total=10,
        )
        autre = Salle.objects.create(nom="Autre", capacite=1)
        place = Place.objects.create(salle=autre, rangee="A", numero=1)
        association = ReservationPlace(
            reservation=reservation,
            place=place,
            prix_unitaire=10,
        )
        with self.assertRaises(ValidationError):
            association.full_clean()

    def test_billet_exige_reservation_confirmee(self):
        reservation = Reservation.objects.create(
            seance=enregistrer_seance(self.seance()),
            email="test@example.com",
            date_expiration=self.debut,
            montant_total=10,
        )
        place = enregistrer_place(Place(salle=self.salle, rangee="A", numero=1))
        association = ReservationPlace.objects.create(
            reservation=reservation,
            place=place,
            prix_unitaire=10,
        )
        billet = Billet(reservation_place=association)
        with self.assertRaises(ValidationError):
            billet.full_clean()
        reservation.statut = Reservation.Statut.CONFIRMEE
        reservation.save()
        billet.full_clean()


class CoherenceReservationPlaceTests(TestCase):
    def setUp(self):
        self.salle = Salle.objects.create(nom="Salle 1", capacite=3)
        self.autre_salle = Salle.objects.create(nom="Salle 2", capacite=3)
        film = Film.objects.create(titre="Film", duree=90)
        debut = timezone.now() + timedelta(days=1)
        self.seance = enregistrer_seance(
            Seance(
                film=film,
                salle=self.salle,
                debut=debut,
                fin=debut + timedelta(minutes=90),
                prix=10,
            )
        )
        self.reservation = Reservation.objects.create(
            seance=self.seance,
            email="test@example.com",
            date_expiration=debut,
            montant_total=10,
        )
        self.place = enregistrer_place(Place(salle=self.salle, rangee="A", numero=1))
        self.place_inactive = enregistrer_place(
            Place(salle=self.salle, rangee="A", numero=2, active=False)
        )
        self.association = ReservationPlace.objects.create(
            reservation=self.reservation,
            place=self.place,
            prix_unitaire=10,
        )

    def test_remplacement_par_place_inactive_refuse(self):
        self.association.place = self.place_inactive
        with self.assertRaisesMessage(ValidationError, "Cette place est inactive."):
            self.association.full_clean()

    def test_conservation_place_devenue_inactive_autorisee(self):
        self.place.active = False
        enregistrer_place(self.place)
        self.association.prix_unitaire = 12
        self.association.full_clean()
        self.association.save()
        self.association.refresh_from_db()
        self.assertEqual(self.association.prix_unitaire, 12)
        self.assertEqual(self.association.place_id, self.place.pk)

