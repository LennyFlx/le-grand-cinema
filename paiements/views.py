import logging

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from stripe import APIConnectionError, InvalidRequestError, SignatureVerificationError, StripeError, Webhook

from reservations.models import Reservation

from .services import demarrer_paiement, traiter_session_checkout

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def webhook_stripe(request):
    if not settings.STRIPE_WEBHOOK_SECRET:
        return HttpResponse(status=503)
    try:
        evenement = Webhook.construct_event(
            payload=request.body,
            sig_header=request.headers.get("Stripe-Signature", ""),
            secret=settings.STRIPE_WEBHOOK_SECRET,
        ).to_dict()
    except (ValueError, SignatureVerificationError):
        return HttpResponse(status=400)

    if evenement["type"] in ("checkout.session.completed", "checkout.session.expired"):
        try:
            traiter_session_checkout(evenement["type"], evenement["data"]["object"])
        except ValidationError:
            return HttpResponse(status=400)
    return HttpResponse(status=200)


@never_cache
@require_POST
def payer_reservation(request, pk):
    reservation = get_object_or_404(
        Reservation, pk=pk if request.session.get("reservation_provisoire_id") == pk else None,
    )
    url_retour = request.build_absolute_uri(
        reverse("reservations:detail_reservation", args=[reservation.pk]),
    )
    try:
        url_checkout = demarrer_paiement(reservation.pk, url_retour)
    except ImproperlyConfigured:
        messages.error(request, "Le paiement en ligne est momentanément indisponible.")
    except ValidationError as erreur:
        messages.error(request, " ".join(erreur.messages))
    except StripeError as erreur:
        logger.warning(
            "Refus Stripe : type=%s code=%s parametre=%s statut_http=%s",
            type(erreur).__name__, getattr(erreur, "code", None),
            getattr(erreur, "param", None), erreur.http_status,
        )
        if isinstance(erreur, APIConnectionError):
            message = "Impossible de joindre Stripe. Réessayez : votre réservation est conservée."
        elif isinstance(erreur, InvalidRequestError) and erreur.param == "expires_at":
            message = "La tentative de paiement a expiré avant son ouverture. Réessayez."
        else:
            message = "Stripe n’a pas accepté la préparation du paiement. Veuillez réessayer."
        messages.error(request, message)
    else:
        if url_checkout:
            return HttpResponse(status=303, headers={"Location": url_checkout})
    return redirect("reservations:detail_reservation", pk=reservation.pk)
