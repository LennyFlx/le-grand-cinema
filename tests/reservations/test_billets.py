from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from cinema.models import Film, Place, Salle, Seance
from cinema.services import annuler_seance
from paiements.models import Paiement
from reservations.models import Billet, Reservation, ReservationPlace
from reservations.services import generer_billets


class GenerationBilletsTests(TestCase):
    def setUp(self):
        salle = Salle.objects.create(nom="Salle billets", capacite=2)
        film = Film.objects.create(titre="Film billets", duree=90)
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
        self.associations = [
            ReservationPlace.objects.create(
                reservation=self.reservation,
                place=Place.objects.create(salle=salle, rangee="A", numero=numero),
                prix_unitaire=Decimal("12.35"),
            )
            for numero in (1, 2)
        ]
        self.paiement = Paiement.objects.create(
            reservation=self.reservation, montant=Decimal("24.70"),
            statut=Paiement.Statut.REUSSI,
        )

    def test_un_billet_valide_distinct_par_place(self):
        billets = generer_billets(self.reservation)

        self.assertEqual(len(billets), 2)
        self.assertEqual(Billet.objects.count(), 2)
        self.assertEqual(
            {billet.reservation_place_id for billet in billets},
            {association.pk for association in self.associations},
        )
        self.assertEqual(len({str(billet.reference) for billet in billets}), 2)
        for billet in billets:
            self.assertTrue(billet.reference)
            self.assertEqual(billet.statut, Billet.Statut.VALIDE)
            self.assertIsNotNone(billet.date_emission)

    def test_repetition_conserve_identifiants_references_et_dates(self):
        generer_billets(self.reservation)
        billets_initiaux = list(
            Billet.objects.order_by("pk").values_list("pk", "reference", "date_emission")
        )

        generer_billets(self.reservation)

        self.assertEqual(
            list(Billet.objects.order_by("pk").values_list("pk", "reference", "date_emission")),
            billets_initiaux,
        )

    def test_billet_annule_jamais_reactive(self):
        billet = generer_billets(self.reservation)[0]
        Billet.objects.filter(pk=billet.pk).update(statut=Billet.Statut.ANNULE)

        generer_billets(self.reservation)

        billet.refresh_from_db()
        self.assertEqual(billet.statut, Billet.Statut.ANNULE)
        self.assertEqual(Billet.objects.count(), 2)

    def test_reservation_non_confirmee_refusee_meme_avec_instance_ancienne(self):
        for statut in (
            Reservation.Statut.EN_ATTENTE, Reservation.Statut.EXPIREE,
            Reservation.Statut.ANNULEE,
        ):
            with self.subTest(statut=statut):
                Reservation.objects.filter(pk=self.reservation.pk).update(statut=statut)
                with self.assertRaises(ValidationError):
                    generer_billets(self.reservation)
                self.assertFalse(Billet.objects.exists())

    def test_paiement_non_reussi_ou_absent_refuse(self):
        for statut in (
            Paiement.Statut.EN_ATTENTE, Paiement.Statut.ECHOUE,
            Paiement.Statut.REMBOURSE,
        ):
            with self.subTest(statut=statut):
                Paiement.objects.filter(pk=self.paiement.pk).update(statut=statut)
                with self.assertRaises(ValidationError):
                    generer_billets(self.reservation)
                self.assertFalse(Billet.objects.exists())

        self.paiement.delete()
        with self.assertRaises(ValidationError):
            generer_billets(self.reservation)
        self.assertFalse(Billet.objects.exists())

    def test_seance_annulee_refusee(self):
        Seance.objects.filter(pk=self.seance.pk).update(statut=Seance.Statut.ANNULEE)

        with self.assertRaises(ValidationError):
            generer_billets(self.reservation)

        self.assertFalse(Billet.objects.exists())

    def test_annulation_seance_invalide_billets_et_empeche_reemission(self):
        generer_billets(self.reservation)
        references = set(Billet.objects.values_list("reference", flat=True))

        self.assertEqual(annuler_seance(self.seance), 1)

        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.ANNULEE)
        self.assertEqual(Billet.objects.filter(statut=Billet.Statut.ANNULE).count(), 2)
        with self.assertRaises(ValidationError):
            generer_billets(self.reservation)
        self.assertEqual(set(Billet.objects.values_list("reference", flat=True)), references)
