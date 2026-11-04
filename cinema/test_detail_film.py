from datetime import date, timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Film, Genre, Participation, Personne, Salle, Seance


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
        self.assertContains(response, f'href="{self.url}"', count=2)

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
        with self.assertNumQueries(3):
            self.client.get(self.url)
