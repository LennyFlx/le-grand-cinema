from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse

from cinema.models import Film, Place, Salle, Seance
from reservations.models import Reservation, ReservationPlace
from reservations.services import compter_places_disponibles, obtenir_disponibilites


class DisponibilitesSeanceTests(TestCase):
    def setUp(self):
        self.maintenant = datetime(2030, 11, 4, 10, tzinfo=ZoneInfo("Europe/Paris"))
        horloge = patch("cinema.views.timezone.now", return_value=self.maintenant)
        horloge.start()
        self.addCleanup(horloge.stop)
        self.film = Film.objects.create(titre="Film", duree=90)
        self.salle = Salle.objects.create(nom="Salle 1", capacite=4)
        self.debut = self.maintenant + timedelta(days=1)
        self.seance = Seance.objects.create(
            film=self.film, salle=self.salle, debut=self.debut,
            fin=self.debut + timedelta(minutes=90), prix=10,
        )
        self.places = [Place.objects.create(
            salle=self.salle, rangee="A", numero=n, active=n != 4,
        ) for n in range(1, 5)]
        self.url = reverse("cinema:detail_seance", args=[self.seance.pk])

    def reserver(self, place, statut=Reservation.Statut.CONFIRMEE, expiration=None, seance=None):
        reservation = Reservation.objects.create(
            seance=seance or self.seance, email="prive@example.com", statut=statut,
            date_expiration=expiration or self.maintenant + timedelta(minutes=15), montant_total=10,
        )
        ReservationPlace.objects.create(reservation=reservation, place=place, prix_unitaire=10)
        return reservation

    def disponibilites(self):
        return obtenir_disponibilites(self.seance, self.maintenant)

    def test_compteur_et_etats_des_places(self):
        self.reserver(self.places[0])
        self.reserver(self.places[1], statut=Reservation.Statut.EN_ATTENTE)
        etat = self.disponibilites()
        self.assertEqual(etat["total"], 3)
        self.assertEqual(etat["disponibles"], 1)
        self.assertFalse(etat["complet"])
        self.assertEqual([p["statut"] for p in etat["rangees"][0]["places"]], [
            "occupee", "occupee", "disponible", "inactive",
        ])

    def test_statuts_et_expiration_y_compris_la_limite_exacte(self):
        cas = [
            (Reservation.Statut.CONFIRMEE, -1, 2),
            (Reservation.Statut.EN_ATTENTE, 1, 2),
            (Reservation.Statut.EN_ATTENTE, -1, 3),
            (Reservation.Statut.EN_ATTENTE, 0, 3),
            (Reservation.Statut.ANNULEE, 1, 3),
            (Reservation.Statut.EXPIREE, 1, 3),
        ]
        for statut, delai, disponibles in cas:
            with self.subTest(statut=statut, delai=delai):
                ReservationPlace.objects.all().delete()
                Reservation.objects.all().delete()
                self.reserver(self.places[0], statut, self.maintenant + timedelta(minutes=delai))
                self.assertEqual(self.disponibilites()["disponibles"], disponibles)

    def test_reservations_autre_seance_et_places_autre_salle_ignorees(self):
        autre = Seance.objects.create(
            film=self.film, salle=self.salle, debut=self.debut + timedelta(days=1),
            fin=self.debut + timedelta(days=1, minutes=90), prix=10,
        )
        self.reserver(self.places[0], seance=autre)
        salle = Salle.objects.create(nom="Autre salle", capacite=1)
        Place.objects.create(salle=salle, rangee="Z", numero=1)
        etat = self.disponibilites()
        self.assertEqual(etat["total"], 3)
        self.assertEqual(etat["disponibles"], 3)
        self.assertEqual([r["nom"] for r in etat["rangees"]], ["A"])

    def test_place_comptee_une_seule_fois_meme_si_plusieurs_associations(self):
        self.reserver(self.places[0])
        self.reserver(self.places[0], statut=Reservation.Statut.EN_ATTENTE)
        self.assertEqual(self.disponibilites()["disponibles"], 2)

    def test_plan_et_lecture_publique_sans_donnees_personnelles(self):
        self.reserver(self.places[0])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "cinema/detail_seance.html")
        self.assertContains(response, "2 places disponibles sur 3")
        self.assertContains(response, 'aria-label="Place A1 : Réservée"')
        self.assertContains(response, 'aria-label="Place A2 : Disponible"')
        self.assertContains(response, 'aria-label="Place A4 : Hors service"')
        self.assertNotContains(response, "prive@example.com")
        self.assertEqual(self.client.post(self.url, {"place": self.places[1].pk}).status_code, 405)
        self.assertEqual(Reservation.objects.count(), 1)

    def test_seance_complete(self):
        for place in self.places[:3]:
            self.reserver(place)
        etat = self.disponibilites()
        self.assertTrue(etat["complet"])
        self.assertEqual(etat["disponibles"], 0)
        self.assertContains(self.client.get(self.url), "Séance complète.")

    def test_salle_sans_places_et_sans_places_actives(self):
        Place.objects.all().update(active=False)
        etat = self.disponibilites()
        self.assertEqual(etat["total"], 0)
        self.assertFalse(etat["complet"])
        self.assertContains(self.client.get(self.url), "Aucune place ouverte à la réservation")
        Place.objects.all().delete()
        self.assertEqual(self.disponibilites()["rangees"], [])
        self.assertContains(self.client.get(self.url), "Les disponibilités de cette salle ne sont pas encore consultables.")

    def test_seances_non_publiques_refusees(self):
        self.assertEqual(self.client.get(reverse("cinema:detail_seance", args=[999999])).status_code, 404)
        cas = [
            (Seance, {"statut": Seance.Statut.ANNULEE}),
            (Seance, {"statut": Seance.Statut.TERMINEE}),
            (Seance, {"debut": self.maintenant - timedelta(hours=1)}),
            (Seance, {"debut": self.maintenant}),
            (Film, {"actif": False}),
            (Salle, {"active": False}),
        ]
        for modele, changements in cas:
            with self.subTest(modele=modele.__name__, changements=changements):
                Seance.objects.filter(pk=self.seance.pk).update(statut=Seance.Statut.PROGRAMMEE, debut=self.debut)
                Film.objects.filter(pk=self.film.pk).update(actif=True)
                Salle.objects.filter(pk=self.salle.pk).update(active=True)
                cible = {Seance: self.seance.pk, Film: self.film.pk, Salle: self.salle.pk}[modele]
                modele.objects.filter(pk=cible).update(**changements)
                self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_liens_depuis_la_programmation_et_la_fiche_film(self):
        jour = self.debut.date().isoformat()
        for url in (reverse("cinema:films"), reverse("cinema:detail_film", args=[self.film.pk])):
            with self.subTest(url=url):
                response = self.client.get(url, {"date": jour})
                self.assertContains(response, f'href="{self.url}"')
        response = self.client.get(self.url)
        self.assertContains(response, f'href="{reverse("cinema:detail_film", args=[self.film.pk])}?date={jour}"')

    def test_actualisation_apres_confirmation_annulation_et_expiration(self):
        self.assertEqual(self.client.get(self.url).context["disponibilites"]["disponibles"], 3)
        reservation = self.reserver(self.places[0])
        response = self.client.get(self.url)
        self.assertEqual(response.context["disponibilites"]["disponibles"], 2)
        self.assertIn("no-store", response["Cache-Control"])
        reservation.statut = Reservation.Statut.ANNULEE
        reservation.save()
        self.assertEqual(self.client.get(self.url).context["disponibilites"]["disponibles"], 3)
        provisoire = self.reserver(self.places[1], statut=Reservation.Statut.EN_ATTENTE, expiration=self.maintenant + timedelta(minutes=1))
        self.assertEqual(self.client.get(self.url).context["disponibilites"]["disponibles"], 2)
        with patch("cinema.views.timezone.now", return_value=self.maintenant + timedelta(minutes=2)):
            self.assertEqual(self.client.get(self.url).context["disponibilites"]["disponibles"], 3)
        provisoire.refresh_from_db()
        self.assertEqual(provisoire.statut, Reservation.Statut.EN_ATTENTE)

    def test_tri_des_rangees_et_numeros(self):
        self.places[1].numero = 10
        self.places[1].save()
        self.places[2].numero = 2
        self.places[2].save()
        self.places[3].rangee = "B"
        self.places[3].numero = 1
        self.places[3].save()
        etat = self.disponibilites()
        self.assertEqual([r["nom"] for r in etat["rangees"]], ["A", "B"])
        self.assertEqual([p["numero"] for p in etat["rangees"][0]["places"]], [1, 2, 10])

    def test_calcul_en_une_requete_et_page_sans_requete_par_place(self):
        with self.assertNumQueries(1):
            self.disponibilites()
        with self.assertNumQueries(2):
            self.client.get(self.url)

    def test_compteur_admin_identique_au_plan_public(self):
        self.reserver(self.places[0])
        self.reserver(self.places[1], statut=Reservation.Statut.EN_ATTENTE)
        self.reserver(self.places[2], statut=Reservation.Statut.EN_ATTENTE, expiration=self.maintenant)
        with self.assertNumQueries(1):
            compteur = compter_places_disponibles(self.seance, self.maintenant)
        self.assertEqual(compteur, self.disponibilites()["disponibles"])
        self.assertEqual(compteur, 1)
