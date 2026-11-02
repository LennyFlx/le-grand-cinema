from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Film, Salle, Seance


@override_settings(TIME_ZONE="Europe/Paris")
class FilmsALAfficheTests(TestCase):
    def setUp(self):
        self.url = reverse("cinema:films")
        self.maintenant = datetime(2030, 1, 7, 9, tzinfo=ZoneInfo("Europe/Paris"))
        self.aujourd_hui = self.maintenant.date()
        horloge = patch("cinema.views.timezone.now", return_value=self.maintenant)
        horloge.start()
        self.addCleanup(horloge.stop)
        self.salle = Salle.objects.create(nom="Salle 1", capacite=20)

    def programmer(self, film, jours=0, heure=12, minute=0, statut=Seance.Statut.PROGRAMMEE, salle=None):
        debut = timezone.make_aware(datetime.combine(
            self.aujourd_hui + timedelta(days=jours), time(heure, minute)
        ))
        return Seance.objects.create(
            film=film, salle=salle or self.salle, debut=debut,
            fin=debut + timedelta(minutes=film.duree), prix=10, statut=statut,
        )

    def consulter(self, jours=None, **parametres):
        if jours is not None:
            parametres["date"] = (self.aujourd_hui + timedelta(days=jours)).isoformat()
        response = self.client.get(self.url, parametres)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "cinema/films.html")
        return response

    def test_consultation_publique_et_aujourdhui_par_defaut(self):
        film = Film.objects.create(titre="Film du jour", duree=90)
        seance = self.programmer(film)
        response = self.consulter()
        self.assertEqual(response.context["jour_selectionne"], self.aujourd_hui)
        self.assertEqual(list(response.context["films"]), [film])
        self.assertEqual(response.context["films"][0].seances_du_jour, [seance])
        self.assertContains(response, film.titre)
        self.assertContains(response, "12:00")

    def test_frise_sur_sept_jours_et_selection_accessible(self):
        response = self.consulter(jours=3)
        self.assertEqual(response.context["jours"], [
            self.aujourd_hui + timedelta(days=ecart) for ecart in range(7)
        ])
        self.assertContains(response, 'aria-current="date"', count=1)
        self.assertContains(response, '?date=2030-01-13')
        self.assertContains(response, 'aria-label="jeudi 10 janvier 2030"')

    def test_film_visible_uniquement_le_jour_selectionne(self):
        film = Film.objects.create(titre="Demain uniquement", duree=90)
        self.programmer(film, jours=1)
        self.assertEqual(list(self.consulter().context["films"]), [])
        self.assertEqual(list(self.consulter(jours=1).context["films"]), [film])

    def test_plusieurs_horaires_regroupes_sur_une_carte(self):
        film = Film.objects.create(titre="Plusieurs séances", duree=90)
        tard = self.programmer(film, heure=20)
        tot = self.programmer(film, heure=14)
        self.programmer(film, jours=1)
        response = self.consulter()
        films = list(response.context["films"])
        self.assertEqual(len(films), 1)
        self.assertEqual(films[0].seances_du_jour, [tot, tard])
        html = response.content.decode()
        self.assertLess(html.index(">14:00</time>"), html.index(">20:00</time>"))
        self.assertNotContains(response, ">12:00</time>")

    def test_tri_sorties_recentes_puis_titre_dates_manquantes_en_dernier(self):
        recentes_a = Film.objects.create(titre="A récent", duree=30, date_sortie=date(2029, 12, 1))
        recentes_z = Film.objects.create(titre="Z récent", duree=30, date_sortie=date(2029, 12, 1))
        ancien = Film.objects.create(titre="Ancien", duree=30, date_sortie=date(2028, 1, 1))
        sans_date = Film.objects.create(titre="A sans date", duree=30)
        for heure, film in enumerate([sans_date, ancien, recentes_z, recentes_a], start=12):
            self.programmer(film, heure=heure)
        self.assertEqual(list(self.consulter().context["films"]), [recentes_a, recentes_z, ancien, sans_date])

    def test_films_inactifs_ou_sans_seance_exclus(self):
        Film.objects.create(titre="Sans séance", duree=90)
        retire = Film.objects.create(titre="Retiré", duree=90, actif=False)
        self.programmer(retire)
        self.assertEqual(list(self.consulter().context["films"]), [])

    def test_seances_passees_annulees_et_terminees_exclues(self):
        for index, (heure, statut) in enumerate((
            (8, Seance.Statut.PROGRAMMEE),
            (12, Seance.Statut.ANNULEE),
            (15, Seance.Statut.TERMINEE),
        )):
            film = Film.objects.create(titre=f"Film {index}", duree=30)
            self.programmer(film, heure=heure, statut=statut)
        self.assertEqual(list(self.consulter().context["films"]), [])

    def test_conditions_portent_sur_la_meme_seance(self):
        film = Film.objects.create(titre="Passée ou annulée", duree=90)
        self.programmer(film, jours=-1)
        self.programmer(film, statut=Seance.Statut.ANNULEE)
        self.assertEqual(list(self.consulter().context["films"]), [])

    def test_salle_inactive_exclue(self):
        self.salle.active = False
        self.salle.save()
        film = Film.objects.create(titre="Salle fermée", duree=90)
        self.programmer(film)
        self.assertEqual(list(self.consulter().context["films"]), [])

    def test_seance_a_lheure_actuelle_non_proposee(self):
        film = Film.objects.create(titre="Déjà commencé", duree=90)
        self.programmer(film, heure=9)
        self.assertEqual(list(self.consulter().context["films"]), [])

    def test_horaires_invalides_exclus_du_film_encore_programme(self):
        film = Film.objects.create(titre="Film", duree=30)
        valide = self.programmer(film, heure=18)
        self.programmer(film, heure=8)
        self.programmer(film, heure=12, statut=Seance.Statut.ANNULEE)
        self.assertEqual(self.consulter().context["films"][0].seances_du_jour, [valide])

    def test_dates_invalides_ou_passees_reviennent_a_aujourdhui(self):
        for valeur in ("incorrect", "2030-02-31", "2029-12-31", "9999-12-31", ""):
            with self.subTest(date=valeur):
                self.assertEqual(self.consulter(date=valeur).context["jour_selectionne"], self.aujourd_hui)

    def test_limites_de_jour_en_heure_de_paris(self):
        film = Film.objects.create(titre="Séances de minuit", duree=30)
        minuit = self.programmer(film, jours=1, heure=0)
        self.programmer(film, jours=2, heure=0)
        self.assertEqual(minuit.debut.astimezone(datetime_timezone.utc).date(), self.aujourd_hui)
        response = self.consulter(jours=1)
        self.assertEqual(response.context["films"][0].seances_du_jour, [minuit])
        self.assertContains(response, ">00:00</time>")

    def test_message_sans_programmation_du_jour(self):
        self.assertContains(self.consulter(), "Aucune séance disponible pour cette journée.")

    def test_uniquement_infos_demandees_sur_la_carte(self):
        film = Film.objects.create(
            titre="Film complet", duree=105, description="Synopsis à masquer", date_sortie=date(2029, 10, 21),
        )
        self.programmer(film)
        response = self.consulter()
        self.assertTemplateUsed(response, "cinema/base.html")
        self.assertContains(response, "105 min")
        self.assertContains(response, ">12:00</time>")
        self.assertNotContains(response, "Synopsis à masquer")
        self.assertNotContains(response, "21/10/2029")
        self.assertNotContains(response, "Lire le synopsis")

    def test_titre_non_interprete_comme_html(self):
        film = Film.objects.create(titre="<script>alert('titre')</script>", duree=90)
        self.programmer(film)
        response = self.consulter()
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>")

    def test_affiche_depuis_media_avec_espaces_dans_le_nom(self):
        film = Film.objects.create(titre="Film illustré", duree=90, affiche="affiches/mon film.webp")
        self.programmer(film)
        with override_settings(MEDIA_URL="/medias-test/"):
            response = self.consulter()
        self.assertContains(response, 'src="/medias-test/affiches/mon%20film.webp"')
        self.assertContains(response, 'alt="Affiche du film Film illustré"')
        self.assertNotContains(response, "Affiche non disponible")

    def test_film_sans_affiche_affiche_un_emplacement_de_remplacement(self):
        film = Film.objects.create(titre="Sans affiche", duree=90)
        self.programmer(film)
        response = self.consulter()
        self.assertContains(response, "Affiche non disponible")
        self.assertNotContains(response, 'alt="Affiche du film')

    def test_regroupement_sans_requete_par_film(self):
        for heure in range(12, 15):
            film = Film.objects.create(titre=f"Film {heure}", duree=30)
            self.programmer(film, heure=heure)
        with self.assertNumQueries(2):
            self.client.get(self.url)

    def test_onglet_date_ouvre_le_formulaire_sans_changer_le_jour(self):
        response = self.consulter(jours=2, calendrier="1")
        self.assertContains(response, 'id="choisir-date"')
        self.assertContains(response, 'type="date"')
        self.assertContains(response, 'min="2030-01-07"')
        self.assertContains(response, 'value="2030-01-09"')
        self.assertEqual(response.context["jour_selectionne"], self.aujourd_hui + timedelta(days=2))
        self.assertNotContains(self.consulter(), 'id="choisir-date"')

    def test_choix_de_j_plus_sept_et_date_plus_lointaine(self):
        for jours in (7, 30):
            with self.subTest(jours=jours):
                film = Film.objects.create(titre=f"Film à J+{jours}", duree=90)
                seance = self.programmer(film, jours=jours)
                response = self.consulter(jours=jours)
                self.assertEqual(list(response.context["films"]), [film])
                self.assertEqual(response.context["films"][0].seances_du_jour, [seance])
                self.assertTrue(response.context["date_personnalisee"])
                self.assertContains(response, 'aria-current="date"', count=1)
                self.assertContains(response, 'id="choisir-date"')

    def test_date_personnalisee_sans_seance(self):
        response = self.consulter(jours=10)
        self.assertEqual(response.context["jour_selectionne"], self.aujourd_hui + timedelta(days=10))
        self.assertContains(response, "Aucune séance disponible pour cette journée.")
