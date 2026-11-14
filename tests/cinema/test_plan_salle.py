from django.test import SimpleTestCase

from cinema.plan_salle import organiser_plan


class PlanSalleTests(SimpleTestCase):
    def rangees(self, capacite):
        rangees = []
        for index in range(capacite):
            if index % 20 == 0:
                rangees.append({"nom": chr(65 + index // 20), "places": []})
            rangees[-1]["places"].append({
                "numero": index % 20 + 1, "statut": "disponible", "libelle": "Disponible",
            })
        return rangees

    def test_salle_standard_180_places_en_5_10_5_et_2_5_2(self):
        plan = organiser_plan(self.rangees(180))
        self.assertEqual(len(plan), 9)
        self.assertEqual([r["nom"] for r in plan if r["separation_avant"]], ["C", "H"])
        for rangee in plan:
            self.assertEqual([p["numero"] for p in rangee["places"] if p["allee_avant"]], [6, 16])
            self.assertEqual([p["numero"] for p in rangee["places"]], list(range(1, 21)))

    def test_adaptation_des_capacites_sans_perdre_de_places(self):
        for capacite, nombre_rangees, separations in (
            (300, 15, [3, 12]),
            (180, 9, [2, 7]),
            (120, 6, [1, 5]),
            (70, 4, [1, 3]),
        ):
            with self.subTest(capacite=capacite):
                original = self.rangees(capacite)
                plan = organiser_plan(original)
                self.assertEqual(len(plan), nombre_rangees)
                self.assertEqual(sum(len(r["places"]) for r in plan), capacite)
                self.assertEqual([i for i, r in enumerate(plan) if r["separation_avant"]], separations)
                self.assertEqual(
                    [(r["nom"], p["numero"], p["statut"]) for r in original for p in r["places"]],
                    [(r["nom"], p["numero"], p["statut"]) for r in plan for p in r["places"]],
                )
                self.assertNotIn("allee_avant", original[0]["places"][0])

    def test_derniere_rangee_de_dix_places_centree_sans_allee_interne(self):
        derniere = organiser_plan(self.rangees(70))[-1]
        self.assertEqual(len(derniere["places"]), 10)
        self.assertFalse(any(p["allee_avant"] for p in derniere["places"]))

    def test_petites_salles_et_plan_vide(self):
        self.assertEqual(organiser_plan([]), [])
        for capacite in (1, 10, 20, 40):
            with self.subTest(capacite=capacite):
                plan = organiser_plan(self.rangees(capacite))
                self.assertFalse(any(r["separation_avant"] for r in plan))
                self.assertEqual(sum(len(r["places"]) for r in plan), capacite)
