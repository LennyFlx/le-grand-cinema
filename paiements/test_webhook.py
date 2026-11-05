import hashlib
import hmac
import json
import time
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cinema.models import Film, Place, Salle, Seance
from reservations.models import Billet, Reservation, ReservationPlace
from reservations.services import (
    compter_places_disponibles, creer_reservation_provisoire, expirer_reservations,
)

from .models import Paiement

SECRET_WEBHOOK = "whsec_test_webhook"


@override_settings(STRIPE_WEBHOOK_SECRET=SECRET_WEBHOOK)
class WebhookStripeTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        film = Film.objects.create(titre="Film", duree=90)
        salle = Salle.objects.create(nom="Salle webhook", capacite=1)
        debut = timezone.now() + timedelta(days=1)
        self.seance = Seance.objects.create(
            film=film, salle=salle, debut=debut, fin=debut + timedelta(minutes=90),
            prix=Decimal("24.60"),
        )
        self.place = Place.objects.create(salle=salle, rangee="A", numero=1)
        self.reservation = Reservation.objects.create(
            seance=self.seance, email="spectateur@example.com",
            date_expiration=timezone.now() + timedelta(minutes=31),
            montant_total=Decimal("24.60"),
        )
        ReservationPlace.objects.create(
            reservation=self.reservation, place=self.place, prix_unitaire=Decimal("24.60"),
        )
        self.paiement = Paiement.objects.create(
            reservation=self.reservation, stripe_session_id="cs_test_webhook",
            montant=Decimal("24.60"),
        )
        self.url = reverse("paiements:webhook_stripe")

    def poster_corps(self, corps, signature=None):
        if signature is None:
            instant = str(int(time.time()))
            empreinte = hmac.new(
                SECRET_WEBHOOK.encode(), instant.encode() + b"." + corps, hashlib.sha256,
            ).hexdigest()
            signature = f"t={instant},v1={empreinte}"
        return self.client.post(
            self.url, corps, content_type="application/json", HTTP_STRIPE_SIGNATURE=signature,
        )

    def envoyer(self, type_evenement="checkout.session.completed", **modifications):
        session = {
            "id": self.paiement.stripe_session_id,
            "object": "checkout.session",
            "mode": "payment",
            "status": "complete",
            "payment_status": "paid",
            "currency": "eur",
            "amount_total": 2460,
            "client_reference_id": str(self.reservation.pk),
            "metadata": {"reservation_id": str(self.reservation.pk)},
            "payment_intent": "pi_test_webhook",
        }
        if type_evenement == "checkout.session.expired":
            session.update(status="expired", payment_status="unpaid", payment_intent=None)
        session.update(modifications)
        corps = json.dumps({
            "id": "evt_test_webhook", "object": "event", "type": type_evenement,
            "data": {"object": session},
        }).encode()
        return self.poster_corps(corps)

    def verifier_en_attente(self):
        self.paiement.refresh_from_db()
        self.reservation.refresh_from_db()
        self.assertEqual(self.paiement.statut, Paiement.Statut.EN_ATTENTE)
        self.assertEqual(self.reservation.statut, Reservation.Statut.EN_ATTENTE)

    def test_paiement_signe_confirme_sans_session_navigateur_ni_csrf(self):
        self.assertEqual(self.envoyer().status_code, 200)
        self.paiement.refresh_from_db()
        self.reservation.refresh_from_db()
        self.assertEqual(self.paiement.statut, Paiement.Statut.REUSSI)
        self.assertEqual(self.paiement.stripe_payment_intent_id, "pi_test_webhook")
        self.assertIsNotNone(self.paiement.date_confirmation)
        self.assertEqual(self.reservation.statut, Reservation.Statut.CONFIRMEE)
        self.assertEqual(compter_places_disponibles(self.seance), 0)
        self.assertFalse(Billet.objects.exists())

    def test_evenements_repetes_ne_reconfirment_pas_le_paiement(self):
        self.envoyer()
        self.paiement.refresh_from_db()
        date_confirmation = self.paiement.date_confirmation
        self.assertEqual(self.envoyer().status_code, 200)
        self.paiement.refresh_from_db()
        self.assertEqual(self.paiement.date_confirmation, date_confirmation)
        self.assertEqual(Paiement.objects.count(), 1)
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(self.envoyer("checkout.session.expired").status_code, 200)
        self.paiement.refresh_from_db()
        self.assertEqual(self.paiement.statut, Paiement.Statut.REUSSI)

    def test_signature_absente_ou_fausse_et_json_invalide_refuses(self):
        for signature in ("", "t=0,v1=faux"):
            self.assertEqual(self.poster_corps(b"{}", signature).status_code, 400)
        self.assertEqual(self.poster_corps(b"json invalide").status_code, 400)
        self.verifier_en_attente()

    @override_settings(STRIPE_WEBHOOK_SECRET="")
    def test_configuration_absente_signale_service_indisponible(self):
        self.assertEqual(self.envoyer().status_code, 503)
        self.verifier_en_attente()

    def test_montant_devise_et_rattachement_incorrects_refuses(self):
        for modification in (
            {"amount_total": 1}, {"currency": "usd"},
            {"client_reference_id": "autre"}, {"metadata": {"reservation_id": "autre"}},
            {"payment_intent": None}, {"mode": "subscription"},
        ):
            with self.subTest(modification=modification):
                self.assertEqual(self.envoyer(**modification).status_code, 400)
                self.verifier_en_attente()

    def test_session_non_payee_ne_confirme_pas_la_reservation(self):
        self.assertEqual(self.envoyer(payment_status="unpaid").status_code, 200)
        self.verifier_en_attente()

    def test_evenements_non_utilises_et_sessions_inconnues_ignores(self):
        self.assertEqual(self.envoyer("customer.created").status_code, 200)
        self.assertEqual(self.envoyer(id="cs_test_autre").status_code, 200)
        self.verifier_en_attente()
        self.assertEqual(Paiement.objects.count(), 1)

    def test_expiration_stripe_libere_les_places_et_supporte_les_doublons(self):
        for _ in range(2):
            self.assertEqual(self.envoyer("checkout.session.expired").status_code, 200)
        self.paiement.refresh_from_db()
        self.reservation.refresh_from_db()
        self.assertEqual(self.paiement.statut, Paiement.Statut.ECHOUE)
        self.assertEqual(self.reservation.statut, Reservation.Statut.EXPIREE)
        self.assertEqual(compter_places_disponibles(self.seance), 1)

    def test_retard_webhook_ne_libere_pas_les_places_avant_verification(self):
        Reservation.objects.filter(pk=self.reservation.pk).update(
            date_expiration=timezone.now() - timedelta(seconds=1),
        )
        self.assertEqual(expirer_reservations(), 0)
        self.assertEqual(compter_places_disponibles(self.seance), 0)
        with self.assertRaises(ValidationError):
            creer_reservation_provisoire(self.seance, [self.place.pk], "autre@example.com")
        self.assertEqual(self.envoyer().status_code, 200)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.CONFIRMEE)

    def test_page_ne_fait_pas_expirer_un_paiement_en_attente(self):
        Reservation.objects.filter(pk=self.reservation.pk).update(
            date_expiration=timezone.now() - timedelta(seconds=1),
        )
        session = self.client.session
        session["reservation_provisoire_id"] = self.reservation.pk
        session.save()
        page = self.client.get(reverse("reservations:detail_reservation", args=[self.reservation.pk]))
        self.assertContains(page, "Vérification du paiement en cours")
        self.assertNotContains(page, "data-compte-rebours")
        self.assertContains(page, "Reprendre ma réservation")
        self.verifier_en_attente()

    def test_expiration_une_tentative_ne_cloture_pas_une_autre_en_cours(self):
        Paiement.objects.create(
            reservation=self.reservation, stripe_session_id="cs_test_seconde",
            montant=self.paiement.montant,
        )
        self.envoyer("checkout.session.expired")
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.statut, Reservation.Statut.EN_ATTENTE)
        self.assertEqual(compter_places_disponibles(self.seance), 0)

    def test_reservation_annulee_ou_expiree_jamais_reactivee(self):
        for statut in (Reservation.Statut.ANNULEE, Reservation.Statut.EXPIREE):
            with self.subTest(statut=statut):
                Paiement.objects.filter(pk=self.paiement.pk).update(statut=Paiement.Statut.EN_ATTENTE)
                Reservation.objects.filter(pk=self.reservation.pk).update(statut=statut)
                self.assertEqual(self.envoyer().status_code, 200)
                self.reservation.refresh_from_db()
                self.paiement.refresh_from_db()
                self.assertEqual(self.reservation.statut, statut)
                self.assertEqual(self.paiement.statut, Paiement.Statut.REUSSI)

    def test_get_refuse(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
