import json
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta
from io import StringIO
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from cinema.models import Film, Genre, Participation, Personne, Place, Salle, Seance
from scripts.generer_fixtures_demo import FUSEAU, generer_seances, main


class SallesInitialesFixtureTests(TestCase):
    fixtures = ["salles_initiales"]

    def test_capacites_et_disposition_conformes(self):
        attendues = {
            "Premium / IMAX 1": 300,
            "Premium / IMAX 2": 300,
            "Standard 1": 180,
            "Standard 2": 180,
            "Standard 3": 180,
            "Standard 4": 180,
            "VIP 1": 120,
            "VIP 2": 120,
            "Événementielle / modulable 1": 70,
            "Événementielle / modulable 2": 70,
        }
        self.assertEqual(dict(Salle.objects.values_list("nom", "capacite")), attendues)
        self.assertEqual(Place.objects.count(), 1700)
        self.assertFalse(Salle.objects.filter(active=False).exists())
        self.assertFalse(Place.objects.filter(active=False).exists())
        for salle in Salle.objects.all():
            with self.subTest(salle=salle.nom):
                # Convention du projet : 20 places par rangée (A, B, etc.).
                # Pour 70 places, la dernière rangée ne contient que 10 places.
                places_attendues = {
                    (chr(65 + index // 20), index % 20 + 1)
                    for index in range(salle.capacite)
                }
                places = list(salle.places.values_list("rangee", "numero"))
                self.assertEqual(len(places), salle.capacite)
                self.assertEqual(set(places), places_attendues)

    def test_rechargement_ne_cree_pas_de_doublons(self):
        call_command("loaddata", "salles_initiales", stdout=StringIO())
        self.assertEqual(Salle.objects.count(), 10)
        self.assertEqual(Place.objects.count(), 1700)

class CatalogueInitialFixtureTests(TestCase):
    fixtures = ["catalogue_initial"]

    def test_chargement_et_rechargement_conservent_les_associations(self):
        avant = {
            film.pk: (
                film.titre,
                film.description,
                film.affiche,
                list(film.genres.values_list("pk", flat=True)),
                list(film.participations.values_list("personne_id", "fonction")),
            )
            for film in Film.objects.all()
        }
        call_command("loaddata", "catalogue_initial", stdout=StringIO())
        self.assertEqual(Film.objects.count(), 5)
        self.assertEqual(Personne.objects.count(), 26)
        self.assertEqual(Genre.objects.count(), 5)
        self.assertEqual(Participation.objects.count(), 27)
        for film in Film.objects.all():
            with self.subTest(film=film.titre):
                self.assertEqual(
                    (
                        film.titre,
                        film.description,
                        film.affiche,
                        list(film.genres.values_list("pk", flat=True)),
                        list(film.participations.values_list("personne_id", "fonction")),
                    ),
                    avant[film.pk],
                )
                self.assertTrue(film.genres.exists())
                self.assertTrue(film.participations.filter(fonction="acteur").exists())
                self.assertTrue(film.participations.filter(fonction="realisateur").exists())

class GenerationSeancesFixtureTests(TestCase):
    fixtures = ["salles_initiales", "catalogue_initial"]

    def test_fichier_genere_charge_sans_chevauchement_et_se_recharge(self):
        with TemporaryDirectory() as dossier:
            chemin = Path(dossier) / "seances.json"
            with redirect_stdout(StringIO()):
                main(["--date-debut", "2027-03-27", "--jours", "3", "--sortie", str(chemin)])
            self.assertEqual(len(json.loads(chemin.read_text(encoding="utf-8"))), 90)
            call_command("loaddata", str(chemin), stdout=StringIO())
            call_command("loaddata", str(chemin), stdout=StringIO())
        self.assertEqual(Seance.objects.count(), 90)
        self.assertEqual(set(Seance.objects.values_list("film_id", flat=True)), set(Film.objects.values_list("pk", flat=True)))
        for salle in Salle.objects.all():
            precedentes = list(salle.seances.select_related("film").order_by("debut"))
            self.assertEqual(len(precedentes), 9)
            for seance in precedentes:
                self.assertEqual(seance.fin - seance.debut, timedelta(minutes=seance.film.duree))
                self.assertEqual(seance.statut, Seance.Statut.PROGRAMMEE)
                self.assertIn(seance.debut.astimezone(FUSEAU).date(), (date(2027, 3, 27), date(2027, 3, 28), date(2027, 3, 29)))
            for premiere, suivante in pairwise(precedentes):
                self.assertGreaterEqual(suivante.debut - premiere.fin, timedelta(minutes=30))


class HorairesFixturesTests(SimpleTestCase):
    def test_heures_locales_conservees_au_changement_d_heure(self):
        films = [{"pk": 1, "fields": {"actif": True, "duree": 90}}]
        salles = [{"pk": 1, "fields": {"active": True}}]
        for premier_jour in (date(2027, 3, 27), date(2027, 10, 30)):
            with self.subTest(premier_jour=premier_jour):
                seances = generer_seances(films, salles, premier_jour, 2)
                debuts = [datetime.fromisoformat(seance["fields"]["debut"]) for seance in seances]
                self.assertEqual([debut.hour for debut in debuts], [14, 18, 21, 14, 18, 21])
                self.assertNotEqual(debuts[0].utcoffset(), debuts[3].utcoffset())

    def test_periode_invalide_ne_genere_pas_de_fichier(self):
        with TemporaryDirectory() as dossier:
            chemin = Path(dossier) / "seances.json"
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as sortie:
                main(["--jours", "0", "--sortie", str(chemin)])
            self.assertEqual(sortie.exception.code, 2)
            self.assertFalse(chemin.exists())
