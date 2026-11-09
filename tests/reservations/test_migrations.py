from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class SeparationApplicationsMigrationTests(TransactionTestCase):
    ancienne = [("cinema", "0003_supprimer_date_naissance_personne"), ("reservations", None), ("paiements", None)]

    def migrer(self, cibles):
        executor = MigrationExecutor(connection)
        executor.migrate(cibles)
        ContentType.objects.clear_cache()
        return executor.loader.project_state(list(executor.recorder.applied_migrations())).apps

    def identifiants_tables(self, anciennes=False):
        tables = {"reservation": "reservations", "reservationplace": "reservations", "billet": "reservations", "paiement": "paiements"}
        with connection.cursor() as curseur:
            identifiants = {}
            for modele, application in tables.items():
                prefixe = "cinema" if anciennes else application
                table = f"{prefixe}_{modele}"
                curseur.execute("SELECT %s::regclass::oid", [table])
                identifiants[modele] = curseur.fetchone()[0]
        return identifiants

    def test_transfert_reversible_conserve_donnees_relations_tables_et_permissions(self):
        cibles_courantes = MigrationExecutor(connection).loader.graph.leaf_nodes()
        try:
            ancien = self.migrer(self.ancienne)
            film = ancien.get_model("cinema", "Film").objects.create(titre="Film conservé", duree=90)
            salle = ancien.get_model("cinema", "Salle").objects.create(nom="Salle conservée", capacite=1)
            place = ancien.get_model("cinema", "Place").objects.create(salle=salle, rangee="A", numero=1)
            debut = timezone.now() + timedelta(days=2)
            seance = ancien.get_model("cinema", "Seance").objects.create(
                film=film, salle=salle, debut=debut, fin=debut + timedelta(minutes=90), prix=12,
            )
            reservation = ancien.get_model("cinema", "Reservation").objects.create(
                seance=seance, email="demo@example.com", statut="confirmee",
                date_expiration=debut, montant_total=12,
            )
            association = ancien.get_model("cinema", "ReservationPlace").objects.create(
                reservation=reservation, place=place, prix_unitaire=12,
            )
            billet = ancien.get_model("cinema", "Billet").objects.create(
                reservation_place=association, reference="billet-conserve", statut="valide",
            )
            paiement = ancien.get_model("cinema", "Paiement").objects.create(
                reservation=reservation, stripe_session_id="session-conservee",
                stripe_payment_intent_id="paiement-conserve", montant=12, statut="reussi",
                date_confirmation=timezone.now(),
            )
            types = ancien.get_model("contenttypes", "ContentType")
            permissions = ancien.get_model("auth", "Permission")
            groupe = ancien.get_model("auth", "Group").objects.create(name="Gestionnaires")
            utilisateur = ancien.get_model("auth", "User").objects.create(username="gestionnaire", is_staff=True)
            utilisateur.groups.add(groupe)
            identifiants_types = {}
            identifiants_permissions = {}
            applications = {"reservation": "reservations", "reservationplace": "reservations", "billet": "reservations", "paiement": "paiements"}
            for modele in applications:
                type_contenu, _ = types.objects.get_or_create(app_label="cinema", model=modele)
                permission, _ = permissions.objects.get_or_create(
                    content_type=type_contenu, codename=f"view_{modele}", defaults={"name": f"Can view {modele}"},
                )
                identifiants_types[modele] = type_contenu.pk
                identifiants_permissions[modele] = permission.pk
                if modele == "billet":
                    utilisateur.user_permissions.add(permission)
                else:
                    groupe.permissions.add(permission)
            journal = ancien.get_model("admin", "LogEntry").objects.create(
                user=utilisateur, content_type_id=identifiants_types["reservation"],
                object_id=str(reservation.pk), object_repr="Réservation conservée", action_flag=1,
            )
            tables_avant = self.identifiants_tables(anciennes=True)
            nouveau = self.migrer(cibles_courantes)

            Reservation = nouveau.get_model("reservations", "Reservation")
            ReservationPlace = nouveau.get_model("reservations", "ReservationPlace")
            Billet = nouveau.get_model("reservations", "Billet")
            Paiement = nouveau.get_model("paiements", "Paiement")
            conservee = Reservation.objects.get(pk=reservation.pk)
            self.assertEqual(conservee.seance_id, seance.pk)
            self.assertEqual(conservee.email, "demo@example.com")
            self.assertEqual(conservee.statut, "confirmee")
            self.assertEqual(conservee.montant_total, Decimal("12.00"))
            self.assertEqual(conservee.date_creation, reservation.date_creation)
            self.assertEqual(conservee.date_expiration, reservation.date_expiration)
            self.assertEqual(conservee.places.get().pk, association.pk)
            self.assertEqual(conservee.places.get().place_id, place.pk)
            self.assertEqual(Billet.objects.get(pk=billet.pk).reservation_place_id, association.pk)
            self.assertEqual(Billet.objects.get(pk=billet.pk).reference, "billet-conserve")
            self.assertEqual(Billet.objects.get(pk=billet.pk).date_emission, billet.date_emission)
            paye = Paiement.objects.get(pk=paiement.pk)
            self.assertEqual(paye.reservation_id, reservation.pk)
            self.assertEqual(paye.stripe_session_id, "session-conservee")
            self.assertEqual(paye.stripe_payment_intent_id, "paiement-conserve")
            self.assertEqual(paye.statut, "reussi")
            self.assertEqual(paye.montant, Decimal("12.00"))
            self.assertEqual(paye.date_creation, paiement.date_creation)
            self.assertEqual(paye.date_confirmation, paiement.date_confirmation)
            self.assertEqual(conservee.paiements.get().pk, paiement.pk)
            self.assertEqual(self.identifiants_tables(), tables_avant)
            for modele, application in applications.items():
                self.assertEqual(types.objects.get(app_label=application, model=modele).pk, identifiants_types[modele])
                self.assertEqual(permissions.objects.get(pk=identifiants_permissions[modele]).content_type_id, identifiants_types[modele])
            self.assertEqual(nouveau.get_model("admin", "LogEntry").objects.get(pk=journal.pk).content_type_id, identifiants_types["reservation"])
            courant = get_user_model().objects.get(pk=utilisateur.pk)
            for modele, application in applications.items():
                self.assertTrue(courant.has_perm(f"{application}.view_{modele}"))
                self.assertFalse(courant.has_perm(f"cinema.view_{modele}"))
            with self.assertRaises(IntegrityError), transaction.atomic():
                ReservationPlace.objects.create(reservation_id=reservation.pk, place_id=place.pk, prix_unitaire=12)
            suivante = Reservation.objects.create(
                seance_id=seance.pk, email="autre@example.com", date_expiration=debut, montant_total=0,
            )
            self.assertGreater(suivante.pk, reservation.pk)
            # Le retour arrière restaure aussi les anciennes permissions sans perdre les données.
            restaure = self.migrer(self.ancienne)
            self.assertEqual(restaure.get_model("cinema", "Reservation").objects.get(pk=reservation.pk).places.get().pk, association.pk)
            self.assertTrue(restaure.get_model("cinema", "Paiement").objects.filter(pk=paiement.pk, reservation_id=reservation.pk).exists())
            self.assertTrue(restaure.get_model("cinema", "Billet").objects.filter(pk=billet.pk, reservation_place_id=association.pk).exists())
            self.assertEqual(self.identifiants_tables(anciennes=True), tables_avant)
            for modele in applications:
                self.assertEqual(types.objects.get(app_label="cinema", model=modele).pk, identifiants_types[modele])
        finally:
            self.migrer(cibles_courantes)
