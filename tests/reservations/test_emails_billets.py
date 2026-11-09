from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import timedelta
from decimal import Decimal
from io import BytesIO, StringIO
from smtplib import SMTPException
from threading import Barrier
from unittest.mock import patch
from urllib.parse import urlsplit

from django.core import mail
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader

from cinema.models import Film, Place, Salle, Seance
from cinema.services import annuler_seance
from paiements.models import Paiement
from paiements.services import traiter_session_checkout

from reservations.emails import envoyer_billets, programmer_envoi_billets
from reservations.models import Billet, EnvoiBillets, Reservation, ReservationPlace
from reservations.services import creer_url_billets, generer_billets


def creer_reservation_email(*, confirmee=True):
    salle = Salle.objects.create(nom="Salle email", capacite=2)
    film = Film.objects.create(titre="L’Été & une <séance spéciale>", duree=90)
    debut = timezone.now() + timedelta(days=1)
    seance = Seance.objects.create(
        film=film, salle=salle, debut=debut,
        fin=debut + timedelta(minutes=90), prix=Decimal("12.35"),
    )
    reservation = Reservation.objects.create(
        seance=seance, email="spectateur@example.com",
        statut=Reservation.Statut.CONFIRMEE if confirmee else Reservation.Statut.EN_ATTENTE,
        date_expiration=timezone.now() + timedelta(minutes=31),
        montant_total=Decimal("24.70"),
    )
    for numero in (1, 2):
        ReservationPlace.objects.create(
            reservation=reservation,
            place=Place.objects.create(salle=salle, rangee="A", numero=numero),
            prix_unitaire=Decimal("12.35"),
        )
    Paiement.objects.create(
        reservation=reservation, montant=Decimal("24.70"),
        stripe_session_id="cs_test_email",
        statut=Paiement.Statut.REUSSI if confirmee else Paiement.Statut.EN_ATTENTE,
    )
    if confirmee:
        generer_billets(reservation)
    return reservation


def session_payee(reservation):
    return {
        "id": "cs_test_email", "mode": "payment", "status": "complete",
        "payment_status": "paid", "currency": "eur", "amount_total": 2470,
        "client_reference_id": str(reservation.pk),
        "metadata": {"reservation_id": str(reservation.pk)},
        "payment_intent": "pi_test_email",
    }


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    SITE_URL="https://cinema.example.com",
    DEFAULT_FROM_EMAIL="Le Grand Cinéma <billets@example.com>",
)
class EnvoiEmailBilletsTests(TestCase):
    def setUp(self):
        self.reservation = creer_reservation_email()
        self.url = reverse("reservations:renvoyer_email_billets", args=[self.reservation.pk])
        self.detail = reverse("reservations:detail_reservation", args=[self.reservation.pk])
        session = self.client.session
        session["reservation_provisoire_id"] = self.reservation.pk
        session.save()

    def test_email_texte_et_html_avec_recapitulatif_et_lien_signe(self):
        with self.captureOnCommitCallbacks(execute=True):
            programmer_envoi_billets(self.reservation)

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [self.reservation.email])
        self.assertEqual(message.from_email, "Le Grand Cinéma <billets@example.com>")
        self.assertIn(self.reservation.seance.film.titre, message.body)
        self.assertIn(self.reservation.seance.salle.nom, message.body)
        self.assertIn("Places : A1, A2", message.body)
        self.assertIn("24,70", message.body)
        self.assertIn("Vos billets sont joints à cet email au format PDF.", message.body)
        self.assertEqual(len(message.alternatives), 1)
        self.assertEqual(message.alternatives[0].mimetype, "text/html")
        self.assertIn("&lt;séance spéciale&gt;", message.alternatives[0].content)
        self.assertNotIn("<séance spéciale>", message.alternatives[0].content)
        url = "https://cinema.example.com" + creer_url_billets(self.reservation)
        self.assertIn(url, message.body)
        self.assertIn(url, message.alternatives[0].content)
        self.assertEqual(Client().get(urlsplit(url).path).status_code, 200)
        envoi = EnvoiBillets.objects.get()
        self.assertEqual(envoi.statut, EnvoiBillets.Statut.ENVOYE)
        self.assertIsNotNone(envoi.date_envoi)
        self.assertIsNotNone(envoi.date_derniere_tentative)

    def test_piece_jointe_pdf_contient_un_billet_par_place(self):
        self.assertTrue(envoyer_billets(self.reservation.pk))

        message = mail.outbox[0]
        self.assertEqual(len(message.attachments), 1)
        pieces = [
            partie for partie in message.message().walk()
            if partie.get_content_type() == "application/pdf"
        ]
        self.assertEqual(len(pieces), 1)
        piece = pieces[0]
        self.assertEqual(piece.get_filename(), f"billets-reservation-{self.reservation.pk}.pdf")
        self.assertEqual(piece.get_content_disposition(), "attachment")
        lecteur = PdfReader(BytesIO(piece.get_payload(decode=True)), strict=True)
        self.assertEqual(len(lecteur.pages), 2)
        billets = Billet.objects.select_related("reservation_place__place").order_by(
            "reservation_place__place__numero",
        )
        for page, billet in zip(lecteur.pages, billets, strict=True):
            texte = " ".join(page.extract_text().split())
            self.assertIn(self.reservation.seance.film.titre, texte)
            self.assertIn(self.reservation.seance.salle.nom, texte)
            self.assertIn(f"Place A{billet.reservation_place.place.numero}", texte)
            self.assertIn(str(billet.reference), texte)

    def test_piece_jointe_exclut_billet_annule(self):
        billet_annule = Billet.objects.order_by("pk").first()
        Billet.objects.filter(pk=billet_annule.pk).update(statut=Billet.Statut.ANNULE)
        billet_valide = Billet.objects.get(statut=Billet.Statut.VALIDE)

        self.assertTrue(envoyer_billets(self.reservation.pk))

        piece = mail.outbox[0].attachments[0]
        lecteur = PdfReader(BytesIO(piece.content), strict=True)
        self.assertEqual(len(lecteur.pages), 1)
        texte = lecteur.pages[0].extract_text()
        self.assertNotIn(str(billet_annule.reference), texte)
        self.assertIn(str(billet_valide.reference), texte)
        self.assertIn("Place A2", texte)

    def test_echec_generation_pdf_conserve_paiement_et_permet_relance(self):
        billets_initiaux = list(Billet.objects.order_by("pk").values())
        with patch("reservations.emails.generer_pdf_billets", side_effect=RuntimeError("Export interrompu")):
            with patch("reservations.emails.EmailMultiAlternatives.send") as envoi_message:
                with self.assertLogs("reservations.emails", level="WARNING"):
                    self.assertFalse(envoyer_billets(self.reservation.pk))

        envoi_message.assert_not_called()
        self.assertEqual(len(mail.outbox), 0)
        envoi = EnvoiBillets.objects.get()
        self.assertEqual(envoi.statut, EnvoiBillets.Statut.ECHOUE)
        self.assertIsNone(envoi.date_envoi)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.CONFIRMEE)
        self.assertEqual(self.reservation.paiements.get().statut, Paiement.Statut.REUSSI)
        self.assertEqual(list(Billet.objects.order_by("pk").values()), billets_initiaux)

        with patch("reservations.emails.timezone.now", return_value=envoi.date_derniere_tentative + timedelta(seconds=61)):
            self.assertTrue(envoyer_billets(self.reservation.pk, renvoi=True))

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(len(mail.outbox[0].attachments), 1)
        self.assertEqual(EnvoiBillets.objects.get().statut, EnvoiBillets.Statut.ENVOYE)
        self.assertEqual(list(Billet.objects.order_by("pk").values()), billets_initiaux)

    def test_envoi_differe_et_demande_conservee_en_base(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            with transaction.atomic():
                programmer_envoi_billets(self.reservation)
                self.assertEqual(len(mail.outbox), 0)
                self.assertEqual(EnvoiBillets.objects.get().statut, EnvoiBillets.Statut.EN_ATTENTE)

        self.assertEqual(len(callbacks), 1)
        callbacks[0]()
        self.assertEqual(len(mail.outbox), 1)

    def test_transaction_annulee_ne_conserve_ni_demande_ni_email(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    programmer_envoi_billets(self.reservation)
                    raise RuntimeError("Transaction interrompue")

        self.assertEqual(callbacks, [])
        self.assertFalse(EnvoiBillets.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_programmation_repetee_ne_multiplie_pas_envois(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            programmer_envoi_billets(self.reservation)
            programmer_envoi_billets(self.reservation)
        with self.captureOnCommitCallbacks(execute=True) as suivantes:
            programmer_envoi_billets(self.reservation)

        self.assertEqual(len(callbacks), 1)
        self.assertEqual(suivantes, [])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(EnvoiBillets.objects.count(), 1)
        self.assertFalse(envoyer_billets(self.reservation.pk))
        self.assertEqual(len(mail.outbox), 1)

    def test_panne_email_conserve_paiement_et_billets_sans_secret_dans_logs(self):
        billets = list(Billet.objects.order_by("pk").values())
        with patch(
            "reservations.emails.EmailMultiAlternatives.send",
            side_effect=SMTPException("mot_de_passe_smtp_factice"),
        ):
            with self.assertLogs("reservations.emails", level="WARNING") as journal:
                self.assertFalse(envoyer_billets(self.reservation.pk))

        self.reservation.refresh_from_db()
        envoi = EnvoiBillets.objects.get()
        self.assertEqual(envoi.statut, EnvoiBillets.Statut.ECHOUE)
        self.assertIsNone(envoi.date_envoi)
        self.assertEqual(self.reservation.statut, Reservation.Statut.CONFIRMEE)
        self.assertEqual(self.reservation.paiements.get().statut, Paiement.Statut.REUSSI)
        self.assertEqual(list(Billet.objects.order_by("pk").values()), billets)
        self.assertEqual(self.client.get(creer_url_billets(self.reservation)).status_code, 200)
        self.assertNotIn("mot_de_passe_smtp_factice", " ".join(journal.output))
        self.assertNotIn(self.reservation.email, " ".join(journal.output))
        self.assertIn("SMTPException", " ".join(journal.output))

    def test_service_mail_retournant_zero_signale_echec(self):
        with patch("reservations.emails.EmailMultiAlternatives.send", return_value=0):
            with self.assertLogs("reservations.emails", level="WARNING"):
                self.assertFalse(envoyer_billets(self.reservation.pk))

        self.assertEqual(EnvoiBillets.objects.get().statut, EnvoiBillets.Statut.ECHOUE)

    @override_settings(SITE_URL="https://cinema.example.com?redirection=autre")
    def test_url_site_invalide_ne_produit_pas_email(self):
        with self.assertLogs("reservations.emails", level="WARNING"):
            self.assertFalse(envoyer_billets(self.reservation.pk))

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(EnvoiBillets.objects.get().statut, EnvoiBillets.Statut.ECHOUE)

    def test_delai_une_minute_et_relance_conservent_memes_billets(self):
        self.assertTrue(envoyer_billets(self.reservation.pk))
        envoi = EnvoiBillets.objects.get()
        billets = list(Billet.objects.order_by("pk").values())
        with self.assertRaisesMessage(ValidationError, "Patientez une minute"):
            envoyer_billets(self.reservation.pk, renvoi=True)
        with patch("reservations.emails.timezone.now", return_value=envoi.date_derniere_tentative + timedelta(seconds=61)):
            self.assertTrue(envoyer_billets(self.reservation.pk, renvoi=True))

        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[0].body, mail.outbox[1].body)
        for message in mail.outbox:
            self.assertEqual(len(message.attachments), 1)
            lecteur = PdfReader(BytesIO(message.attachments[0].content), strict=True)
            self.assertEqual(len(lecteur.pages), 2)
            texte = " ".join(page.extract_text() for page in lecteur.pages)
            for billet in billets:
                self.assertIn(billet["reference"], texte)
        self.assertEqual(list(Billet.objects.order_by("pk").values()), billets)

    def test_envoi_interrompu_peut_etre_repris_apres_delai(self):
        EnvoiBillets.objects.create(
            reservation=self.reservation, statut=EnvoiBillets.Statut.EN_COURS,
            date_derniere_tentative=timezone.now() - timedelta(minutes=2),
        )

        self.assertTrue(envoyer_billets(self.reservation.pk, renvoi=True))

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(EnvoiBillets.objects.get().statut, EnvoiBillets.Statut.ENVOYE)

    def test_confirmation_affiche_formulaire_de_relance(self):
        page = self.client.get(self.detail)

        self.assertContains(page, "Recevoir mes billets par email")
        self.assertContains(page, f'action="{self.url}"')

    def test_relance_utilise_adresse_enregistree_et_ignore_email_soumis(self):
        page = self.client.post(self.url, {"email": "autre@example.com"}, follow=True)

        self.assertContains(page, "a été envoyé à l’adresse de la réservation")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.reservation.email])

    def test_relance_respecte_delai_et_affiche_message(self):
        self.client.post(self.url)

        page = self.client.post(self.url, follow=True)

        self.assertContains(page, "Patientez une minute")
        self.assertEqual(len(mail.outbox), 1)

    def test_relance_sans_session_get_et_csrf_refuses(self):
        self.assertEqual(Client().post(self.url).status_code, 404)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(Client(enforce_csrf_checks=True).post(self.url).status_code, 403)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(EnvoiBillets.objects.exists())

    def test_autre_reservation_dans_session_ne_donne_pas_droit_relance(self):
        session = self.client.session
        session["reservation_provisoire_id"] = self.reservation.pk + 1
        session.save()

        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.assertEqual(len(mail.outbox), 0)

    def test_paiement_non_reussi_refuse_envoi_et_masque_bouton(self):
        paiement = self.reservation.paiements.get()
        for statut in (Paiement.Statut.EN_ATTENTE, Paiement.Statut.ECHOUE, Paiement.Statut.REMBOURSE):
            with self.subTest(statut=statut):
                Paiement.objects.filter(pk=paiement.pk).update(statut=statut)
                with self.assertRaises(ValidationError):
                    envoyer_billets(self.reservation.pk)
                self.assertNotContains(self.client.get(self.detail), "Recevoir mes billets par email")
        self.assertEqual(len(mail.outbox), 0)

    def test_reservation_non_confirmee_refuse_envoi(self):
        for statut in (Reservation.Statut.EN_ATTENTE, Reservation.Statut.EXPIREE, Reservation.Statut.ANNULEE):
            with self.subTest(statut=statut):
                Reservation.objects.filter(pk=self.reservation.pk).update(statut=statut)
                with self.assertRaises(ValidationError):
                    envoyer_billets(self.reservation.pk)
                self.assertNotContains(self.client.get(self.detail), "Recevoir mes billets par email")
        self.assertEqual(len(mail.outbox), 0)

    def test_annulation_seance_refuse_relance(self):
        annuler_seance(self.reservation.seance)

        page = self.client.post(self.url, follow=True)

        self.assertContains(page, "Les billets de cette réservation ne sont pas disponibles")
        self.assertEqual(len(mail.outbox), 0)

    def test_absence_billets_valides_ne_permet_pas_envoi(self):
        Billet.objects.update(statut=Billet.Statut.ANNULE)

        with self.assertRaises(ValidationError):
            envoyer_billets(self.reservation.pk)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(EnvoiBillets.objects.exists())

    def test_echec_relance_affiche_erreur_sans_perdre_billets(self):
        with patch("reservations.emails.EmailMultiAlternatives.send", side_effect=SMTPException):
            with self.assertLogs("reservations.emails", level="WARNING"):
                page = self.client.post(self.url, follow=True)

        self.assertContains(page, "L’envoi de l’email a échoué")
        self.assertContains(page, "Voir mes billets")
        self.assertEqual(Billet.objects.filter(statut=Billet.Statut.VALIDE).count(), 2)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_console_affiche_email_et_message_local_sans_reception_reelle(self):
        sortie = StringIO()
        with redirect_stdout(sortie):
            page = self.client.post(self.url, follow=True)

        self.assertIn("To: spectateur@example.com", sortie.getvalue())
        self.assertIn("https://cinema.example.com", sortie.getvalue())
        self.assertContains(page, "affiché dans le terminal du serveur")
        self.assertEqual(EnvoiBillets.objects.get().statut, EnvoiBillets.Statut.ENVOYE)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    SITE_URL="https://cinema.example.com",
)
class EmailApresTransactionTests(TransactionTestCase):
    def test_confirmation_et_billets_commites_avant_contact_mail(self):
        reservation = creer_reservation_email(confirmee=False)

        def verifier_envoi(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            reservation.refresh_from_db()
            self.assertEqual(reservation.statut, Reservation.Statut.CONFIRMEE)
            self.assertEqual(reservation.paiements.get().statut, Paiement.Statut.REUSSI)
            self.assertEqual(Billet.objects.count(), 2)
            self.assertEqual(EnvoiBillets.objects.get().statut, EnvoiBillets.Statut.EN_COURS)
            return 1

        with patch("reservations.emails.EmailMultiAlternatives.send", side_effect=verifier_envoi) as envoyer:
            traiter_session_checkout("checkout.session.completed", session_payee(reservation))
            traiter_session_checkout("checkout.session.completed", session_payee(reservation))

        envoyer.assert_called_once()
        self.assertEqual(EnvoiBillets.objects.get().statut, EnvoiBillets.Statut.ENVOYE)

    def test_panne_apres_confirmation_ne_fait_pas_echouer_paiement_et_se_reprend(self):
        reservation = creer_reservation_email(confirmee=False)
        with patch("reservations.emails.EmailMultiAlternatives.send", side_effect=SMTPException):
            with self.assertLogs("reservations.emails", level="WARNING"):
                traiter_session_checkout("checkout.session.completed", session_payee(reservation))

        reservation.refresh_from_db()
        envoi = EnvoiBillets.objects.get()
        self.assertEqual(reservation.statut, Reservation.Statut.CONFIRMEE)
        self.assertEqual(reservation.paiements.get().statut, Paiement.Statut.REUSSI)
        self.assertEqual(envoi.statut, EnvoiBillets.Statut.ECHOUE)
        traiter_session_checkout("checkout.session.completed", session_payee(reservation))
        self.assertEqual(len(mail.outbox), 0)
        with patch("reservations.emails.timezone.now", return_value=envoi.date_derniere_tentative + timedelta(seconds=61)):
            self.assertTrue(envoyer_billets(reservation.pk, renvoi=True))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(Billet.objects.count(), 2)

    def test_deux_relances_simultanees_ne_declenchent_qu_un_envoi(self):
        reservation = creer_reservation_email()
        EnvoiBillets.objects.create(reservation=reservation)
        rendez_vous = Barrier(2, timeout=10)

        def relancer():
            close_old_connections()
            try:
                rendez_vous.wait()
                try:
                    return envoyer_billets(reservation.pk, renvoi=True)
                except ValidationError:
                    return False
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executant:
            resultats = list(executant.map(lambda _: relancer(), range(2)))

        self.assertEqual(sorted(resultats), [False, True])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(EnvoiBillets.objects.get().statut, EnvoiBillets.Statut.ENVOYE)
