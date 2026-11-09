from datetime import date, datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cinema.models import Film, Genre, Participation, Personne, Salle, Seance


class DetailFilmTests(TestCase):
    def setUp(self):
        self.film = Film.objects.create(
            titre="Film à découvrir", duree=105, description="Un voyage au cinéma.\nUne nouvelle aventure.",
            date_sortie=date(2026, 1, 21), affiche="affiches/mon film.webp",
        )
        self.url = reverse("cinema:detail_film", args=[self.film.pk])

    def test_fiche_publique_avec_informations_sans_seance(self):
        genre = Genre.objects.create(nom="Aventure")
        self.film.genres.add(genre)
        personne = Personne.objects.create(prenom="Camille", nom="Dupont")
        for fonction in Participation.Fonction.values:
            Participation.objects.create(film=self.film, personne=personne, fonction=fonction)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "cinema/detail_film.html")
        self.assertTemplateUsed(response, "cinema/base.html")
        self.assertContains(response, self.film.titre)
        self.assertContains(response, "105 min")
        self.assertContains(response, "21/01/2026")
        self.assertContains(response, "Aventure")
        self.assertContains(response, "Camille Dupont", count=2)
        self.assertContains(response, "Un voyage au cinéma.<br>Une nouvelle aventure.")
        self.assertEqual(response.context["realisateurs"], [personne])
        self.assertEqual(response.context["acteurs"], [personne])

    def test_repartition_et_tri_des_acteurs_et_realisateurs(self):
        realisateur = Personne.objects.create(prenom="Sam", nom="Durand")
        acteur_z = Personne.objects.create(prenom="Alex", nom="Zola")
        acteur_a = Personne.objects.create(prenom="Chris", nom="Albert")
        Participation.objects.create(film=self.film, personne=realisateur, fonction="realisateur")
        for personne in (acteur_z, acteur_a):
            Participation.objects.create(film=self.film, personne=personne, fonction="acteur")
        response = self.client.get(self.url)
        self.assertEqual(response.context["realisateurs"], [realisateur])
        self.assertEqual(response.context["acteurs"], [acteur_a, acteur_z])

    def test_film_inexistant_et_retire_non_accessibles(self):
        self.assertEqual(self.client.get(reverse("cinema:detail_film", args=[self.film.pk + 1])).status_code, 404)
        self.film.actif = False
        self.film.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_informations_facultatives_absentes(self):
        self.film.affiche = ""
        self.film.description = ""
        self.film.date_sortie = None
        self.film.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        for texte in (
            "Affiche non disponible", "Genres non renseignés.", "Réalisation non renseignée.",
            "Acteurs non renseignés.", "Synopsis non renseigné.",
        ):
            self.assertContains(response, texte)
        self.assertNotContains(response, "Date de sortie")

    @override_settings(MEDIA_URL="/medias-test/")
    def test_affiche_et_lien_de_retour(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'src="/medias-test/affiches/mon%20film.webp"')
        self.assertContains(response, 'alt="Affiche du film Film à découvrir"')
        self.assertContains(response, "Retour à la programmation")
        self.assertContains(response, f'href="{reverse("cinema:films")}"')

    def test_liens_vers_fiche_depuis_affiche_et_titre(self):
        salle = Salle.objects.create(nom="Salle 1", capacite=20)
        debut = timezone.now() + timedelta(days=1)
        Seance.objects.create(
            film=self.film, salle=salle, debut=debut,
            fin=debut + timedelta(minutes=105), prix=10,
        )
        response = self.client.get(reverse("cinema:films"), {"date": timezone.localdate(debut).isoformat()})
        self.assertContains(response, f'href="{self.url}?date={timezone.localdate(debut).isoformat()}"', count=2)

    def test_contenus_saisis_non_interpretes_comme_html(self):
        self.film.titre = "<script>alert('titre')</script>"
        self.film.description = "<script>alert('synopsis')</script>"
        self.film.save()
        personne = Personne.objects.create(nom="<script>alert('nom')</script>")
        Participation.objects.create(film=self.film, personne=personne, fonction="acteur")
        response = self.client.get(self.url)
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>")

    def test_chargement_equipe_sans_requete_par_personne(self):
        for index in range(5):
            personne = Personne.objects.create(nom=f"Personne {index}")
            Participation.objects.create(film=self.film, personne=personne, fonction="acteur")
        self.film.genres.add(Genre.objects.create(nom="Drame"))
        with self.assertNumQueries(5):
            self.client.get(self.url)


@override_settings(TIME_ZONE="Europe/Paris")
class DetailFilmSeancesTests(TestCase):
    def setUp(self):
        self.maintenant = datetime(2030, 1, 7, 9, tzinfo=ZoneInfo("Europe/Paris"))
        self.aujourd_hui = self.maintenant.date()
        horloge = patch("cinema.views.timezone.now", return_value=self.maintenant)
        horloge.start()
        self.addCleanup(horloge.stop)
        self.film = Film.objects.create(titre="Film", duree=30)
        self.salle = Salle.objects.create(nom="Salle A", capacite=20)
        self.url = reverse("cinema:detail_film", args=[self.film.pk])

    def programmer(self, jours=0, heure=12, minute=0, film=None, salle=None, statut=Seance.Statut.PROGRAMMEE):
        debut = timezone.make_aware(datetime.combine(
            self.aujourd_hui + timedelta(days=jours), time(heure, minute),
        ))
        return Seance.objects.create(
            film=film or self.film, salle=salle or self.salle, debut=debut,
            fin=debut + timedelta(minutes=30), prix=10, statut=statut,
        )

    def consulter(self, jours=0, **parametres):
        parametres.setdefault("date", (self.aujourd_hui + timedelta(days=jours)).isoformat())
        response = self.client.get(self.url, parametres)
        self.assertEqual(response.status_code, 200)
        return response

    def seances_affichees(self, response):
        return [
            seance for groupe in response.context["seances_par_jour"] for seance in groupe["seances"]
        ] + response.context["seances_ulterieures"]

    def test_horaires_groupes_par_jour_et_salles_sans_compte(self):
        tard = self.programmer(heure=20)
        tot = self.programmer(heure=14)
        salle_b = Salle.objects.create(nom="Salle B", capacite=20)
        simultanee = self.programmer(heure=14, salle=salle_b)
        demain = self.programmer(jours=1)
        response = self.consulter()
        self.assertEqual(self.seances_affichees(response), [tot, simultanee, tard, demain])
        self.assertEqual([g["jour"] for g in response.context["seances_par_jour"]], [
            self.aujourd_hui, self.aujourd_hui + timedelta(days=1),
        ])
        self.assertContains(response, "Salle A")
        self.assertContains(response, "Salle B")
        self.assertContains(response, ">14:00</time>", count=2)
        self.assertContains(response, ">20:00</time>")
        self.assertContains(response, "lundi 07 janvier")
        self.assertContains(response, "mardi 08 janvier")
        html = response.content.decode()
        self.assertLess(html.index('id="jour-2030-01-07"'), html.index('id="jour-2030-01-08"'))
        self.assertLess(html.index('id="jour-2030-01-08"'), html.index('id="autre-date-titre"'))

    def test_seances_passees_annulees_terminees_autres_films_et_salles_inactives_exclues(self):
        self.programmer(heure=8)
        self.programmer(heure=12, statut=Seance.Statut.ANNULEE)
        self.programmer(heure=14, statut=Seance.Statut.TERMINEE)
        self.programmer(heure=9)
        autre = Film.objects.create(titre="Autre", duree=30)
        self.programmer(heure=16, film=autre)
        inactive = Salle.objects.create(nom="Fermée", capacite=20, active=False)
        self.programmer(heure=18, salle=inactive)
        response = self.consulter()
        self.assertEqual(self.seances_affichees(response), [])
        self.assertContains(response, "Aucune séance à venir pour ce film.")

    def test_fenetre_sur_sept_jours_en_heure_de_paris(self):
        minuit = self.programmer(jours=1, heure=0)
        soir = self.programmer(jours=6, heure=23, minute=30)
        self.programmer(jours=7, heure=0)
        response = self.consulter(jours=1)
        self.assertEqual(self.seances_affichees(response), [minuit, soir])
        self.assertContains(response, ">00:00</time>", count=1)
        self.assertContains(response, ">23:30</time>")
        self.assertContains(response, 'href="/?date=2030-01-08"')
        self.assertContains(response, 'min="2030-01-14"')
        self.assertContains(response, 'value="2030-01-14"')

    def test_aucune_seance_sur_semaine_mais_programmation_future(self):
        seance = self.programmer(jours=10)
        response = self.consulter()
        self.assertEqual(response.context["seances_par_jour"], [])
        self.assertTrue(response.context["a_des_seances_a_venir"])
        self.assertContains(response, "Aucune séance prévue sur les 7 prochains jours.")
        self.assertNotContains(response, "Aucune séance à venir pour ce film.")
        response = self.consulter(jours=10)
        self.assertEqual(response.context["seances_ulterieures"], [seance])

    def test_calendrier_a_j_plus_sept_sans_masquer_la_semaine(self):
        aujourd_hui = self.programmer()
        ulterieure = self.programmer(jours=7, heure=18)
        self.programmer(jours=8, heure=20)
        response = self.consulter(jours=7)
        self.assertEqual(self.seances_affichees(response), [aujourd_hui, ulterieure])
        self.assertEqual(response.context["seances_par_jour"][0]["seances"], [aujourd_hui])
        self.assertEqual(response.context["seances_ulterieures"], [ulterieure])
        self.assertContains(response, 'value="2030-01-14"')
        self.assertNotContains(response, ">20:00</time>")

    def test_date_ulterieure_sans_seance_conserve_affichage_semaine(self):
        seance = self.programmer()
        response = self.consulter(jours=12)
        self.assertEqual(self.seances_affichees(response), [seance])
        self.assertContains(response, "Aucune séance disponible pour ce film à cette date.")

    def test_aucune_seance_a_venir_pour_ce_film(self):
        self.assertContains(self.consulter(), "Aucune séance à venir pour ce film.")

    def test_dates_invalides_et_passees_retournent_a_aujourdhui(self):
        for valeur in ("invalide", "2030-02-31", "2029-01-01", "9999-12-31"):
            with self.subTest(date=valeur):
                self.assertEqual(self.consulter(date=valeur).context["jour_selectionne"], self.aujourd_hui)

    def test_modification_et_annulation_admin_repercutees_sur_fiche_publique(self):
        from django.contrib.auth import get_user_model

        seance = self.programmer()
        admin = get_user_model().objects.create_superuser(username="admin", email="admin@example.com", password=None)
        self.client.force_login(admin)
        response = self.client.post(reverse("admin:cinema_seance_change", args=[seance.pk]), {
            "film": self.film.pk, "salle": self.salle.pk,
            "debut_0": "2030-01-08", "debut_1": "18:30:00", "prix": "10.00", "_save": "Enregistrer",
        })
        self.assertEqual(response.status_code, 302)
        self.client.logout()
        response = self.consulter()
        self.assertEqual(response.context["seances_par_jour"][0]["jour"], self.aujourd_hui + timedelta(days=1))
        self.assertContains(response, ">18:30</time>")
        self.client.force_login(admin)
        response = self.client.post(reverse("admin:cinema_seance_changelist"), {
            "action": "annuler_les_seances", "_selected_action": [seance.pk], "confirmer": "oui",
        })
        self.assertEqual(response.status_code, 302)
        self.client.logout()
        response = self.consulter()
        self.assertEqual(self.seances_affichees(response), [])
        self.assertContains(response, "Aucune séance à venir pour ce film.")

    def test_chargement_des_salles_sans_requete_par_seance(self):
        for jours in range(7):
            self.programmer(jours=jours)
        with self.assertNumQueries(4):
            self.client.get(self.url)
