import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from cinema.models import Film, Salle, Seance
from cinema.statistiques import obtenir_statistiques_affluence
from paiements.models import Paiement
from reservations.models import Reservation

from .test_statistiques import DonneesRemplissage


class DonneesAffluence(DonneesRemplissage):
    def analyser(self, **filtres):
        parametres = {"date_debut": date(2027, 3, 27), "date_fin": date(2027, 3, 29)}
        parametres.update(filtres)
        return obtenir_statistiques_affluence(**parametres)


@override_settings(TIME_ZONE="Europe/Paris")
class StatistiquesAffluenceTests(DonneesAffluence, TestCase):
    def test_confirmations_seules_et_jours_sans_reservation(self):
        resultat = self.analyser()
        periode = resultat["principale"]
        self.assertEqual(periode["jours"], [
            {"jour": date(2027, 3, 27), "places_reservees": 2},
            {"jour": date(2027, 3, 28), "places_reservees": 1},
            {"jour": date(2027, 3, 29), "places_reservees": 0},
        ])
        self.assertEqual(periode["total_places"], 3)
        self.assertEqual(periode["moyenne_jour"], Decimal("1.00"))
        self.assertIsNone(resultat["reference"])
        self.assertIsNone(resultat["variation"])

    def test_sieges_distincts_par_seance_et_paiements_sans_double_comptage(self):
        self.reserver(self.seance, [self.places[0]])
        Paiement.objects.bulk_create([
            Paiement(reservation=self.confirmee, montant=Decimal(24)),
            Paiement(reservation=self.confirmee, montant=Decimal(24)),
        ])
        autre = self.creer_seance(self.film, self.grande, self.debut + timedelta(hours=3))
        self.reserver(autre, [self.places[0]])
        periode = self.analyser()["principale"]
        self.assertEqual(periode["jours"][0]["places_reservees"], 3)
        self.assertEqual(periode["total_places"], 4)
        self.assertEqual(periode["classement_seances"][0].places_reservees, 2)

    def test_filtre_film_salle_et_combinaison(self):
        for filtres, total in (
            ({"film": self.autre_film}, 1), ({"salle": self.grande}, 2),
            ({"film": self.film, "salle": self.petite}, 0),
        ):
            with self.subTest(filtres=filtres):
                self.assertEqual(self.analyser(**filtres)["principale"]["total_places"], total)

    def test_dates_des_seances_et_non_dates_de_creation_des_reservations(self):
        periode = self.analyser(date_debut=self.debut.date(), date_fin=self.debut.date())["principale"]
        self.assertEqual(periode["total_places"], 2)
        self.assertEqual(periode["nombre_jours"], 1)

    def test_bornes_incluses_fuseau_local_et_changement_heure(self):
        jour = date(2027, 3, 28)
        for heure, minute in ((0, 15), (23, 45)):
            debut = timezone.make_aware(datetime(2027, 3, 28, heure, minute))
            seance = self.creer_seance(self.film, self.grande, debut)
            self.reserver(seance, [self.places[0]])
        hors_periode = self.creer_seance(
            self.film, self.petite, timezone.make_aware(datetime(2027, 3, 29, 0, 5)),
        )
        self.reserver(hors_periode, [self.autre_place])
        periode = self.analyser(date_debut=jour, date_fin=jour)["principale"]
        self.assertEqual(periode["jours"], [{"jour": jour, "places_reservees": 3}])

    def test_classements_et_indicateurs_avec_egalites(self):
        seconde = self.creer_seance(self.film, self.grande, self.debut + timedelta(hours=3))
        self.reserver(seconde, self.places[:2])
        self.reserver(self.sans_reservation, self.places[:2])
        periode = self.analyser()["principale"]
        self.assertEqual(periode["jour_plus_frequente"], {
            "jour": date(2027, 3, 27), "places_reservees": 4,
        })
        self.assertEqual(periode["seance_plus_demandee"].pk, self.seance.pk)
        self.assertEqual([s.pk for s in periode["classement_seances"]], [
            self.seance.pk, seconde.pk, self.sans_reservation.pk, self.autre_seance.pk,
        ])
        self.assertEqual([j["jour"] for j in periode["classement_jours"]], [
            date(2027, 3, 27), date(2027, 3, 29), date(2027, 3, 28),
        ])

    def test_classements_limites_a_dix_et_total_complet(self):
        for numero in range(12):
            seance = self.creer_seance(
                self.film, self.grande, self.debut + timedelta(days=10 + numero),
            )
            self.reserver(seance, [self.places[0]])
        periode = self.analyser(date_fin=date(2027, 4, 30))["principale"]
        self.assertEqual(len(periode["classement_jours"]), 10)
        self.assertEqual(len(periode["classement_seances"]), 10)
        self.assertEqual(periode["total_places"], 15)

    def test_sans_seance_ou_sans_confirmation(self):
        for jour in (date(2027, 3, 29), date(2030, 1, 1)):
            with self.subTest(jour=jour):
                periode = self.analyser(date_debut=jour, date_fin=jour)["principale"]
                self.assertEqual(periode["total_places"], 0)
                self.assertEqual(periode["jours"][0]["places_reservees"], 0)
                self.assertIsNone(periode["jour_plus_frequente"])
                self.assertIsNone(periode["seance_plus_demandee"])
                self.assertEqual(periode["classement_jours"], [])
                self.assertEqual(periode["classement_seances"], [])

    def test_historique_films_salles_places_inactifs_et_statuts_seances(self):
        Film.objects.filter(pk=self.film.pk).update(actif=False)
        Salle.objects.filter(pk=self.grande.pk).update(active=False)
        self.places[0].active = False
        self.places[0].save()
        for statut in (Seance.Statut.ANNULEE, Seance.Statut.TERMINEE):
            Seance.objects.filter(pk=self.seance.pk).update(statut=statut)
            self.assertEqual(self.analyser()["principale"]["total_places"], 3)

    def test_comparaison_totaux_variation_positive_negative_et_nulle(self):
        for debut, fin, reference, ecart, taux in (
            (date(2027, 3, 27), date(2027, 3, 27), 2, 1, Decimal("50.00")),
            (date(2027, 3, 28), date(2027, 3, 28), 1, 2, Decimal("200.00")),
            (date(2027, 3, 27), date(2027, 3, 29), 3, 0, Decimal("0.00")),
        ):
            resultat = self.analyser(comparaison_debut=debut, comparaison_fin=fin)
            self.assertEqual(resultat["reference"]["total_places"], reference)
            self.assertEqual(resultat["variation"], {"places": ecart, "pourcentage": taux})
        resultat = self.analyser(
            date_fin=date(2027, 3, 27),
            comparaison_debut=date(2027, 3, 27), comparaison_fin=date(2027, 3, 29),
        )
        self.assertEqual(resultat["variation"], {"places": -1, "pourcentage": Decimal("-33.33")})

    def test_reference_vide_et_deux_periodes_vides(self):
        resultat = self.analyser(
            comparaison_debut=date(2030, 1, 1), comparaison_fin=date(2030, 1, 1),
        )
        self.assertEqual(resultat["variation"], {"places": 3, "pourcentage": None})
        resultat = self.analyser(
            date_debut=date(2030, 1, 1), date_fin=date(2030, 1, 1),
            comparaison_debut=date(2030, 1, 2), comparaison_fin=date(2030, 1, 2),
        )
        self.assertEqual(resultat["variation"], {"places": 0, "pourcentage": Decimal("0.00")})

    def test_durees_differentes_et_moyennes_par_jour(self):
        resultat = self.analyser(
            comparaison_debut=date(2027, 3, 27), comparaison_fin=date(2027, 3, 27),
        )
        self.assertEqual(resultat["principale"]["nombre_jours"], 3)
        self.assertEqual(resultat["reference"]["nombre_jours"], 1)
        self.assertEqual(resultat["reference"]["moyenne_jour"], Decimal("2.00"))
        self.assertEqual(resultat["principale"]["moyenne_jour"], Decimal("1.00"))

    def test_filtres_communs_aux_deux_periodes(self):
        resultat = self.analyser(
            salle=self.petite, comparaison_debut=date(2027, 3, 27),
            comparaison_fin=date(2027, 3, 29),
        )
        self.assertEqual(resultat["principale"]["total_places"], 1)
        self.assertEqual(resultat["reference"]["total_places"], 1)

    def test_periodes_invalides_refusees_avant_requete(self):
        for filtres in (
            {"date_debut": None}, {"date_fin": None},
            {"date_fin": date(2027, 3, 26)}, {"date_fin": date(2028, 3, 27)},
            {"comparaison_debut": date(2027, 1, 1)},
            {"comparaison_fin": date(2027, 1, 1)},
            {"comparaison_debut": date(2027, 2, 2), "comparaison_fin": date(2027, 2, 1)},
            {"comparaison_debut": date(2027, 1, 1), "comparaison_fin": date(2028, 2, 1)},
        ):
            with self.subTest(filtres=filtres), self.assertNumQueries(0), self.assertRaises(ValidationError):
                self.analyser(**filtres)

    def test_annee_bissextile_complete_acceptee(self):
        periode = self.analyser(
            date_debut=date(2028, 1, 1), date_fin=date(2028, 12, 31),
        )["principale"]
        self.assertEqual(periode["nombre_jours"], 366)
        self.assertEqual(periode["jours"][-1]["jour"], date(2028, 12, 31))

    def test_deux_requetes_par_periode_independamment_du_nombre_de_seances(self):
        for numero in range(20):
            self.creer_seance(self.film, self.grande, self.debut + timedelta(days=10 + numero))
        with self.assertNumQueries(2):
            resultat = self.analyser(date_fin=date(2027, 4, 30))
            for seance in resultat["principale"]["classement_seances"]:
                self.assertTrue(seance.film.titre)
                self.assertTrue(seance.salle.nom)
        with self.assertNumQueries(4):
            self.analyser(comparaison_debut=date(2027, 3, 27), comparaison_fin=date(2027, 3, 29))

    def test_lecture_seule_et_actualisation_des_confirmations(self):
        avant = self.etat_metier()
        self.analyser()
        self.assertEqual(self.etat_metier(), avant)
        self.confirmee.statut = Reservation.Statut.ANNULEE
        self.confirmee.save(update_fields=["statut"])
        self.assertEqual(self.analyser()["principale"]["total_places"], 1)


@override_settings(TIME_ZONE="Europe/Paris", SECURE_SSL_REDIRECT=False)
class AdminAffluenceTests(DonneesAffluence, TestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("admin:cinema_seance_affluence")
        self.filtres = {"date_debut": "2027-03-27", "date_fin": "2027-03-29"}
        self.personnel = get_user_model().objects.create_user(username="stat-affluence", is_staff=True)
        self.permissions = list(Permission.objects.filter(
            content_type__app_label="cinema", codename="view_seance",
        )) + list(Permission.objects.filter(
            content_type__app_label="reservations", codename="view_reservation",
        ))
        self.personnel.user_permissions.add(*self.permissions)
        self.client.force_login(self.personnel)

    def test_page_graphiques_classements_et_navigation(self):
        page = self.client.get(self.url, self.filtres)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Jour le plus fréquenté")
        self.assertContains(page, "Les 10 séances les plus demandées")
        self.assertContains(page, 'data-affluence="principale"')
        self.assertContains(page, "affluence-donnees")
        self.assertContains(page, "cinema/vendor/chartjs/chart.umd.js")
        self.assertContains(page, "cinema/js/affluence.js")
        self.assertContains(page, reverse("admin:cinema_seance_statistiques"))
        for url in (reverse("admin:cinema_seance_changelist"), reverse("admin:cinema_seance_statistiques")):
            self.assertContains(self.client.get(url), self.url)
        self.assertIn("no-store", page.headers["Cache-Control"])

    def test_defaut_trente_derniers_jours(self):
        with patch("cinema.admin.timezone.localdate", return_value=date(2027, 3, 29)):
            page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["principale"]["date_debut"], date(2027, 2, 28))
        self.assertEqual(page.context["principale"]["date_fin"], date(2027, 3, 29))
        self.assertEqual(page.context["principale"]["nombre_jours"], 30)

    def test_filtres_et_comparaison_avec_deux_graphiques(self):
        page = self.client.get(self.url, {
            **self.filtres, "film": self.film.pk, "salle": self.grande.pk,
            "comparaison_debut": "2027-03-27", "comparaison_fin": "2027-03-27",
        })
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["principale"]["total_places"], 2)
        self.assertEqual(page.context["reference"]["total_places"], 2)
        self.assertContains(page, "Comparaison des périodes")
        self.assertContains(page, 'data-affluence="reference"')

    def test_donnees_json_dates_iso_et_entiers(self):
        page = self.client.get(self.url, self.filtres)
        bloc = re.search(
            r'<script id="affluence-donnees" type="application/json">(.*?)</script>',
            page.content.decode(), re.DOTALL,
        )
        donnees = json.loads(bloc.group(1))
        self.assertEqual(donnees["principale"][0], {"jour": "2027-03-27", "places_reservees": 2})
        self.assertEqual(donnees["principale"][-1]["places_reservees"], 0)
        self.assertEqual(donnees["reference"], [])

    def test_sans_confirmation_et_sans_seance_message_explicite(self):
        for jour in ("2027-03-29", "2030-01-01"):
            page = self.client.get(self.url, {"date_debut": jour, "date_fin": jour})
            self.assertContains(page, "Aucune donnée disponible")
            self.assertNotContains(page, 'data-affluence="principale"')
            self.assertIsNone(page.context["principale"]["jour_plus_frequente"])

    def test_reference_vide_variation_non_calculable(self):
        page = self.client.get(self.url, {
            **self.filtres, "comparaison_debut": "2030-01-01", "comparaison_fin": "2030-01-01",
        })
        self.assertContains(page, "non calculable")
        self.assertContains(page, "Aucune donnée disponible")

    def test_dates_et_choix_invalides_refuses_sans_statistiques(self):
        for filtres in (
            {"date_debut": ""}, {"date_fin": ""}, {"date_debut": "incorrect"},
            {"date_fin": "2027-03-26"}, {"date_fin": "2028-03-27"},
            {"comparaison_debut": "2027-03-01"}, {"comparaison_fin": "2027-03-01"},
            {"comparaison_debut": "incorrect", "comparaison_fin": "2027-03-01"},
            {"comparaison_debut": "2027-03-02", "comparaison_fin": "2027-03-01"},
            {"comparaison_debut": "2027-01-01", "comparaison_fin": "2028-02-01"},
            {"film": "999999"}, {"salle": "999999"},
        ):
            with self.subTest(filtres=filtres):
                page = self.client.get(self.url, {**self.filtres, **filtres})
                self.assertEqual(page.status_code, 400)
                self.assertNotIn("principale", page.context)
                self.assertContains(page, "Corrigez les filtres", status_code=400)
        self.assertEqual(self.client.get(self.url, {"date_debut": "2027-03-27"}).status_code, 400)

    def test_anonyme_redirige_vers_connexion(self):
        self.client.logout()
        page = self.client.get(self.url, self.filtres)
        self.assertEqual(page.status_code, 302)
        self.assertIn(reverse("admin:login"), page.url)

    def test_les_deux_permissions_sont_necessaires(self):
        for permissions in ([], self.permissions[:1], self.permissions[1:]):
            self.personnel.user_permissions.set(permissions)
            page = self.client.get(self.url, self.filtres)
            self.assertEqual(page.status_code, 403)
        self.personnel.user_permissions.set(self.permissions[:1])
        page = self.client.get(reverse("admin:cinema_seance_changelist"))
        self.assertNotContains(page, self.url)

    def test_permissions_change_seules_insuffisantes(self):
        self.personnel.user_permissions.set(Permission.objects.filter(
            codename__in=("change_seance", "change_reservation"),
        ))
        self.assertEqual(self.client.get(self.url, self.filtres).status_code, 403)

    def test_non_personnel_et_inactif_refuses(self):
        for actif, personnel in ((True, False), (False, True)):
            utilisateur = get_user_model().objects.create_user(
                username=f"affluence-{actif}-{personnel}", is_active=actif, is_staff=personnel,
            )
            utilisateur.user_permissions.add(*self.permissions)
            self.client.force_login(utilisateur)
            self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_superutilisateur_autorise(self):
        utilisateur = get_user_model().objects.create_superuser(
            username="admin-affluence", email="admin@example.com", password=None,
        )
        self.client.force_login(utilisateur)
        self.assertEqual(self.client.get(self.url, self.filtres).status_code, 200)

    def test_get_head_seulement_et_aucune_modification_metier(self):
        avant = self.etat_metier()
        self.assertEqual(self.client.head(self.url, self.filtres).status_code, 200)
        for methode in ("post", "put", "patch", "delete"):
            self.assertEqual(getattr(self.client, methode)(self.url).status_code, 405)
        self.assertEqual(self.etat_metier(), avant)

    def test_titres_et_salles_echappes(self):
        texte = '<script>alert("affluence")</script>'
        Film.objects.filter(pk=self.film.pk).update(titre=texte)
        Salle.objects.filter(pk=self.grande.pk).update(nom=texte)
        page = self.client.get(self.url, self.filtres)
        self.assertNotContains(page, texte)
        self.assertContains(page, "&lt;script&gt;")

    def test_nombre_de_requetes_constant_quand_le_nombre_de_seances_augmente(self):
        with CaptureQueriesContext(connection) as avant:
            self.assertEqual(self.client.get(self.url, self.filtres).status_code, 200)
        for numero in range(10):
            self.creer_seance(self.film, self.grande, self.debut + timedelta(hours=3 * (numero + 1)))
        with CaptureQueriesContext(connection) as apres:
            self.assertEqual(self.client.get(self.url, self.filtres).status_code, 200)
        self.assertEqual(len(avant), len(apres))
