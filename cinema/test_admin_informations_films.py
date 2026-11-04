from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from .models import Film, Genre, Participation, Personne


class InformationsFilmsAdminTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password=None,
        )
        self.client.force_login(self.admin)
        self.film = Film.objects.create(titre="Film", duree=90)
        self.personne = Personne.objects.create(nom="Dupont", prenom="Camille")
        self.genre = Genre.objects.create(nom="Drame")
        self.url_film = reverse("admin:cinema_film_change", args=[self.film.pk])

    def donnees_film(self, participations=(), **changements):
        donnees = {
            "titre": self.film.titre, "duree": self.film.duree, "description": "",
            "date_sortie": "", "affiche": "", "actif": "on", "genres": [self.genre.pk],
            "participations-TOTAL_FORMS": str(len(participations)),
            "participations-INITIAL_FORMS": str(sum(bool(ligne.get("id")) for ligne in participations)),
            "participations-MIN_NUM_FORMS": "0", "participations-MAX_NUM_FORMS": "1000",
            "_save": "Enregistrer",
        }
        for index, participation in enumerate(participations):
            for champ, valeur in participation.items():
                donnees[f"participations-{index}-{champ}"] = valeur
        donnees.update(changements)
        return donnees

    def autocomplete(self, modele, champ, recherche):
        return self.client.get(reverse("admin:autocomplete"), {
            "app_label": "cinema", "model_name": modele, "field_name": champ, "term": recherche,
        })

    def test_recherche_personnes_et_homonymes_autorises(self):
        homonyme = Personne.objects.create(nom="Dupont", prenom="Camille")
        Personne.objects.create(nom="Martin", prenom="Alex")
        for recherche in ("Dupont", "Camille"):
            with self.subTest(recherche=recherche):
                response = self.client.get(reverse("admin:cinema_personne_changelist"), {"q": recherche})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(set(response.context["cl"].result_list), {self.personne, homonyme})
        response = self.autocomplete("participation", "personne", "Camille Dupont")
        self.assertEqual(response.status_code, 200)
        resultats = response.json()["results"]
        self.assertEqual({ligne["id"] for ligne in resultats}, {str(self.personne.pk), str(homonyme.pk)})
        self.assertTrue(all(ligne["text"] == "Camille Dupont" for ligne in resultats))

    def test_creation_et_modification_personne_depuis_admin(self):
        response = self.client.post(reverse("admin:cinema_personne_add"), {
            "nom": "  Martin ", "prenom": " Alex ", "_save": "Enregistrer",
        })
        self.assertEqual(response.status_code, 302)
        personne = Personne.objects.get(nom="Martin")
        self.assertEqual(personne.prenom, "Alex")
        response = self.client.post(reverse("admin:cinema_personne_change", args=[personne.pk]), {
            "nom": "Martin", "prenom": "Alexandre", "_save": "Enregistrer",
        })
        self.assertEqual(response.status_code, 302)
        personne.refresh_from_db()
        self.assertEqual(personne.prenom, "Alexandre")

    def test_gestion_et_recherche_des_genres(self):
        response = self.client.post(reverse("admin:cinema_genre_add"), {"nom": " Animation ", "_save": "Enregistrer"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Genre.objects.filter(nom="Animation").exists())
        response = self.autocomplete("film", "genres", "Dra")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [{"id": str(self.genre.pk), "text": "Drame"}])
        response = self.client.post(reverse("admin:cinema_genre_change", args=[self.genre.pk]), {
            "nom": "Drame historique", "_save": "Enregistrer",
        })
        self.assertEqual(response.status_code, 302)
        self.genre.refresh_from_db()
        self.assertEqual(self.genre.nom, "Drame historique")

    def test_genre_en_double_affiche_une_erreur(self):
        response = self.client.post(reverse("admin:cinema_genre_add"), {"nom": " drame ", "_save": "Enregistrer"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["adminform"].form.errors)
        self.assertEqual(Genre.objects.count(), 1)

    def test_association_genres_et_deux_fonctions_sans_dupliquer_la_personne(self):
        comedie = Genre.objects.create(nom="Comédie")
        participations = [
            {"personne": self.personne.pk, "fonction": fonction}
            for fonction in Participation.Fonction.values
        ]
        response = self.client.post(self.url_film, self.donnees_film(
            participations, genres=[self.genre.pk, comedie.pk],
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(self.film.genres.all()), {self.genre, comedie})
        self.assertEqual(set(self.film.participations.values_list("fonction", flat=True)), {"acteur", "realisateur"})
        self.assertEqual(Personne.objects.count(), 1)

    def test_creation_film_avec_participation_et_genre(self):
        response = self.client.post(reverse("admin:cinema_film_add"), self.donnees_film(
            [{"personne": self.personne.pk, "fonction": "realisateur"}], titre="Nouveau film",
        ))
        self.assertEqual(response.status_code, 302)
        film = Film.objects.get(titre="Nouveau film")
        self.assertEqual(list(film.genres.all()), [self.genre])
        self.assertEqual(film.participations.get().personne_id, self.personne.pk)

    def test_doublon_dans_les_lignes_refuse_sans_sauvegarde_partielle(self):
        ligne = {"personne": self.personne.pk, "fonction": "acteur"}
        response = self.client.post(self.url_film, self.donnees_film(
            [ligne, ligne], titre="Modification à refuser",
        ))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["inline_admin_formsets"][0].formset.non_form_errors())
        self.film.refresh_from_db()
        self.assertEqual(self.film.titre, "Film")
        self.assertFalse(self.film.participations.exists())
        self.assertFalse(self.film.genres.exists())

    def test_fonction_invalide_refusee(self):
        response = self.client.post(self.url_film, self.donnees_film(
            [{"personne": self.personne.pk, "fonction": "inconnue"}],
        ))
        self.assertEqual(response.status_code, 200)
        formset = response.context["inline_admin_formsets"][0].formset
        self.assertIn("fonction", formset.forms[0].errors)
        self.assertFalse(self.film.participations.exists())

    def test_retrait_participation_et_genre_conserve_les_referentiels(self):
        participation = Participation.objects.create(film=self.film, personne=self.personne, fonction="acteur")
        self.film.genres.add(self.genre)
        response = self.client.post(self.url_film, self.donnees_film([
            {"id": participation.pk, "personne": self.personne.pk, "fonction": "acteur", "DELETE": "on"},
        ], genres=[]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.film.participations.exists())
        self.assertFalse(self.film.genres.exists())
        self.assertTrue(Personne.objects.filter(pk=self.personne.pk).exists())
        self.assertTrue(Genre.objects.filter(pk=self.genre.pk).exists())

    def test_suppression_personne_participante_protegee(self):
        Participation.objects.create(film=self.film, personne=self.personne, fonction="acteur")
        response = self.client.post(reverse("admin:cinema_personne_delete", args=[self.personne.pk]), {"post": "yes"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["protected"])
        self.assertTrue(Personne.objects.filter(pk=self.personne.pk).exists())

    def test_acces_anonyme_et_personnel_sans_permission_refuses(self):
        urls = [reverse("admin:cinema_personne_add"), reverse("admin:cinema_genre_add")]
        self.client.logout()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)
        personnel = get_user_model().objects.create_user(username="personnel", is_staff=True)
        self.client.force_login(personnel)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {"nom": "Interdit"}).status_code, 403)
        self.assertEqual(self.autocomplete("participation", "personne", "Dupont").status_code, 403)
        self.assertEqual(self.autocomplete("film", "genres", "Drame").status_code, 403)

    def test_autocomplete_avec_permission_de_consultation_uniquement(self):
        personnel = get_user_model().objects.create_user(username="lecteur", is_staff=True)
        personnel.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="cinema", codename__in=["view_personne", "view_genre"],
        ))
        self.client.force_login(personnel)
        self.assertEqual(self.autocomplete("participation", "personne", "Dupont").status_code, 200)
        self.assertEqual(self.autocomplete("film", "genres", "Drame").status_code, 200)
        self.assertEqual(self.client.post(reverse("admin:cinema_personne_add"), {"nom": "Interdit"}).status_code, 403)

    def test_permission_modifier_film_ne_permet_pas_ajout_participation(self):
        personnel = get_user_model().objects.create_user(username="editeur", is_staff=True)
        personnel.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="cinema", codename__in=["change_film", "view_genre", "view_personne"],
        ))
        self.client.force_login(personnel)
        response = self.client.post(self.url_film, self.donnees_film([
            {"personne": self.personne.pk, "fonction": "acteur"},
        ]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.film.participations.exists())
