from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cinema.models import Film, Place, Salle, Seance
from cinema.services import annuler_seance, enregistrer_seance
from paiements.models import Paiement
from reservations.models import Billet, Reservation, ReservationPlace


class ModificationAnnulationSeanceTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="test-password"
        )
        self.client.force_login(self.admin)
        self.salle = Salle.objects.create(nom="Salle 1", capacite=2)
        self.film = Film.objects.create(titre="Film", duree=90)
        self.debut = timezone.localtime(timezone.now() + timedelta(days=2)).replace(second=0, microsecond=0)
        self.seance = enregistrer_seance(Seance(
            film=self.film, salle=self.salle, debut=self.debut,
            fin=self.debut + timedelta(minutes=90), prix=10,
        ))
        self.url = reverse("admin:cinema_seance_change", args=[self.seance.pk])
        self.liste = reverse("admin:cinema_seance_changelist")

    def donnees(self, **changements):
        donnees = {
            "film": self.film.pk, "salle": self.salle.pk,
            "debut_0": self.debut.strftime("%Y-%m-%d"),
            "debut_1": self.debut.strftime("%H:%M:%S"), "prix": "10.00", "_save": "Enregistrer",
        }
        donnees.update(changements)
        return donnees

    def reserver(self):
        reservation = Reservation.objects.create(
            seance=self.seance, email="spectateur@example.com", statut=Reservation.Statut.CONFIRMEE,
            date_expiration=self.debut, montant_total=10,
        )
        place = Place.objects.create(salle=self.salle, rangee="A", numero=1)
        association = ReservationPlace.objects.create(reservation=reservation, place=place, prix_unitaire=10)
        billet = Billet.objects.create(reservation_place=association)
        paiement = Paiement.objects.create(
            reservation=reservation, stripe_session_id="session-test", montant=10, statut=Paiement.Statut.REUSSI,
        )
        return reservation, billet, paiement

    def action(self, confirmer=False, ids=None):
        donnees = {"action": "annuler_les_seances", "_selected_action": ids or [self.seance.pk]}
        if confirmer:
            donnees["confirmer"] = "oui"
        return self.client.post(self.liste, donnees)

    def test_modification_recalcule_fin(self):
        film = Film.objects.create(titre="Autre", duree=120)
        self.debut += timedelta(hours=3)
        response = self.client.post(self.url, self.donnees(film=film.pk))
        self.assertEqual(response.status_code, 302)
        self.seance.refresh_from_db()
        self.assertEqual(self.seance.film_id, film.pk)
        self.assertEqual(self.seance.fin, self.debut + timedelta(minutes=120))

    def test_date_passee_et_chevauchement_refuses(self):
        enregistrer_seance(Seance(
            film=self.film, salle=self.salle, debut=self.debut + timedelta(hours=3),
            fin=self.debut + timedelta(hours=5), prix=10,
        ))
        self.debut += timedelta(hours=3)
        response = self.client.post(self.url, self.donnees())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["adminform"].form.errors)
        self.debut = timezone.localtime(timezone.now() - timedelta(days=1))
        response = self.client.post(self.url, self.donnees())
        self.assertIn("debut", response.context["adminform"].form.errors)
        self.seance.refresh_from_db()
        self.assertGreater(self.seance.debut, timezone.now())

    def test_reservation_bloque_film_horaire_et_tarif(self):
        self.reserver()
        autre = Film.objects.create(titre="Autre", duree=120)
        for changements in ({"film": autre.pk}, {"prix": "12.00"}, {"debut_1": "23:00:00"}):
            with self.subTest(changements=changements):
                response = self.client.post(self.url, self.donnees(**changements))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["adminform"].form.non_field_errors())
        self.seance.refresh_from_db()
        self.assertEqual(self.seance.film_id, self.film.pk)
        self.assertEqual(self.seance.prix, Decimal("10.00"))
        self.assertEqual(self.seance.debut, self.debut)

    def test_confirmation_sans_effet_avant_validation(self):
        reservation, _, _ = self.reserver()
        response = self.action()
        self.assertContains(response, reservation.email)
        self.assertContains(response, "manuellement")
        self.seance.refresh_from_db()
        self.assertEqual(self.seance.statut, Seance.Statut.PROGRAMMEE)

    def test_annulation_conserve_historique_et_invalide_billets(self):
        reservation, billet, paiement = self.reserver()
        response = self.action(confirmer=True)
        self.assertEqual(response.status_code, 302)
        self.seance.refresh_from_db()
        reservation.refresh_from_db()
        billet.refresh_from_db()
        paiement.refresh_from_db()
        self.assertEqual(self.seance.statut, Seance.Statut.ANNULEE)
        self.assertEqual(reservation.statut, Reservation.Statut.ANNULEE)
        self.assertEqual(billet.statut, Billet.Statut.ANNULE)
        self.assertEqual(paiement.statut, Paiement.Statut.REUSSI)
        self.assertEqual(reservation.places.count(), 1)
        self.assertContains(self.client.get(self.liste), "1 paiement(s)")

    def test_annulation_reservation_provisoire_et_expiree(self):
        provisoire = Reservation.objects.create(
            seance=self.seance, email="test@example.com", date_expiration=self.debut, montant_total=10,
        )
        expiree = Reservation.objects.create(
            seance=self.seance, email="expire@example.com", date_expiration=self.debut,
            montant_total=10, statut=Reservation.Statut.EXPIREE,
        )
        self.assertEqual(annuler_seance(self.seance), 0)
        provisoire.refresh_from_db()
        expiree.refresh_from_db()
        self.assertEqual(provisoire.statut, Reservation.Statut.ANNULEE)
        self.assertEqual(expiree.statut, Reservation.Statut.EXPIREE)
        with self.assertRaises(ValidationError):
            annuler_seance(self.seance)

    def test_lot_invalide_annule_toute_operation(self):
        autre = Seance.objects.create(
            film=self.film, salle=self.salle, debut=self.debut + timedelta(days=1),
            fin=self.debut + timedelta(days=1, minutes=90), prix=10, statut=Seance.Statut.ANNULEE,
        )
        reservation, billet, _ = self.reserver()
        self.action(confirmer=True, ids=[self.seance.pk, autre.pk])
        self.seance.refresh_from_db()
        reservation.refresh_from_db()
        billet.refresh_from_db()
        self.assertEqual(self.seance.statut, Seance.Statut.PROGRAMMEE)
        self.assertEqual(reservation.statut, Reservation.Statut.CONFIRMEE)
        self.assertEqual(billet.statut, Billet.Statut.VALIDE)

    def test_seance_commencee_et_annulee_non_modifiables(self):
        for statut, debut in (
            (Seance.Statut.ANNULEE, self.debut),
            (Seance.Statut.PROGRAMMEE, self.debut - timedelta(days=3)),
        ):
            Seance.objects.filter(pk=self.seance.pk).update(statut=statut, debut=debut, fin=debut + timedelta(minutes=90))
            self.assertEqual(self.client.post(self.url, self.donnees()).status_code, 403)
            self.seance.refresh_from_db()
            with self.assertRaises(ValidationError):
                annuler_seance(self.seance)

    def test_permissions_et_suppression(self):
        personnel = get_user_model().objects.create_user(username="personnel", is_staff=True)
        self.client.force_login(personnel)
        self.assertEqual(self.client.post(self.url, self.donnees()).status_code, 403)
        self.action(confirmer=True)
        self.seance.refresh_from_db()
        self.assertEqual(self.seance.statut, Seance.Statut.PROGRAMMEE)
        self.client.force_login(self.admin)
        url = reverse("admin:cinema_seance_delete", args=[self.seance.pk])
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
