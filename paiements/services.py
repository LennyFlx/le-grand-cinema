"""Sessions Stripe Checkout et suivi des paiements après annulation."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.utils import timezone
from stripe import StripeClient

from cinema.models import Salle, Seance
from reservations.models import Reservation

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


@transaction.atomic
def traiter_session_checkout(type_evenement, session):
    """Applique un résultat Stripe authentifié, une seule fois par paiement."""
    paiement = (Paiement.objects.select_related("reservation__seance")
                .filter(stripe_session_id=session["id"]).first())
    if paiement is None:
        return

    # Même ordre que le blocage des places et l'annulation des séances.
    Salle.objects.select_for_update().get(pk=paiement.reservation.seance.salle_id)
    seance = Seance.objects.select_for_update().get(pk=paiement.reservation.seance_id)
    reservation = Reservation.objects.select_for_update().get(pk=paiement.reservation_id)
    paiement = Paiement.objects.select_for_update().get(pk=paiement.pk)

    if (session.get("mode") != "payment" or session.get("currency") != "eur"
            or type(session.get("amount_total")) is not int
            or session["amount_total"] != int(paiement.montant * 100)
            or paiement.montant != reservation.montant_total
            or session.get("client_reference_id") != str(reservation.pk)
            or session.get("metadata", {}).get("reservation_id") != str(reservation.pk)):
        raise ValidationError("La session Stripe ne correspond pas au paiement enregistré.")

    if paiement.statut in (Paiement.Statut.REUSSI, Paiement.Statut.REMBOURSE):
        return

    if type_evenement == "checkout.session.completed":
        if session.get("status") != "complete" or session.get("payment_status") != "paid":
            return
        identifiant = session.get("payment_intent")
        if not isinstance(identifiant, str) or not identifiant:
            raise ValidationError("Le paiement Stripe confirmé ne possède pas d'identifiant.")
        paiement.statut = Paiement.Statut.REUSSI
        paiement.stripe_payment_intent_id = identifiant
        paiement.date_confirmation = timezone.now()
        paiement.save(update_fields=["statut", "stripe_payment_intent_id", "date_confirmation"])
        # Un résultat tardif ne réactive jamais une réservation déjà annulée ou expirée.
        if reservation.statut == Reservation.Statut.EN_ATTENTE and seance.statut != Seance.Statut.ANNULEE:
            reservation.statut = Reservation.Statut.CONFIRMEE
            reservation.save(update_fields=["statut"])

    elif type_evenement == "checkout.session.expired":
        if session.get("status") != "expired" or session.get("payment_status") != "unpaid":
            return
        paiement.statut = Paiement.Statut.ECHOUE
        paiement.save(update_fields=["statut"])
        autres_paiements = reservation.paiements.filter(
            statut__in=(Paiement.Statut.EN_ATTENTE, Paiement.Statut.REUSSI),
        ).exists()
        if reservation.statut == Reservation.Statut.EN_ATTENTE and not autres_paiements:
            reservation.statut = Reservation.Statut.EXPIREE
            reservation.save(update_fields=["statut"])
