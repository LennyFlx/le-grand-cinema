from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from .models import Billet, Film, Place, Reservation, ReservationPlace, Salle, Seance
from .services import enregistrer_place, enregistrer_salle, enregistrer_seance


class ProgrammationTests(TestCase):
    def setUp(self):
        self.salle = Salle.objects.create(nom="Salle 1", capacite=1)
        self.film = Film.objects.create(titre="Film", duree=90)
        self.debut = timezone.now() + timedelta(days=1)

    def seance(self, **changes):
        values = dict(film=self.film, salle=self.salle, debut=self.debut,
                      fin=self.debut + timedelta(minutes=90), prix=10)
        values.update(changes)
        return Seance(**values)

    def test_terminee_bloque_creneau(self):
        self.seance(statut=Seance.Statut.TERMINEE).save()
        with self.assertRaises(ValidationError):
            enregistrer_seance(self.seance())

    def test_creneaux_contigus_et_annulation(self):
        enregistrer_seance(self.seance())
        enregistrer_seance(self.seance(statut=Seance.Statut.ANNULEE))
        enregistrer_seance(self.seance(debut=self.debut + timedelta(minutes=90),
                                      fin=self.debut + timedelta(minutes=180)))

    def test_protection_sql_sans_validation(self):
        self.seance().save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.seance().save()

    def test_salle_inactive_autorise_annulation(self):
        seance = enregistrer_seance(self.seance())
        self.salle.active = False
        enregistrer_salle(self.salle)
        seance.statut = Seance.Statut.ANNULEE
        enregistrer_seance(seance)
        with self.assertRaises(ValidationError):
            enregistrer_seance(self.seance())

    def test_capacite_compte_places_inactives(self):
        enregistrer_place(Place(salle=self.salle, rangee="A", numero=1, active=False))
        with self.assertRaises(ValidationError):
            enregistrer_place(Place(salle=self.salle, rangee="A", numero=2))

    def test_reduction_capacite(self):
        self.salle.capacite = 2
        enregistrer_salle(self.salle)
        for numero in (1, 2):
            enregistrer_place(Place(salle=self.salle, rangee="A", numero=numero))
        self.salle.capacite = 1
        with self.assertRaises(ValidationError):
            enregistrer_salle(self.salle)

    def test_place_reservee_doit_appartenir_a_la_salle(self):
        reservation = Reservation.objects.create(
            seance=enregistrer_seance(self.seance()), email="test@example.com",
            date_expiration=self.debut, montant_total=10,
        )
        autre = Salle.objects.create(nom="Autre", capacite=1)
        place = Place.objects.create(salle=autre, rangee="A", numero=1)
        association = ReservationPlace(reservation=reservation, place=place, prix_unitaire=10)
        with self.assertRaises(ValidationError):
            association.full_clean()

    def test_billet_exige_reservation_confirmee(self):
        reservation = Reservation.objects.create(
            seance=enregistrer_seance(self.seance()), email="test@example.com",
            date_expiration=self.debut, montant_total=10,
        )
        place = enregistrer_place(Place(salle=self.salle, rangee="A", numero=1))
        association = ReservationPlace.objects.create(reservation=reservation, place=place, prix_unitaire=10)
        billet = Billet(reservation_place=association)
        with self.assertRaises(ValidationError):
            billet.full_clean()
        reservation.statut = Reservation.Statut.CONFIRMEE
        reservation.save()
        billet.full_clean()
