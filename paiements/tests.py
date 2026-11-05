from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from stripe import APIConnectionError
from stripe.params.checkout import SessionCreateParams

from cinema.models import Film, Salle, Seance
from reservations.models import Reservation

from .models import Paiement
from .services import compter_paiements_a_traiter, creer_session_checkout


class SuiviPaiementsTests(TestCase):
    def setUp(self):
        film = Film.objects.create(titre="Film", duree=90)
        salle = Salle.objects.create(nom="Salle 1", capacite=20)
        debut = timezone.now() + timedelta(days=1)
        self.seance = Seance.objects.create(
            film=film, salle=salle, debut=debut, fin=debut + timedelta(minutes=90), prix=10,
        )
        self.reservation = Reservation.objects.create(
            seance=self.seance, email="demo@example.com", date_expiration=debut, montant_total=10,
        )

    def test_seuls_paiements_reussis_et_en_attente_sont_a_traiter(self):
        for statut in Paiement.Statut.values:
            Paiement.objects.create(
                reservation=self.reservation, stripe_session_id=f"session-{statut}", montant=10, statut=statut,
            )
        etats_avant = list(Paiement.objects.order_by("pk").values_list("pk", "statut"))
        self.assertEqual(compter_paiements_a_traiter(self.seance), 2)
        self.assertEqual(list(Paiement.objects.order_by("pk").values_list("pk", "statut")), etats_avant)

    def test_paiements_des_autres_seances_ignores(self):
        debut = self.seance.debut + timedelta(days=1)
        autre = Seance.objects.create(
            film=self.seance.film, salle=self.seance.salle, debut=debut,
            fin=debut + timedelta(minutes=90), prix=10,
        )
        reservation = Reservation.objects.create(
            seance=autre, email="autre@example.com", date_expiration=debut, montant_total=10,
        )
        Paiement.objects.create(
            reservation=reservation, stripe_session_id="session-autre", montant=10, statut=Paiement.Statut.REUSSI,
        )
        self.assertEqual(compter_paiements_a_traiter(self.seance), 0)


@override_settings(STRIPE_SECRET_KEY="sk_test_exemple")
class SessionCheckoutTests(SimpleTestCase):
    def setUp(self):
        self.reservation = SimpleNamespace(
            pk=42, email="spectateur@example.com", montant_total=Decimal("24.60"),
        )
        self.parametres = {
            "date_expiration": timezone.now() + timedelta(minutes=31),
            "url_succes": "http://testserver/reservations/42/",
            "url_annulation": "http://testserver/reservations/42/",
            "cle_idempotence": "paiement-42",
        }

    @patch("paiements.services.StripeClient")
    def test_montant_exact_en_centimes_et_paiement_par_carte_en_euros(self, client_stripe):
        for montant, centimes in ((Decimal("24.60"), 2460), (Decimal("10.01"), 1001)):
            with self.subTest(montant=montant):
                self.reservation.montant_total = montant
                session = creer_session_checkout(self.reservation, **self.parametres)
                appel = client_stripe.return_value.v1.checkout.sessions.create
                donnees = appel.call_args.args[0]
                self.assertEqual(donnees["mode"], "payment")
                self.assertEqual(donnees["allowed_payment_method_types"], ["card"])
                ligne = donnees["line_items"][0]
                self.assertEqual(ligne["price_data"]["unit_amount"], centimes)
                self.assertEqual(ligne["price_data"]["currency"], "eur")
                self.assertEqual(ligne["quantity"], 1)
                self.assertEqual(donnees["metadata"]["reservation_id"], "42")
                self.assertEqual(donnees["customer_email"], self.reservation.email)
                self.assertIs(session, appel.return_value)

    @patch("paiements.services.StripeClient")
    def test_nouvelle_demande_conserve_echeance_et_cle_idempotence(self, client_stripe):
        creer_session_checkout(self.reservation, **self.parametres)
        creer_session_checkout(self.reservation, **self.parametres)
        appels = client_stripe.return_value.v1.checkout.sessions.create.call_args_list
        self.assertEqual(len(appels), 2)
        self.assertEqual(appels[0], appels[1])
        self.assertEqual(
            appels[0].args[0]["expires_at"],
            int(self.parametres["date_expiration"].timestamp()),
        )
        self.assertEqual(appels[0].kwargs["options"], {"idempotency_key": "paiement-42"})

    @override_settings(STRIPE_SECRET_KEY="")
    @patch("paiements.services.StripeClient")
    def test_absence_de_cle_refusee_avant_tout_appel_stripe(self, client_stripe):
        with self.assertRaisesMessage(ImproperlyConfigured, "clé secrète Stripe"):
            creer_session_checkout(self.reservation, **self.parametres)
        client_stripe.assert_not_called()

    @patch("paiements.services.StripeClient")
    def test_erreur_reseau_remontee_a_appelant(self, client_stripe):
        appel = client_stripe.return_value.v1.checkout.sessions.create
        appel.side_effect = APIConnectionError("Connexion interrompue")
        with self.assertRaises(APIConnectionError):
            creer_session_checkout(self.reservation, **self.parametres)

    @patch("paiements.services.StripeClient")
    def test_parametres_compatibles_avec_version_sdk_installee(self, client_stripe):
        creer_session_checkout(self.reservation, **self.parametres)
        parametres = client_stripe.return_value.v1.checkout.sessions.create.call_args.args[0]
        self.assertLessEqual(set(parametres), set(SessionCreateParams.__annotations__))
