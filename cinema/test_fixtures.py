from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .models import Place, Salle


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
