from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from cinema.models import Film, Place, Salle, Seance
from reservations.models import Billet, Reservation, ReservationPlace
from reservations.services import (
    compter_places_disponibles,
    creer_reservation_provisoire,
    expirer_reservations,
)


class DonneesReservationProvisoire:
    def setUp(self):
        super().setUp()
        self.maintenant = timezone.now()
        self.salle = Salle.objects.create(nom="Salle", capacite=10)
        self.film = Film.objects.create(titre="Film", duree=90)
        self.seance = Seance.objects.create(
            film=self.film, salle=self.salle, debut=self.maintenant + timedelta(days=1),
            fin=self.maintenant + timedelta(days=1, minutes=90), prix=Decimal("12.35"),
        )
        self.places = [Place.objects.create(salle=self.salle, rangee="A", numero=n) for n in range(1, 11)]

    def reserver(self, places=None, email="spectateur@example.com"):
        return creer_reservation_provisoire(
            self.seance, places if places is not None else [self.places[0].pk], email,
        )


class ReservationProvisoireTests(DonneesReservationProvisoire, TestCase):
    def setUp(self):
        super().setUp()
        horloge = patch("reservations.services.timezone.now", return_value=self.maintenant)
        horloge.start()
        self.addCleanup(horloge.stop)

    def test_creation_statut_montant_places_et_blocage(self):
        reservation = self.reserver([place.pk for place in self.places[:8]])
        self.assertEqual(reservation.statut, Reservation.Statut.EN_ATTENTE)
        self.assertEqual(reservation.montant_total, Decimal("98.80"))
        self.assertEqual(reservation.date_expiration, self.maintenant + timedelta(minutes=30))
        self.assertEqual(reservation.places.count(), 8)
        self.assertEqual(set(reservation.places.values_list("prix_unitaire", flat=True)), {Decimal("12.35")})
        self.assertEqual(compter_places_disponibles(self.seance, self.maintenant), 2)
        self.assertFalse(Billet.objects.exists())

    def test_expiration_ne_depasse_pas_le_debut_de_seance(self):
        Seance.objects.filter(pk=self.seance.pk).update(debut=self.maintenant + timedelta(minutes=5))
        self.assertEqual(self.reserver().date_expiration, self.maintenant + timedelta(minutes=5))

    def test_selection_invalide_et_email_invalide_sans_ecriture(self):
        cas = [[], [p.pk for p in self.places[:9]], [self.places[0].pk] * 2,
               ["1"], [True], [-1], [2**100], [99999999]]
        for places in cas:
            with self.subTest(places=places), self.assertRaises(ValidationError):
                self.reserver(places)
            self.assertFalse(Reservation.objects.exists())
        for email in ("", "invalide"):
            with self.subTest(email=email), self.assertRaises(ValidationError):
                self.reserver(email=email)
            self.assertFalse(Reservation.objects.exists())
        self.assertFalse(ReservationPlace.objects.exists())

    def test_place_autre_salle_et_place_inactive_refusees(self):
        autre_salle = Salle.objects.create(nom="Autre", capacite=1)
        autre = Place.objects.create(salle=autre_salle, rangee="A", numero=1)
        Place.objects.filter(pk=self.places[0].pk).update(active=False)
        for pk in (autre.pk, self.places[0].pk):
            with self.subTest(pk=pk), self.assertRaises(ValidationError):
                self.reserver([pk])
        self.assertFalse(Reservation.objects.exists())

    def test_seance_invalide_refusee_meme_avec_instance_ancienne(self):
        for changements in ({"statut": "annulee"}, {"statut": "terminee"}, {"debut": self.maintenant}):
            Seance.objects.filter(pk=self.seance.pk).update(statut="programmee", debut=self.maintenant + timedelta(days=1))
            Seance.objects.filter(pk=self.seance.pk).update(**changements)
            with self.subTest(changements=changements), self.assertRaises(ValidationError):
                self.reserver()
        Seance.objects.filter(pk=self.seance.pk).update(debut=self.maintenant + timedelta(days=1))
        for modele, pk in ((Film, self.film.pk), (Salle, self.salle.pk)):
            champ = "actif" if modele is Film else "active"
            modele.objects.filter(pk=pk).update(**{champ: False})
            with self.subTest(modele=modele), self.assertRaises(ValidationError):
                self.reserver()
            modele.objects.filter(pk=pk).update(**{champ: True})
        self.assertFalse(Reservation.objects.exists())

    def test_double_reservation_refusee_puis_place_reutilisable_a_expiration(self):
        initiale = self.reserver()
        with self.assertRaises(ValidationError):
            self.reserver()
        self.assertEqual(Reservation.objects.count(), 1)
        with patch("reservations.services.timezone.now", return_value=initiale.date_expiration):
            suivante = self.reserver()
        initiale.refresh_from_db()
        self.assertEqual(initiale.statut, Reservation.Statut.EXPIREE)
        self.assertEqual(suivante.statut, Reservation.Statut.EN_ATTENTE)
        self.assertEqual(ReservationPlace.objects.count(), 2)

    def test_confirmation_reste_bloquante_apres_expiration(self):
        reservation = self.reserver()
        reservation.statut = Reservation.Statut.CONFIRMEE
        reservation.save()
        with patch("reservations.services.timezone.now", return_value=reservation.date_expiration):
            with self.assertRaises(ValidationError):
                self.reserver()
            self.assertEqual(expirer_reservations(), 0)
        reservation.refresh_from_db()
        self.assertEqual(reservation.statut, Reservation.Statut.CONFIRMEE)

    def test_expiration_idempotente_et_liberation_sans_mise_a_jour_du_statut(self):
        reservation = self.reserver()
        with patch("reservations.services.timezone.now", return_value=reservation.date_expiration):
            self.assertEqual(compter_places_disponibles(self.seance), 10)
            reservation.refresh_from_db()
            self.assertEqual(reservation.statut, Reservation.Statut.EN_ATTENTE)
            self.assertEqual(expirer_reservations(), 1)
            self.assertEqual(expirer_reservations(), 0)
        reservation.refresh_from_db()
        self.assertEqual(reservation.statut, Reservation.Statut.EXPIREE)

    def test_reservation_autre_seance_et_annulation_ne_bloquent_pas(self):
        initiale = self.reserver()
        initiale.statut = Reservation.Statut.ANNULEE
        initiale.save()
        nouvelle = self.reserver()
        autre = Seance.objects.create(
            film=self.film, salle=self.salle, debut=self.seance.debut + timedelta(days=1),
            fin=self.seance.fin + timedelta(days=1), prix=10,
        )
        reservation = creer_reservation_provisoire(autre, [self.places[0].pk], "autre@example.com")
        self.assertNotEqual(reservation.seance_id, nouvelle.seance_id)

    def test_echec_associations_annule_toute_la_transaction(self):
        with patch("reservations.services.ReservationPlace.objects.bulk_create", side_effect=RuntimeError("Échec simulé")):
            with self.assertRaises(RuntimeError):
                self.reserver()
        self.assertFalse(Reservation.objects.exists())
        self.assertFalse(ReservationPlace.objects.exists())


class ReservationProvisoireConcurrenceTests(DonneesReservationProvisoire, TransactionTestCase):
    def test_deux_demandes_simultanees_une_seule_reservation(self):
        rendez_vous = Barrier(2, timeout=10)

        def demander():
            connections.close_all()
            try:
                with connections["default"].cursor() as curseur:
                    curseur.execute("SET statement_timeout = '10s'")
                rendez_vous.wait()
                try:
                    self.reserver()
                except ValidationError:
                    return False
                return True
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executant:
            demandes = [executant.submit(demander) for _ in range(2)]
            resultats = [demande.result(timeout=20) for demande in demandes]
        self.assertEqual(sorted(resultats), [False, True])
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(ReservationPlace.objects.count(), 1)
