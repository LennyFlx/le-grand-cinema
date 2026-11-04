from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from .models import Film, Genre, Participation, Personne


class InformationsFilmsTests(TestCase):
    def setUp(self):
        self.film = Film.objects.create(titre="Film", duree=90)
        self.personne = Personne.objects.create(nom="Dupont", prenom="Camille")

    def test_film_sans_informations_complementaires_reste_valide(self):
        self.film.full_clean()
        self.assertFalse(self.film.genres.exists())
        self.assertFalse(self.film.participations.exists())

    def test_meme_personne_acteur_et_realisateur_de_plusieurs_films(self):
        autre_film = Film.objects.create(titre="Autre film", duree=120)
        for film in (self.film, autre_film):
            for fonction in Participation.Fonction.values:
                Participation.objects.create(film=film, personne=self.personne, fonction=fonction)
        self.assertEqual(Personne.objects.count(), 1)
        self.assertEqual(self.personne.participations.count(), 4)
        self.assertEqual(self.film.participations.count(), 2)

    def test_participation_identique_refusee_en_validation_et_en_base(self):
        valeurs = {"film": self.film, "personne": self.personne, "fonction": Participation.Fonction.ACTEUR}
        Participation.objects.create(**valeurs)
        with self.assertRaises(ValidationError):
            Participation(**valeurs).full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Participation.objects.create(**valeurs)

    def test_fonction_inconnue_refusee_en_validation_et_en_base(self):
        participation = Participation(film=self.film, personne=self.personne, fonction="inconnue")
        with self.assertRaises(ValidationError):
            participation.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            participation.save()

    def test_genres_reutilisables_et_plusieurs_genres_par_film(self):
        drame = Genre.objects.create(nom="Drame")
        comedie = Genre.objects.create(nom="Comédie")
        autre_film = Film.objects.create(titre="Autre film", duree=120)
        self.film.genres.add(drame, comedie)
        autre_film.genres.add(drame)
        self.assertEqual(self.film.genres.count(), 2)
        self.assertEqual(drame.films.count(), 2)
        self.assertEqual(Genre.objects.count(), 2)

    def test_association_genre_unique_meme_en_ecriture_directe(self):
        genre = Genre.objects.create(nom="Drame")
        self.film.genres.add(genre)
        self.film.genres.add(genre)
        self.assertEqual(self.film.genres.count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Film.genres.through.objects.create(film_id=self.film.pk, genre_id=genre.pk)

    def test_genre_unique_sans_casse_ni_espaces_peripheriques(self):
        Genre.objects.create(nom="Drame")
        for nom in ("drame", "DRAME", " Drame "):
            with self.subTest(nom=nom):
                with self.assertRaises(ValidationError):
                    Genre(nom=nom).full_clean()
                # bulk_create contourne save() pour vérifier la contrainte SQL elle-même.
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Genre.objects.bulk_create([Genre(nom=nom)])

    def test_nettoyage_des_noms_et_homonymes_autorises(self):
        personne = Personne(nom="  Dupont  ", prenom="  Camille  ")
        personne.full_clean()
        self.assertEqual((personne.nom, personne.prenom), ("Dupont", "Camille"))
        personne.save()
        self.assertEqual(Personne.objects.filter(nom="Dupont", prenom="Camille").count(), 2)
        genre = Genre.objects.create(nom="  Animation  ")
        genre.refresh_from_db()
        self.assertEqual(genre.nom, "Animation")
        personne = Personne.objects.create(nom="  Dupont  ", prenom=" Camille ")
        personne.refresh_from_db()
        self.assertEqual((personne.nom, personne.prenom), ("Dupont", "Camille"))

    def test_noms_vides_refuses_en_validation_et_en_base(self):
        for modele in (Personne, Genre):
            for nom in ("", "   ", "\t\n"):
                with self.subTest(modele=modele.__name__, nom=repr(nom)):
                    with self.assertRaises(ValidationError):
                        modele(nom=nom).full_clean()
                    with self.assertRaises(IntegrityError), transaction.atomic():
                        modele.objects.bulk_create([modele(nom=nom)])

    def test_mononyme_et_affichage_identite(self):
        mononyme = Personne(nom="Cher")
        mononyme.full_clean()
        mononyme.save()
        self.assertEqual(str(mononyme), "Cher")
        self.assertEqual(str(self.personne), "Camille Dupont")

    def test_personne_associee_protegee_et_retrait_sans_suppression(self):
        participation = Participation.objects.create(
            film=self.film, personne=self.personne, fonction=Participation.Fonction.ACTEUR,
        )
        with self.assertRaises(ProtectedError):
            self.personne.delete()
        genre = Genre.objects.create(nom="Drame")
        self.film.genres.add(genre)
        self.film.genres.remove(genre)
        participation.delete()
        self.assertTrue(Film.objects.filter(pk=self.film.pk).exists())
        self.assertTrue(Personne.objects.filter(pk=self.personne.pk).exists())
        self.assertTrue(Genre.objects.filter(pk=genre.pk).exists())


class InformationsFilmsMigrationTests(TransactionTestCase):
    def test_migration_conserve_films_seances_et_reservations(self):
        ancienne = [("cinema", "0001_initial")]
        nouvelle = [("cinema", "0003_supprimer_date_naissance_personne")]
        executor = MigrationExecutor(connection)
        try:
            executor.migrate(ancienne)
            anciens_modeles = executor.loader.project_state(ancienne).apps
            FilmAvant = anciens_modeles.get_model("cinema", "Film")
            SalleAvant = anciens_modeles.get_model("cinema", "Salle")
            SeanceAvant = anciens_modeles.get_model("cinema", "Seance")
            ReservationAvant = anciens_modeles.get_model("cinema", "Reservation")
            film = FilmAvant.objects.create(
                titre="Film existant", duree=105, description="Synopsis conservé",
                affiche="affiches/existant.webp", date_sortie=date(2026, 1, 1), actif=True,
            )
            salle = SalleAvant.objects.create(nom="Salle existante", capacite=20)
            debut = timezone.now() + timedelta(days=1)
            seance = SeanceAvant.objects.create(
                film=film, salle=salle, debut=debut, fin=debut + timedelta(minutes=105), prix=10,
            )
            reservation = ReservationAvant.objects.create(
                seance=seance, email="spectateur@example.com", date_expiration=debut, montant_total=10,
            )
            intermediaire = [("cinema", "0002_informations_films")]
            executor = MigrationExecutor(connection)
            executor.migrate(intermediaire)
            modeles_avec_date = executor.loader.project_state(intermediaire).apps
            personne = modeles_avec_date.get_model("cinema", "Personne").objects.create(
                nom="Dupont", prenom="Camille", date_naissance=date(1980, 1, 2),
            )
            genre = modeles_avec_date.get_model("cinema", "Genre").objects.create(nom="Drame")
            modeles_avec_date.get_model("cinema", "Film").objects.get(pk=film.pk).genres.add(genre)
            for fonction in ("acteur", "realisateur"):
                modeles_avec_date.get_model("cinema", "Participation").objects.create(
                    film_id=film.pk, personne_id=personne.pk, fonction=fonction,
                )
            executor = MigrationExecutor(connection)
            executor.migrate(nouvelle)
            nouveaux_modeles = executor.loader.project_state(nouvelle).apps
            conserve = nouveaux_modeles.get_model("cinema", "Film").objects.get(pk=film.pk)
            self.assertEqual(conserve.titre, "Film existant")
            self.assertEqual(conserve.description, "Synopsis conservé")
            self.assertEqual(conserve.duree, 105)
            self.assertEqual(conserve.affiche, "affiches/existant.webp")
            self.assertEqual(conserve.date_sortie, date(2026, 1, 1))
            self.assertTrue(conserve.actif)
            self.assertEqual(list(conserve.genres.values_list("pk", flat=True)), [genre.pk])
            self.assertEqual(conserve.participations.filter(personne_id=personne.pk).count(), 2)
            PersonneApres = nouveaux_modeles.get_model("cinema", "Personne")
            conservee = PersonneApres.objects.get(pk=personne.pk)
            self.assertEqual((conservee.nom, conservee.prenom), ("Dupont", "Camille"))
            self.assertNotIn("date_naissance", [champ.name for champ in PersonneApres._meta.fields])
            self.assertTrue(nouveaux_modeles.get_model("cinema", "Seance").objects.filter(pk=seance.pk, film_id=film.pk).exists())
            self.assertTrue(nouveaux_modeles.get_model("cinema", "Reservation").objects.filter(pk=reservation.pk, seance_id=seance.pk).exists())
        finally:
            # Toujours restaurer le schéma courant avant le nettoyage de la base de test.
            restauration = MigrationExecutor(connection)
            restauration.migrate(restauration.loader.graph.leaf_nodes())
