from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from cinema.models import Film, Place, Salle, Seance
from paiements.models import Paiement
from reservations.models import Billet, EnvoiBillets, Reservation, ReservationPlace


class ConsultationReservationsAdminTests(TestCase):
    def setUp(self):
        self.lecteur = get_user_model().objects.create_user(
            username="lecture-reservations", is_staff=True,
        )
        self.permission = Permission.objects.get(
            content_type__app_label="reservations", codename="view_reservation",
        )
        self.lecteur.user_permissions.add(self.permission)
        self.superutilisateur = get_user_model().objects.create_superuser(
            username="administrateur", email="admin@example.com", password=None,
        )
        self.film = Film.objects.create(titre="Le Voyage", duree=90)
        self.autre_film = Film.objects.create(titre="Les Retrouvailles", duree=100)
        self.salle = Salle.objects.create(nom="Grande salle", capacite=3)
        self.autre_salle = Salle.objects.create(nom="Petite salle", capacite=1)
        self.debut = (timezone.localtime() + timedelta(days=3)).replace(
            hour=15, minute=30, second=0, microsecond=0,
        )
        self.seance = Seance.objects.create(
            film=self.film, salle=self.salle, debut=self.debut,
            fin=self.debut + timedelta(minutes=90), prix=Decimal("12.35"),
        )
        self.autre_seance = Seance.objects.create(
            film=self.autre_film, salle=self.autre_salle, debut=self.debut + timedelta(days=1),
            fin=self.debut + timedelta(days=1, minutes=100), prix=Decimal("10.00"),
        )
        self.derniere_seance = Seance.objects.create(
            film=self.film, salle=self.salle, debut=self.debut + timedelta(days=2),
            fin=self.debut + timedelta(days=2, minutes=90), prix=Decimal("12.35"),
        )
        self.places = [
            Place.objects.create(salle=self.salle, rangee="B", numero=2),
            Place.objects.create(salle=self.salle, rangee="A", numero=1),
            Place.objects.create(salle=self.salle, rangee="A", numero=3),
        ]
        self.autre_place = Place.objects.create(
            salle=self.autre_salle, rangee="C", numero=4,
        )
        self.reservation = Reservation.objects.create(
            seance=self.seance, email="spectateur@example.com",
            date_expiration=timezone.now() - timedelta(minutes=1),
            montant_total=Decimal("24.70"),
        )
        for place in self.places[:2]:
            ReservationPlace.objects.create(
                reservation=self.reservation, place=place, prix_unitaire=Decimal("12.35"),
            )
        self.confirmee = Reservation.objects.create(
            seance=self.autre_seance, email="autre@example.com",
            statut=Reservation.Statut.CONFIRMEE, date_expiration=self.debut,
            montant_total=Decimal("10.00"),
        )
        association = ReservationPlace.objects.create(
            reservation=self.confirmee, place=self.autre_place, prix_unitaire=Decimal("10.00"),
        )
        Paiement.objects.create(
            reservation=self.confirmee, montant=Decimal("10.00"), statut=Paiement.Statut.REUSSI,
        )
        Billet.objects.create(reservation_place=association)
        EnvoiBillets.objects.create(
            reservation=self.confirmee, statut=EnvoiBillets.Statut.ENVOYE,
        )
        Paiement.objects.create(
            reservation=self.reservation, montant=Decimal("24.70"),
            date_expiration=timezone.now() - timedelta(minutes=1),
        )
        self.expiree = Reservation.objects.create(
            seance=self.derniere_seance, email="expiree@example.com",
            statut=Reservation.Statut.EXPIREE, date_expiration=timezone.now() - timedelta(hours=1),
            montant_total=Decimal("12.35"),
        )
        self.annulee = Reservation.objects.create(
            seance=self.derniere_seance, email="annulee@example.com",
            statut=Reservation.Statut.ANNULEE, date_expiration=self.debut,
            montant_total=Decimal("12.35"),
        )
        for reservation, place in ((self.expiree, self.places[0]), (self.annulee, self.places[2])):
            ReservationPlace.objects.create(
                reservation=reservation, place=place, prix_unitaire=Decimal("12.35"),
            )

        self.client.force_login(self.lecteur)
        self.liste = reverse("admin:reservations_reservation_changelist")
        self.detail = reverse("admin:reservations_reservation_change", args=[self.reservation.pk])

    def identifiants(self, reponse):
        self.assertEqual(reponse.status_code, 200)
        return {reservation.pk for reservation in reponse.context["cl"].result_list}

    def etat_metier(self):
        return {
            modele._meta.label: list(modele.objects.order_by("pk").values())
            for modele in (Reservation, ReservationPlace, Paiement, Billet, EnvoiBillets)
        }

    def test_permission_consultation_suffit_pour_liste_detail_et_places(self):
        accueil = self.client.get(reverse("admin:index"))
        self.assertContains(accueil, self.liste)
        page = self.client.get(self.liste)
        self.assertEqual(self.identifiants(page), {
            self.reservation.pk, self.confirmee.pk, self.expiree.pk, self.annulee.pk,
        })
        for texte in ("Le Voyage", "Grande salle", "Nombre de places", "Date et heure de séance"):
            self.assertContains(page, texte)
        nombres = {
            reservation.pk: reservation.nombre_places_reservees
            for reservation in page.context["cl"].result_list
        }
        self.assertEqual(nombres[self.reservation.pk], 2)
        self.assertEqual(nombres[self.confirmee.pk], 1)
        detail = self.client.get(self.detail)
        for texte in (
            "spectateur@example.com", "Le Voyage", "Grande salle",
            "A1", "B2", "12,35", "24,70",
        ):
            self.assertContains(detail, texte)
        self.assertContains(detail, date_format(self.debut, "DATETIME_FORMAT"))
        self.assertLess(detail.content.index(b">A1<"), detail.content.index(b">B2<"))

    def test_statuts_sont_identifiables(self):
        page = self.client.get(self.liste)
        for libelle in ("En attente de paiement", "Confirmée", "Expirée", "Annulée"):
            self.assertContains(page, libelle)
        self.assertEqual(self.identifiants(self.client.get(self.liste, {
            "statut__exact": Reservation.Statut.CONFIRMEE,
        })), {self.confirmee.pk})

    def test_filtre_film(self):
        page = self.client.get(self.liste, {"seance__film__id__exact": self.film.pk})
        self.assertEqual(self.identifiants(page), {
            self.reservation.pk, self.expiree.pk, self.annulee.pk,
        })

    def test_filtre_seance_et_libelle_en_heure_locale(self):
        page = self.client.get(self.liste, {"seance": self.seance.pk})
        self.assertEqual(self.identifiants(page), {self.reservation.pk})
        self.assertContains(page, f"N°{self.seance.pk}")
        self.assertContains(page, date_format(self.debut, "DATETIME_FORMAT"))

    def test_filtre_date_porte_sur_la_seance_et_non_la_creation(self):
        jour = self.debut.date()
        page = self.client.get(self.liste, {
            "seance__debut__year": jour.year,
            "seance__debut__month": jour.month,
            "seance__debut__day": jour.day,
        })
        self.assertEqual(self.identifiants(page), {self.reservation.pk})
        self.assertContains(page, "date de séance")

    def test_seance_apres_minuit_est_classee_selon_le_jour_local(self):
        debut_local = self.debut.replace(hour=0, minute=30)
        Seance.objects.filter(pk=self.seance.pk).update(
            debut=debut_local, fin=debut_local + timedelta(minutes=90),
        )
        page = self.client.get(self.liste, {
            "seance__debut__year": debut_local.year,
            "seance__debut__month": debut_local.month,
            "seance__debut__day": debut_local.day,
        })
        self.assertEqual(self.identifiants(page), {self.reservation.pk})
        self.assertContains(
            self.client.get(self.detail), date_format(debut_local, "DATETIME_FORMAT"),
        )

    def test_filtres_combinables_et_message_sans_resultat(self):
        page = self.client.get(self.liste, {
            "seance__film__id__exact": self.autre_film.pk, "seance": self.seance.pk,
        })
        self.assertEqual(self.identifiants(page), set())
        self.assertContains(page, "Aucune réservation ne correspond aux critères sélectionnés.")

    def test_recherche_par_identifiant_email_et_film(self):
        for recherche, attendus in (
            (str(self.reservation.pk), {self.reservation.pk}),
            ("spectateur@example.com", {self.reservation.pk}),
            ("Retrouvailles", {self.confirmee.pk}),
        ):
            with self.subTest(recherche=recherche):
                self.assertEqual(
                    self.identifiants(self.client.get(self.liste, {"q": recherche})), attendus,
                )

    def test_aucun_resultat_et_identifiant_seance_invalide(self):
        for parametres in (
            {"q": "reservation-inexistante"}, {"seance": "abc"},
            {"seance": "-1"}, {"seance": str(2**100)},
        ):
            with self.subTest(parametres=parametres):
                page = self.client.get(self.liste, parametres)
                self.assertEqual(self.identifiants(page), set())
                self.assertContains(page, "Aucune réservation ne correspond aux critères sélectionnés.")

    def test_anonyme_redirige_vers_connexion(self):
        self.client.logout()
        for url in (self.liste, self.detail):
            with self.subTest(url=url):
                reponse = self.client.get(url)
                self.assertRedirects(
                    reponse, reverse("admin:login") + "?next=" + url,
                    fetch_redirect_response=False,
                )

    def test_personnel_sans_permission_et_permission_modifier_seule_refuses(self):
        for code in (None, "change_reservation"):
            utilisateur = get_user_model().objects.create_user(
                username=f"personnel-{code}", is_staff=True,
            )
            if code:
                utilisateur.user_permissions.add(Permission.objects.get(
                    content_type__app_label="reservations", codename=code,
                ))
            self.client.force_login(utilisateur)
            for url in (self.liste, self.detail):
                with self.subTest(permission=code, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)

    def test_permission_sans_statut_personnel_ou_compte_inactif_insuffisante(self):
        for actif, personnel in ((True, False), (False, True)):
            utilisateur = get_user_model().objects.create_user(
                username=f"compte-{actif}-{personnel}", is_active=actif, is_staff=personnel,
            )
            utilisateur.user_permissions.add(self.permission)
            self.client.force_login(utilisateur)
            for url in (self.liste, self.detail):
                with self.subTest(actif=actif, personnel=personnel, url=url):
                    self.assertEqual(self.client.get(url).status_code, 302)

    def test_superutilisateur_peut_consulter_mais_aucun_bouton_ecriture(self):
        self.client.force_login(self.superutilisateur)
        liste = self.client.get(self.liste)
        self.assertEqual(liste.status_code, 200)
        self.assertNotContains(liste, 'name="action"')
        self.assertNotContains(liste, reverse("admin:reservations_reservation_add"))
        detail = self.client.get(self.detail)
        self.assertEqual(detail.status_code, 200)
        for nom in ("_save", "_continue", "_addanother"):
            self.assertNotContains(detail, f'name="{nom}"')
        self.assertNotContains(detail, reverse(
            "admin:reservations_reservation_delete", args=[self.reservation.pk],
        ))

    def test_ajout_modification_suppression_refuses_meme_avec_tous_les_droits(self):
        initial = self.etat_metier()
        for utilisateur in (self.lecteur, self.superutilisateur):
            self.client.force_login(utilisateur)
            for nom in ("add", "delete"):
                url = reverse(
                    f"admin:reservations_reservation_{nom}",
                    args=[self.reservation.pk] if nom == "delete" else [],
                )
                for methode in ("get", "post"):
                    with self.subTest(utilisateur=utilisateur.username, nom=nom, methode=methode):
                        reponse = getattr(self.client, methode)(url, {"post": "yes"})
                        self.assertEqual(reponse.status_code, 403)
            reponse = self.client.post(self.detail, {
                "email": "modification@example.com", "statut": Reservation.Statut.ANNULEE,
                "montant_total": "0", "_save": "Enregistrer",
                "places-0-DELETE": "on",
            })
            self.assertEqual(reponse.status_code, 403)
            self.client.post(self.liste, {
                "action": "delete_selected", "_selected_action": [self.reservation.pk],
                "post": "yes",
            })
            self.assertEqual(self.etat_metier(), initial)

    def test_consultation_reste_sans_effet_sur_reservation_echue_avec_paiement_en_attente(self):
        initial = self.etat_metier()
        self.assertEqual(self.client.get(self.liste).status_code, 200)
        self.assertEqual(self.client.get(self.detail).status_code, 200)
        self.assertEqual(self.etat_metier(), initial)

    def test_donnees_affichees_sont_actualisees_depuis_la_base(self):
        self.assertEqual(self.client.get(self.detail).status_code, 200)
        Reservation.objects.filter(pk=self.reservation.pk).update(
            statut=Reservation.Statut.ANNULEE, email="nouvelle-adresse@example.com",
        )
        page = self.client.get(self.detail)
        self.assertContains(page, "nouvelle-adresse@example.com")
        self.assertContains(page, "Annulée")
        self.assertNotContains(page, "spectateur@example.com")

    def test_textes_metier_sont_echappes_dans_liste_et_detail(self):
        titre = '<script>alert("film")</script>'
        Film.objects.filter(pk=self.film.pk).update(titre=titre)
        for url in (self.liste, self.detail):
            with self.subTest(url=url):
                page = self.client.get(url)
                self.assertNotContains(page, titre)
                self.assertContains(page, "&lt;script&gt;")

    def test_nombre_de_requetes_ne_croit_pas_avec_les_reservations_affichees(self):
        # Les nouvelles réservations partagent la même séance : seuls les sièges
        # de la réservation changent, pas le nombre d'options dans les filtres.
        with CaptureQueriesContext(connection) as avant:
            self.assertEqual(self.client.get(self.liste).status_code, 200)
        for numero in range(10):
            reservation = Reservation.objects.create(
                seance=self.seance, email=f"historique-{numero}@example.com",
                statut=Reservation.Statut.ANNULEE, date_expiration=self.debut,
                montant_total=Decimal("12.35"),
            )
            ReservationPlace.objects.create(
                reservation=reservation, place=self.places[0], prix_unitaire=Decimal("12.35"),
            )
        with CaptureQueriesContext(connection) as apres:
            page = self.client.get(self.liste)
            self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["cl"].result_count, 14)
        self.assertEqual(len(apres), len(avant))
