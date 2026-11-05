from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from stripe import SignatureVerificationError, Webhook

from .services import traiter_session_checkout


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
