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

    def test_terminee_bloque_creneau(self):
        self.seance(statut=Seance.Statut.TERMINEE).save()
        with self.assertRaises(ValidationError):
            enregistrer_seance(self.seance())

    def test_creneaux_contigus_et_annulation(self):
        enregistrer_seance(self.seance())
        enregistrer_seance(self.seance(statut=Seance.Statut.ANNULEE))
        enregistrer_seance(
            self.seance(
                debut=self.debut + timedelta(minutes=90),
                fin=self.debut + timedelta(minutes=180),
            )
        )

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
        enregistrer_place(
            Place(salle=self.salle, rangee="A", numero=1, active=False)
        )
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


class CoherenceReservationsTests(TestCase):
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

    def test_seance_avec_places_reservees_ne_change_pas_de_salle(self):
        self.seance.salle = self.autre_salle

        with self.assertRaisesMessage(ValidationError, "ne peut pas changer de salle"):
            enregistrer_seance(self.seance)

        self.seance.refresh_from_db()
        self.assertEqual(self.seance.salle_id, self.salle.pk)

    def test_seance_sans_reservation_peut_changer_de_salle(self):
        seance = enregistrer_seance(
            Seance(
                film=self.seance.film,
                salle=self.salle,
                debut=self.seance.fin,
                fin=self.seance.fin + timedelta(minutes=90),
                prix=10,
            )
        )
        seance.salle = self.autre_salle
        enregistrer_seance(seance)
        seance.refresh_from_db()
        self.assertEqual(seance.salle_id, self.autre_salle.pk)


class CreationSeanceAdminTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        self.admin = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="test-password"
        )
        self.client.force_login(self.admin)
        self.url = reverse("admin:cinema_seance_add")
        self.film = Film.objects.create(titre="Film test", duree=90)
        self.salle = Salle.objects.create(nom="Salle 1", capacite=3)
        Place.objects.create(salle=self.salle, rangee="A", numero=1)
        Place.objects.create(salle=self.salle, rangee="A", numero=2, active=False)
        self.debut = timezone.localtime(timezone.now() + timedelta(days=1)).replace(
            second=0, microsecond=0
        )

    def donnees(self, **modifications):
        donnees = {
            "film": self.film.pk,
            "salle": self.salle.pk,
            "debut_0": self.debut.strftime("%Y-%m-%d"),
            "debut_1": self.debut.strftime("%H:%M:%S"),
            "prix": "10.00",
            "_save": "Enregistrer",
        }
        donnees.update(modifications)
        return donnees

    def test_creation_calcule_fin_et_conserve_places_physiques(self):
        from django.contrib.messages import get_messages
        from cinema.admin import SeanceAdmin
        from django.contrib import admin

        response = self.client.post(self.url, self.donnees())
        self.assertEqual(response.status_code, 302)
        seance = Seance.objects.get()
        self.assertEqual(seance.fin, self.debut + timedelta(minutes=90))
        self.assertEqual(seance.statut, Seance.Statut.PROGRAMMEE)
        self.assertEqual(SeanceAdmin(Seance, admin.site).places_disponibles(seance), 1)
        self.assertEqual(self.salle.places.count(), 2)
        self.assertTrue(list(get_messages(response.wsgi_request)))

    def test_date_passee_refusee(self):
        self.debut = timezone.localtime(timezone.now() - timedelta(days=1))
        response = self.client.post(self.url, self.donnees())
        self.assertIn("debut", response.context["adminform"].form.errors)
        self.assertFalse(Seance.objects.exists())

    def test_chevauchement_refuse_et_creneau_contigu_accepte(self):
        self.client.post(self.url, self.donnees())
        response = self.client.post(self.url, self.donnees())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["adminform"].form.non_field_errors())
        self.assertEqual(Seance.objects.count(), 1)
        self.debut += timedelta(minutes=90)
        self.assertEqual(self.client.post(self.url, self.donnees()).status_code, 302)
        self.assertEqual(Seance.objects.count(), 2)

    def test_film_et_salle_inactifs_refuses(self):
        self.film.actif = False
        self.film.save()
        response = self.client.post(self.url, self.donnees())
        self.assertIn("film", response.context["adminform"].form.errors)
        self.film.actif = True
        self.film.save()
        self.salle.active = False
        self.salle.save()
        response = self.client.post(self.url, self.donnees())
        self.assertIn("salle", response.context["adminform"].form.errors)
        self.assertFalse(Seance.objects.exists())

    def test_champs_obligatoires_et_prix_negatif_refuses(self):
        response = self.client.post(self.url, self.donnees(film="", prix="-1"))
        errors = response.context["adminform"].form.errors
        self.assertIn("film", errors)
        self.assertIn("prix", errors)
        self.assertFalse(Seance.objects.exists())

    def test_acces_anonyme_et_personnel_sans_permission_refuses(self):
        from django.contrib.auth import get_user_model

        self.client.logout()
        self.assertEqual(self.client.post(self.url, self.donnees()).status_code, 302)
        personnel = get_user_model().objects.create_user(username="personnel", is_staff=True)
        self.client.force_login(personnel)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.donnees()).status_code, 403)
        self.assertFalse(Seance.objects.exists())
