"""Sessions Stripe Checkout et suivi des paiements après annulation."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from stripe import StripeClient

from .models import Paiement


def creer_session_checkout(
    reservation, *, date_expiration, url_succes, url_annulation, cle_idempotence,
):
    """Demande une session ; l'appelant gère son enregistrement et le blocage des places.

    L'échéance et la clé sont fournies par l'appelant pour conserver les mêmes
    paramètres lors d'une nouvelle tentative après une erreur réseau.
    """
    if not settings.STRIPE_SECRET_KEY:
        raise ImproperlyConfigured("La clé secrète Stripe n'est pas configurée.")

    client = StripeClient(settings.STRIPE_SECRET_KEY)
    return client.v1.checkout.sessions.create(
        {
            "mode": "payment",
            "payment_method_types": ["card"],
            "customer_email": reservation.email,
            "client_reference_id": str(reservation.pk),
            "metadata": {"reservation_id": str(reservation.pk)},
            "line_items": [{
                "price_data": {
                    "currency": "eur",
                    "unit_amount": int(reservation.montant_total * 100),
                    "product_data": {"name": f"Réservation cinéma n°{reservation.pk}"},
                },
                "quantity": 1,
            }],
            "expires_at": int(date_expiration.timestamp()),
            "success_url": url_succes,
            "cancel_url": url_annulation,
        },
        options={"idempotency_key": cle_idempotence},
    )


def compter_paiements_a_traiter(seance):
    """Compte les transactions à vérifier manuellement, sans modifier leur statut."""
    return Paiement.objects.filter(
        reservation__seance=seance,
        statut__in=(Paiement.Statut.REUSSI, Paiement.Statut.EN_ATTENTE),
    ).count()
