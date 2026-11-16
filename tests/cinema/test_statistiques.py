from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import Q
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from cinema.models import Film, Place, Salle, Seance
from cinema.statistiques import obtenir_statistiques_remplissage
from paiements.models import Paiement
from reservations.models import Reservation, ReservationPlace


class DonneesRemplissage:
    def setUp(self):
        super().setUp()
        self.film = Film.objects.create(titre="Le Voyage", duree=90)
        self.autre_film = Film.objects.create(titre="Les Retrouvailles", duree=90)
        self.grande = Salle.objects.create(nom="Grande salle", capacite=10)
        self.petite = Salle.objects.create(nom="Petite salle", capacite=2)
        self.places = [
            Place.objects.create(salle=self.grande, rangee="A", numero=numero)
            for numero in (1, 2, 3)
        ]
        self.autre_place = Place.objects.create(salle=self.petite, rangee="B", numero=1)
        self.debut = timezone.make_aware(datetime(2027, 3, 27, 14))
        self.seance = self.creer_seance(self.film, self.grande, self.debut)
        self.autre_seance = self.creer_seance(
            self.autre_film, self.petite, self.debut + timedelta(days=1),
        )
        self.sans_reservation = self.creer_seance(
            self.film, self.grande, self.debut + timedelta(days=2),
        )
        self.confirmee = self.reserver(self.seance, self.places[:2])
        self.reserver(self.autre_seance, [self.autre_place])
        for statut in (
            Reservation.Statut.EN_ATTENTE, Reservation.Statut.EXPIREE, Reservation.Statut.ANNULEE,
        ):
            self.reserver(self.seance, [self.places[2]], statut)

    def creer_seance(self, film, salle, debut, statut=Seance.Statut.PROGRAMMEE):
        return Seance.objects.create(
            film=film, salle=salle, debut=debut, fin=debut + timedelta(minutes=90),
            prix=Decimal("12.00"), statut=statut,
        )

    def reserver(self, seance, places, statut=Reservation.Statut.CONFIRMEE):
        reservation = Reservation.objects.create(
            seance=seance, email="spectateur-statistiques@example.com", statut=statut,
            date_expiration=self.debut, montant_total=Decimal("12.00") * len(places),
        )
        ReservationPlace.objects.bulk_create([
            ReservationPlace(reservation=reservation, place=place, prix_unitaire=Decimal("12.00"))
            for place in places
        ])
        return reservation

    def etat_metier(self):
        return {
            modele._meta.label: list(modele.objects.order_by("pk").values())
            for modele in (Seance, Reservation, ReservationPlace, Paiement)
        }


@override_settings(TIME_ZONE="Europe/Paris")
class StatistiquesRemplissageTests(DonneesRemplissage, TestCase):
    def test_seuls_sieges_confirmes_sont_comptes_et_seances_vides_conservees(self):
        resultat = obtenir_statistiques_remplissage()
        seances = {seance.pk: seance for seance in resultat["seances"]}
        self.assertEqual(set(seances), {
            self.seance.pk, self.autre_seance.pk, self.sans_reservation.pk,
        })
        self.assertEqual(seances[self.seance.pk].places_reservees, 2)
        self.assertEqual(seances[self.seance.pk].capacite, 10)
        self.assertEqual(seances[self.seance.pk].taux_remplissage, Decimal("20.00"))
        self.assertEqual(seances[self.autre_seance.pk].taux_remplissage, Decimal("50.00"))
        self.assertEqual(seances[self.sans_reservation.pk].places_reservees, 0)
        self.assertEqual(seances[self.sans_reservation.pk].taux_remplissage, Decimal("0.00"))

    def test_synthese_ponderee_et_non_moyenne_des_pourcentages(self):
        resultat = obtenir_statistiques_remplissage(
            date_debut=date(2027, 3, 27), date_fin=date(2027, 3, 28),
        )
        self.assertEqual(resultat["synthese"], {
            "nombre_seances": 2, "places_reservees": 3,
            "capacite_totale": 12, "taux_global": Decimal("25.00"),
        })

    def test_capacite_comptee_par_seance_meme_pour_deux_salles_identiques(self):
        resultat = obtenir_statistiques_remplissage(film=self.film, salle=self.grande)
        self.assertEqual(resultat["synthese"], {
            "nombre_seances": 2, "places_reservees": 2,
            "capacite_totale": 20, "taux_global": Decimal("10.00"),
        })

    def test_un_siege_double_et_des_paiements_multiples_ne_gonflent_pas_les_chiffres(self):
        self.reserver(self.seance, [self.places[0]])
        for identifiant in ("session-un", "session-deux"):
            Paiement.objects.create(
                reservation=self.confirmee, montant=Decimal("24.00"),
                stripe_session_id=identifiant, statut=Paiement.Statut.REUSSI,
            )
        resultat = obtenir_statistiques_remplissage()
        self.assertEqual(resultat["synthese"]["places_reservees"], 3)
        self.assertEqual(resultat["synthese"]["capacite_totale"], 22)
        self.assertEqual(resultat["synthese"]["taux_global"], Decimal("13.64"))

    def test_meme_siege_compte_pour_deux_seances_differentes(self):
        self.reserver(self.sans_reservation, [self.places[0]])
        resultat = obtenir_statistiques_remplissage(salle=self.grande)
        self.assertEqual(resultat["synthese"]["places_reservees"], 3)
        self.assertEqual(resultat["synthese"]["taux_global"], Decimal("15.00"))

    def test_capacite_declaree_et_places_confirmees_conservees_si_siege_desactive(self):
        Place.objects.filter(pk=self.places[0].pk).update(active=False)
        resultat = obtenir_statistiques_remplissage(date_fin=date(2027, 3, 27))
        seance = resultat["seances"].get()
        self.assertEqual(seance.places_reservees, 2)
        self.assertEqual(seance.capacite, 10)
        self.assertEqual(seance.taux_remplissage, Decimal("20.00"))

    def test_filtres_film_salle_periode_seuls_et_combines(self):
        cas = (
            ({"film": self.autre_film}, {self.autre_seance.pk}),
            ({"salle": self.petite}, {self.autre_seance.pk}),
            ({"date_debut": date(2027, 3, 28)}, {
                self.autre_seance.pk, self.sans_reservation.pk,
            }),
            ({"date_fin": date(2027, 3, 27)}, {self.seance.pk}),
            ({"film": self.film, "salle": self.grande,
              "date_debut": date(2027, 3, 27), "date_fin": date(2027, 3, 27)}, {self.seance.pk}),
        )
        for filtres, attendus in cas:
            with self.subTest(filtres=filtres):
                resultat = obtenir_statistiques_remplissage(**filtres)
                self.assertEqual({seance.pk for seance in resultat["seances"]}, attendus)

    def test_bornes_inclusives_en_heure_locale_lors_du_changement_d_heure(self):
        avant = self.creer_seance(
            self.film, self.grande, timezone.make_aware(datetime(2027, 3, 27, 22)),
        )
        premier = self.creer_seance(
            self.film, self.grande, timezone.make_aware(datetime(2027, 3, 28, 0)),
        )
        dernier = self.creer_seance(
            self.film, self.grande, timezone.make_aware(datetime(2027, 3, 28, 22)),
        )
        apres = self.creer_seance(
            self.autre_film, self.petite, timezone.make_aware(datetime(2027, 3, 29, 0)),
        )
        resultat = obtenir_statistiques_remplissage(
            date_debut=date(2027, 3, 28), date_fin=date(2027, 3, 28),
        )
        ids = {seance.pk for seance in resultat["seances"]}
        self.assertEqual(ids, {premier.pk, dernier.pk, self.autre_seance.pk})
        self.assertNotIn(avant.pk, ids)
        self.assertNotIn(apres.pk, ids)

    def test_periode_inversee_refusee_par_service(self):
        with self.assertRaisesMessage(ValidationError, "date de fin"):
            obtenir_statistiques_remplissage(
                date_debut=date(2027, 3, 29), date_fin=date(2027, 3, 27),
            )

    def test_aucune_donnee_ne_provoque_pas_division_par_zero(self):
        resultat = obtenir_statistiques_remplissage(film=self.film, salle=self.petite)
        self.assertEqual(list(resultat["seances"]), [])
        self.assertEqual(resultat["synthese"], {
            "nombre_seances": 0, "places_reservees": 0,
            "capacite_totale": 0, "taux_global": Decimal("0.00"),
        })

    def test_historique_des_films_salles_et_statuts_de_seance_reste_consultable(self):
        Film.objects.filter(pk=self.film.pk).update(actif=False)
        Salle.objects.filter(pk=self.grande.pk).update(active=False)
        Seance.objects.filter(pk=self.seance.pk).update(statut=Seance.Statut.TERMINEE)
        Seance.objects.filter(pk=self.sans_reservation.pk).update(statut=Seance.Statut.ANNULEE)
        resultat = obtenir_statistiques_remplissage(film=self.film, salle=self.grande)
        self.assertEqual({seance.pk for seance in resultat["seances"]}, {
            self.seance.pk, self.sans_reservation.pk,
        })

    def test_arrondi_des_taux_a_deux_decimales(self):
        Salle.objects.filter(pk=self.grande.pk).update(capacite=3)
        resultat = obtenir_statistiques_remplissage(date_fin=date(2027, 3, 27))
        self.assertEqual(resultat["seances"].get().taux_remplissage, Decimal("66.67"))
        self.assertEqual(resultat["synthese"]["taux_global"], Decimal("66.67"))

    def test_nombre_constant_de_requetes_et_relations_deja_chargees(self):
        for numero in range(20):
            self.creer_seance(
                self.film, self.grande, self.debut + timedelta(days=10 + numero),
            )
        with self.assertNumQueries(2):
            resultat = obtenir_statistiques_remplissage()
            seances = list(resultat["seances"])
            libelles = [
                (seance.film.titre, seance.salle.nom, seance.taux_remplissage)
                for seance in seances
            ]
        self.assertEqual(len(libelles), 23)
        self.assertEqual(resultat["synthese"]["nombre_seances"], 23)

    def test_consultation_actualisee_sans_modifier_les_donnees(self):
        avant = self.etat_metier()
        obtenir_statistiques_remplissage()
        self.assertEqual(self.etat_metier(), avant)
        Reservation.objects.filter(pk=self.confirmee.pk).update(statut=Reservation.Statut.ANNULEE)
        self.assertEqual(obtenir_statistiques_remplissage()["synthese"]["places_reservees"], 1)


@override_settings(TIME_ZONE="Europe/Paris", SECURE_SSL_REDIRECT=False)
class StatistiquesRemplissageAdminTests(DonneesRemplissage, TestCase):
    def setUp(self):
        super().setUp()
        self.personnel = get_user_model().objects.create_user(username="statistiques", is_staff=True)
        self.permissions = Permission.objects.filter(
            Q(content_type__app_label="cinema", codename="view_seance")
            | Q(content_type__app_label="reservations", codename="view_reservation"),
        )
        self.personnel.user_permissions.add(*self.permissions)
        self.client.force_login(self.personnel)
        self.url = reverse("admin:cinema_seance_statistiques")
        self.liste = reverse("admin:cinema_seance_changelist")

    def test_page_accessible_depuis_liste_seances_et_interface_admin(self):
        self.assertContains(self.client.get(self.liste), self.url)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertTemplateUsed(page, "admin/cinema/seance/statistiques.html")
        for texte in (
            "Taux de remplissage des séances", "Le Voyage", "Grande salle", "Petite salle",
            "Places réservées", "Capacité", "20,00", "50,00", "13,64", "27/03/2027 14:00",
        ):
            self.assertContains(page, texte)
        self.assertEqual(page.context["synthese"]["nombre_seances"], 3)
        self.assertNotContains(page, self.confirmee.email)
        self.assertIn("no-store", page["Cache-Control"])

    def test_filtres_combines_et_synthese_correspondante(self):
        page = self.client.get(self.url, {
            "film": self.film.pk, "salle": self.grande.pk,
            "date_debut": "2027-03-27", "date_fin": "2027-03-27",
        })
        self.assertEqual(page.status_code, 200)
        self.assertEqual([seance.pk for seance in page.context["page_obj"]], [self.seance.pk])
        self.assertEqual(page.context["synthese"]["taux_global"], Decimal("20.00"))
        self.assertContains(page, 'value="2027-03-27"')
        self.assertNotContains(page, "Les Retrouvailles</td>")

    def test_aucune_seance_message_et_synthese_nulle(self):
        page = self.client.get(self.url, {"film": self.film.pk, "salle": self.petite.pk})
        self.assertContains(page, "Aucune séance ne correspond aux filtres sélectionnés.")
        self.assertEqual(page.context["synthese"]["nombre_seances"], 0)
        self.assertEqual(page.context["synthese"]["taux_global"], Decimal("0.00"))

    def test_dates_invalides_et_periode_inversee_affichent_erreur(self):
        for filtres in (
            {"date_debut": "date-invalide"},
            {"date_fin": "2027-02-30"},
            {"date_debut": "2027-03-29", "date_fin": "2027-03-27"},
        ):
            with self.subTest(filtres=filtres):
                page = self.client.get(self.url, filtres)
                self.assertEqual(page.status_code, 400)
                self.assertTrue(page.context["formulaire"].errors)
                self.assertNotIn("synthese", page.context)
                self.assertNotContains(page, 'id="result_list"', status_code=400)

    def test_film_ou_salle_inexistant_refuses(self):
        for filtres in ({"film": 999999}, {"salle": 999999}, {"film": "invalide"}):
            with self.subTest(filtres=filtres):
                page = self.client.get(self.url, filtres)
                self.assertEqual(page.status_code, 400)
                self.assertTrue(page.context["formulaire"].errors)
                self.assertNotIn("synthese", page.context)

    def test_anonyme_redirige_vers_connexion_avec_url_de_retour(self):
        self.client.logout()
        self.assertRedirects(
            self.client.get(self.url), reverse("admin:login") + "?next=" + self.url,
            fetch_redirect_response=False,
        )

    def test_personnel_sans_les_deux_permissions_refuse_et_lien_masque(self):
        for code in (None, "view_seance", "view_reservation", "change_seance"):
            utilisateur = get_user_model().objects.create_user(
                username=f"personnel-{code}", is_staff=True,
            )
            if code:
                application = "reservations" if code == "view_reservation" else "cinema"
                utilisateur.user_permissions.add(Permission.objects.get(
                    content_type__app_label=application, codename=code,
                ))
            self.client.force_login(utilisateur)
            with self.subTest(code=code):
                self.assertEqual(self.client.get(self.url).status_code, 403)
                if code in ("view_seance", "change_seance"):
                    self.assertNotContains(self.client.get(self.liste), self.url)

    def test_non_personnel_et_compte_inactif_refuses_meme_avec_permissions(self):
        for actif, personnel in ((True, False), (False, True)):
            utilisateur = get_user_model().objects.create_user(
                username=f"compte-{actif}-{personnel}", is_active=actif, is_staff=personnel,
            )
            utilisateur.user_permissions.add(*self.permissions)
            self.client.force_login(utilisateur)
            self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_superutilisateur_autorise(self):
        utilisateur = get_user_model().objects.create_superuser(
            username="admin-statistiques", email="admin@example.com", password=None,
        )
        self.client.force_login(utilisateur)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_get_head_et_refus_des_methodes_ecriture_sans_effet_metier(self):
        avant = self.etat_metier()
        self.assertEqual(self.client.head(self.url).status_code, 200)
        for methode in ("post", "put", "delete"):
            with self.subTest(methode=methode):
                self.assertEqual(getattr(self.client, methode)(self.url).status_code, 405)
        self.assertEqual(self.etat_metier(), avant)

    def test_titres_et_noms_de_salle_echappes(self):
        texte = '<script>alert("statistiques")</script>'
        Film.objects.filter(pk=self.film.pk).update(titre=texte)
        Salle.objects.filter(pk=self.grande.pk).update(nom=texte)
        page = self.client.get(self.url)
        self.assertNotContains(page, texte)
        self.assertContains(page, "&lt;script&gt;")

    def test_pagination_conserve_filtres_et_synthese_sur_tous_les_resultats(self):
        for numero in range(50):
            self.creer_seance(
                self.film, self.grande, self.debut + timedelta(days=10 + numero),
            )
        page = self.client.get(self.url, {
            "film": self.film.pk, "salle": self.grande.pk, "page": 2,
        })
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["page_obj"].number, 2)
        self.assertEqual(len(page.context["page_obj"]), 2)
        self.assertEqual(page.context["synthese"]["nombre_seances"], 52)
        self.assertEqual(page.context["synthese"]["places_reservees"], 2)
        self.assertEqual(page.context["synthese"]["capacite_totale"], 520)
        self.assertEqual(page.context["synthese"]["taux_global"], Decimal("0.38"))
        self.assertContains(
            page, f"?film={self.film.pk}&amp;salle={self.grande.pk}&amp;page=1",
        )

    def test_nombre_de_requetes_page_constant_quand_nombre_seances_augmente(self):
        with CaptureQueriesContext(connection) as avant:
            self.assertEqual(self.client.get(self.url).status_code, 200)
        for numero in range(10):
            self.creer_seance(
                self.film, self.grande, self.debut + timedelta(days=10 + numero),
            )
        with CaptureQueriesContext(connection) as apres:
            page = self.client.get(self.url)
            self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["synthese"]["nombre_seances"], 13)
        self.assertEqual(len(apres), len(avant))
