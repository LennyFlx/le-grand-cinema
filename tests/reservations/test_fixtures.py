import json
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.db.models import F
from django.test import TestCase

from cinema.models import Seance
from reservations.models import Reservation, ReservationPlace
from reservations.services import obtenir_disponibilites
from scripts.generer_fixtures_demo import FIXTURES, FUSEAU, generer_seances
from scripts.generer_reservations_demo import main


class ReservationsDemoFixtureTests(TestCase):
    fixtures = ["salles_initiales", "catalogue_initial"]

    def test_generation_chargement_disponibilites_expiration_et_rechargement(self):
        catalogue = json.loads((FIXTURES / "catalogue_initial.json").read_text(encoding="utf-8"))
        salles = json.loads((FIXTURES / "salles_initiales.json").read_text(encoding="utf-8"))
        reference = datetime(2027, 3, 27, 12, tzinfo=FUSEAU)
        seances = generer_seances(
            [objet for objet in catalogue if objet["model"] == "cinema.film"],
            [objet for objet in salles if objet["model"] == "cinema.salle"],
            date(2027, 3, 27), 2,
        )
        with TemporaryDirectory() as dossier:
            chemin_seances = Path(dossier) / "seances.json"
            chemin_reservations = Path(dossier) / "reservations.json"
            chemin_seances.write_text(json.dumps(seances), encoding="utf-8")
            call_command("loaddata", str(chemin_seances), stdout=StringIO())
            with redirect_stdout(StringIO()):
                main(["--seances", str(chemin_seances), "--sortie", str(chemin_reservations),
                      "--date-reference", reference.isoformat(), "--attente-minutes", "30"])
            call_command("loaddata", str(chemin_reservations), stdout=StringIO())
            nombres = (Reservation.objects.count(), ReservationPlace.objects.count())
            call_command("loaddata", str(chemin_reservations), stdout=StringIO())
            self.assertEqual((Reservation.objects.count(), ReservationPlace.objects.count()), nombres)
        self.assertEqual(set(Reservation.objects.values_list("statut", flat=True)),
                         {"confirmee", "en_attente", "expiree", "annulee"})
        self.assertFalse(ReservationPlace.objects.exclude(place__salle_id=F("reservation__seance__salle_id")).exists())
        for reservation in Reservation.objects.prefetch_related("places"):
            self.assertEqual(reservation.montant_total,
                             sum((place.prix_unitaire for place in reservation.places.all()), Decimal(0)))
            self.assertLess(reservation.date_creation, reservation.date_expiration)
        libre = Seance.objects.get(pk=100000)
        partielle = Seance.objects.get(pk=100001)
        complete = Seance.objects.get(salle_id=9, debut__date=reference.date(), debut__hour=21)
        self.assertEqual(obtenir_disponibilites(libre, reference)["disponibles"], 300)
        occupation = obtenir_disponibilites(partielle, reference)
        self.assertGreater(occupation["total"] - occupation["disponibles"], 6)
        self.assertGreaterEqual(sum(any(place["statut"] == "occupee" for place in rangee["places"])
                                    for rangee in occupation["rangees"]), 4)
        for reservation in partielle.reservations.prefetch_related("places__place"):
            places = sorted((association.place for association in reservation.places.all()), key=lambda place: place.numero)
            self.assertIn(len(places), (2, 3))
            self.assertEqual(len({place.rangee for place in places}), 1)
            self.assertEqual([place.numero for place in places], list(range(places[0].numero, places[0].numero + len(places))))
            self.assertFalse(any(places[0].numero <= allee < places[-1].numero for allee in (5, 15)))
        for seance in Seance.objects.filter(debut__date=reference.date(), debut__hour=18).select_related("salle"):
            disponibilites = obtenir_disponibilites(seance, reference)
            self.assertGreater(disponibilites["total"] - disponibilites["disponibles"], 6)
            self.assertGreater(disponibilites["disponibles"], 0)
        apres_expiration = reference + timedelta(minutes=31)
        self.assertGreater(obtenir_disponibilites(partielle, apres_expiration)["disponibles"], occupation["disponibles"])
        self.assertLess(obtenir_disponibilites(partielle, apres_expiration)["disponibles"], occupation["total"])
        self.assertTrue(obtenir_disponibilites(complete, reference)["complet"])
        self.assertTrue(obtenir_disponibilites(complete, apres_expiration)["complet"])
        self.assertFalse(libre.reservations.exists())

        self.assertFalse(Reservation.objects.exclude(seance__debut__date=reference.date()).exists())
        self.assertTrue(Seance.objects.filter(debut__date=reference.date() + timedelta(days=1)).exists())
