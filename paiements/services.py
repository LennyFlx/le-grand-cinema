"""Sessions Stripe Checkout et suivi des paiements après annulation."""

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.utils import timezone
from stripe import InvalidRequestError, StripeClient

from cinema.models import Salle, Seance
from reservations.models import Reservation

from .models import Paiement


def _cle_idempotence(paiement):
    return f"checkout-{paiement.pk}-{paiement.date_creation.isoformat()}"


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
            "allowed_payment_method_types": ["card"],
            "customer_email": reservation.email,
            "client_reference_id": str(reservation.pk),
            "metadata": {
                "reservation_id": str(reservation.pk),
                "reference_paiement": cle_idempotence,
            },
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
    """Applique un résultat Stripe vérifié, une seule fois par paiement."""
    paiement = (Paiement.objects.select_related("reservation__seance")
                .filter(stripe_session_id=session["id"]).first())
    if paiement is None:
        # Stripe peut avoir créé la session malgré une réponse réseau perdue.
        metadata = session.get("metadata", {})
        try:
            reservation_id = int(metadata.get("reservation_id", ""))
        except (TypeError, ValueError):
            return
        if not 0 < reservation_id < 2**63:
            return
        candidates = Paiement.objects.filter(
            reservation_id=reservation_id, stripe_session_id__isnull=True,
            date_expiration__isnull=False, statut=Paiement.Statut.EN_ATTENTE,
        )
        paiement = next((
            candidat for candidat in candidates
            if metadata.get("reference_paiement") == _cle_idempotence(candidat)
        ), None)
        if paiement is None:
            return

    # Même ordre que le blocage des places et l'annulation des séances.
    Salle.objects.select_for_update().get(pk=paiement.reservation.seance.salle_id)
    seance = Seance.objects.select_for_update().get(pk=paiement.reservation.seance_id)
    reservation = Reservation.objects.select_for_update().get(pk=paiement.reservation_id)
    paiement = Paiement.objects.select_for_update().get(pk=paiement.pk)

    if (paiement.stripe_session_id not in (None, session["id"])
            or session.get("mode") != "payment" or session.get("currency") != "eur"
            or type(session.get("amount_total")) is not int
            or session["amount_total"] != int(paiement.montant * 100)
            or paiement.montant != reservation.montant_total
            or session.get("client_reference_id") != str(reservation.pk)
            or session.get("metadata", {}).get("reservation_id") != str(reservation.pk)):
        raise ValidationError("La session Stripe ne correspond pas au paiement enregistré.")

    if paiement.stripe_session_id is None:
        paiement.stripe_session_id = session["id"]
        paiement.save(update_fields=["stripe_session_id"])

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


DUREE_PAIEMENT_CHECKOUT = timedelta(minutes=31)


@transaction.atomic
def _preparer_paiement(reservation_id, url_retour):
    """Enregistre la tentative et son échéance avant tout appel réseau."""
    reference = Reservation.objects.select_related("seance").get(pk=reservation_id)
    Salle.objects.select_for_update().get(pk=reference.seance.salle_id)
    seance = (Seance.objects.select_for_update(of=("self",))
              .select_related("film", "salle").get(pk=reference.seance_id))
    reservation = Reservation.objects.select_for_update().get(pk=reservation_id)
    maintenant = timezone.now()
    if (reservation.statut != Reservation.Statut.EN_ATTENTE
            or seance.statut != Seance.Statut.PROGRAMMEE or seance.debut <= maintenant
            or not seance.film.actif or not seance.salle.active):
        raise ValidationError("Cette réservation ne peut plus être payée.")

    paiement = reservation.paiements.filter(statut=Paiement.Statut.EN_ATTENTE).order_by("-pk").first()
    if paiement is not None:
        return paiement

    if reservation.date_expiration <= maintenant:
        raise ValidationError("Le délai de réservation est écoulé.")
    if reservation.montant_total <= 0:
        raise ValidationError("Le paiement en ligne nécessite un montant strictement positif.")
    expiration = (maintenant + DUREE_PAIEMENT_CHECKOUT).replace(microsecond=0)
    if expiration > seance.debut:
        raise ValidationError("Le paiement en ligne ferme 31 minutes avant le début de la séance.")
    paiement = Paiement.objects.create(
        reservation=reservation, montant=reservation.montant_total,
        date_expiration=expiration, url_retour=url_retour,
    )
    reservation.date_expiration = expiration
    reservation.save(update_fields=["date_expiration"])
    return paiement


def actualiser_paiement(paiement):
    """Vérifie auprès de Stripe un résultat manquant, sans se fier à l'URL de retour."""
    client = StripeClient(settings.STRIPE_SECRET_KEY)
    session = client.v1.checkout.sessions.retrieve(paiement.stripe_session_id).to_dict()
    if session["status"] == "complete":
        traiter_session_checkout("checkout.session.completed", session)
    elif session["status"] == "expired":
        traiter_session_checkout("checkout.session.expired", session)
    return session


def demarrer_paiement(reservation_id, url_retour):
    """Crée ou reprend Checkout ; les appels Stripe restent hors des transactions."""
    if not settings.STRIPE_SECRET_KEY or not settings.STRIPE_WEBHOOK_SECRET:
        raise ImproperlyConfigured("Le paiement Stripe n'est pas encore configuré.")
    paiement = _preparer_paiement(reservation_id, url_retour)
    if paiement.stripe_session_id:
        session = actualiser_paiement(paiement)
    else:
        try:
            session = creer_session_checkout(
                paiement.reservation,
                date_expiration=paiement.date_expiration,
                url_succes=paiement.url_retour + "?retour=succes",
                url_annulation=paiement.url_retour + "?retour=annulation",
                cle_idempotence=_cle_idempotence(paiement),
            ).to_dict()
        except InvalidRequestError as erreur:
            # Une échéance refusée n'a pas créé de session : une nouvelle tentative est possible.
            if erreur.param == "expires_at":
                Paiement.objects.filter(
                    pk=paiement.pk, stripe_session_id__isnull=True,
                ).update(statut=Paiement.Statut.ECHOUE)
            raise
        Paiement.objects.filter(pk=paiement.pk).update(stripe_session_id=session["id"])

    disponible = Reservation.objects.filter(
        pk=reservation_id, statut=Reservation.Statut.EN_ATTENTE,
        seance__statut=Seance.Statut.PROGRAMMEE,
        seance__film__actif=True, seance__salle__active=True,
    ).exists()
    if session["status"] == "open":
        if not disponible:
            StripeClient(settings.STRIPE_SECRET_KEY).v1.checkout.sessions.expire(session["id"])
            return None
        return session["url"]
    if session["status"] == "complete":
        traiter_session_checkout("checkout.session.completed", session)
    elif session["status"] == "expired":
        traiter_session_checkout("checkout.session.expired", session)
    return None
