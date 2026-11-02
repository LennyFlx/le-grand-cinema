from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Film, Salle, Seance


class FilmsALAfficheTests(TestCase):
    def setUp(self):
        self.url = reverse("cinema:films")
        self.maintenant = timezone.now()
        self.salle = Salle.objects.create(nom="Salle 1", capacite=20)

    def programmer(self, film, jours=1, statut=Seance.Statut.PROGRAMMEE, salle=None):
        debut = self.maintenant + timedelta(days=jours)
        return Seance.objects.create(
            film=film, salle=salle or self.salle, debut=debut,
            fin=debut + timedelta(minutes=film.duree), prix=10, statut=statut,
        )

    def films_affiches(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "cinema/films.html")
        return list(response.context["films"])

    def test_consultation_publique_sans_compte(self):
        film = Film.objects.create(titre="Film à venir", duree=90)
        self.programmer(film)
        self.assertEqual(self.films_affiches(), [film])
        self.assertContains(self.client.get(self.url), film.titre)

    def test_films_inactifs_ou_sans_seance_exclus(self):
        Film.objects.create(titre="Sans séance", duree=90)
        retire = Film.objects.create(titre="Retiré", duree=90, actif=False)
        self.programmer(retire)
        self.assertEqual(self.films_affiches(), [])

    def test_seances_passees_annulees_et_terminees_exclues(self):
        for index, (jours, statut) in enumerate((
            (-1, Seance.Statut.PROGRAMMEE),
            (1, Seance.Statut.ANNULEE),
            (2, Seance.Statut.TERMINEE),
        )):
            film = Film.objects.create(titre=f"Film {index}", duree=90)
            self.programmer(film, jours=jours, statut=statut)
        self.assertEqual(self.films_affiches(), [])

    def test_conditions_portent_sur_la_meme_seance(self):
        film = Film.objects.create(titre="Passée ou annulée", duree=90)
        self.programmer(film, jours=-1)
        self.programmer(film, statut=Seance.Statut.ANNULEE)
        self.assertEqual(self.films_affiches(), [])

    def test_salle_inactive_exclue(self):
        self.salle.active = False
        self.salle.save()
        film = Film.objects.create(titre="Salle fermée", duree=90)
        self.programmer(film)
        self.assertEqual(self.films_affiches(), [])

    def test_tri_par_titre_et_absence_de_doublons(self):
        z = Film.objects.create(titre="Z Film", duree=90)
        a = Film.objects.create(titre="A Film", duree=90)
        self.programmer(z)
        self.programmer(a, jours=2)
        self.programmer(a, jours=3)
        self.assertEqual(self.films_affiches(), [a, z])

    def test_seance_deja_commencee_non_proposee(self):
        film = Film.objects.create(titre="Déjà commencé", duree=90)
        self.programmer(film, jours=0)
        with patch("cinema.views.timezone.now", return_value=self.maintenant):
            self.assertEqual(self.films_affiches(), [])

    def test_message_sans_programmation(self):
        self.assertContains(self.client.get(self.url), "Aucun film n'est programmé pour le moment.")

    def test_informations_du_film_et_champs_facultatifs(self):
        from datetime import date

        film = Film.objects.create(
            titre="Film complet", duree=105,
            description="Un voyage au cinéma.", date_sortie=date(2026, 10, 21),
        )
        self.programmer(film)
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, "cinema/base.html")
        self.assertContains(response, "105 min")
        self.assertContains(response, "Un voyage au cinéma.")
        self.assertContains(response, '<time datetime="2026-10-21">21/10/2026</time>', html=True)
        film.description = ""
        film.date_sortie = None
        film.save()
        response = self.client.get(self.url)
        self.assertContains(response, "Synopsis non renseigné.")
        self.assertNotContains(response, "<time")

    def test_titre_et_synopsis_ne_sont_pas_interpretes_comme_html(self):
        film = Film.objects.create(
            titre="<script>alert('titre')</script>", duree=90,
            description="<script>alert('synopsis')</script>\nDeuxième ligne.",
        )
        self.programmer(film)
        response = self.client.get(self.url)
        self.assertContains(response, "&lt;script&gt;")
        self.assertContains(response, "<br>")
        self.assertNotContains(response, "<script>")
