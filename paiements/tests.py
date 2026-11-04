from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from cinema.models import Film, Salle, Seance
from reservations.models import Reservation

from .models import Paiement
from .services import compter_paiements_a_traiter


class SuiviPaiementsTests(TestCase):
    def setUp(self):
        film = Film.objects.create(titre="Film", duree=90)
        salle = Salle.objects.create(nom="Salle 1", capacite=20)
        debut = timezone.now() + timedelta(days=1)
        self.seance = Seance.objects.create(
            film=film, salle=salle, debut=debut, fin=debut + timedelta(minutes=90), prix=10,
        )
        self.reservation = Reservation.objects.create(
            seance=self.seance, email="demo@example.com", date_expiration=debut, montant_total=10,
        )

    def test_seuls_paiements_reussis_et_en_attente_sont_a_traiter(self):
        for statut in Paiement.Statut.values:
            Paiement.objects.create(
                reservation=self.reservation, stripe_session_id=f"session-{statut}", montant=10, statut=statut,
            )
        etats_avant = list(Paiement.objects.order_by("pk").values_list("pk", "statut"))
        self.assertEqual(compter_paiements_a_traiter(self.seance), 2)
        self.assertEqual(list(Paiement.objects.order_by("pk").values_list("pk", "statut")), etats_avant)

    def test_paiements_des_autres_seances_ignores(self):
        debut = self.seance.debut + timedelta(days=1)
        autre = Seance.objects.create(
            film=self.seance.film, salle=self.seance.salle, debut=debut,
            fin=debut + timedelta(minutes=90), prix=10,
        )
        reservation = Reservation.objects.create(
            seance=autre, email="autre@example.com", date_expiration=debut, montant_total=10,
        )
        Paiement.objects.create(
            reservation=reservation, stripe_session_id="session-autre", montant=10, statut=Paiement.Statut.REUSSI,
        )
        self.assertEqual(compter_paiements_a_traiter(self.seance), 0)
