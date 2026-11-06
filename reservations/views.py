from math import ceil

from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_safe
from stripe import StripeError

from cinema.models import Seance
from cinema.plan_salle import organiser_plan
from paiements.models import Paiement
from paiements.services import DUREE_PAIEMENT_CHECKOUT, actualiser_paiement
from .emails import envoyer_billets
from .forms import ReservationProvisoireForm, SelectionPlacesForm
from .models import EnvoiBillets, Reservation
from .pdf import generer_pdf_billets
from .services import (
    SALT_LIEN_BILLETS, creer_reservation_provisoire, creer_url_billets,
    obtenir_billets_valides, obtenir_disponibilites,
    reservations_a_expirer, reservations_provisoires_actives,
)


def _seance_publique(pk, maintenant):
    return get_object_or_404(
        Seance.objects.select_related("film", "salle"), pk=pk,
        statut=Seance.Statut.PROGRAMMEE, debut__gt=maintenant,
        film__actif=True, salle__active=True,
    )


def _afficher_plan(request, seance, maintenant, formulaire):
    disponibilites = obtenir_disponibilites(seance, maintenant)
    soumis = request.POST.getlist("places")
    for rangee in disponibilites["rangees"]:
        for place in rangee["places"]:
            place["selectionnee"] = (len(soumis) <= 8 and str(place["id"]) in soumis
                                      and place["statut"] == "disponible")
    disponibilites["rangees"] = organiser_plan(disponibilites["rangees"])
    return render(request, "cinema/detail_seance.html", {
        "seance": seance, "disponibilites": disponibilites,
        "actualise_a": maintenant, "erreurs_selection": formulaire.errors,
    }, status=400)


@never_cache
@require_POST
def valider_selection(request, pk):
    maintenant = timezone.now()
    seance = _seance_publique(pk, maintenant)
    formulaire = SelectionPlacesForm(request.POST, seance=seance, maintenant=maintenant)
    if not formulaire.is_valid():
        return _afficher_plan(request, seance, maintenant, formulaire)
    places = formulaire.cleaned_data["places"]
    return render(request, "reservations/selection.html", {
        "seance": seance, "places": places, "total": seance.prix * len(places),
        "formulaire": ReservationProvisoireForm(
            initial={"places": [place.pk for place in places]}, seance=seance, maintenant=maintenant,
        ),
    })


@never_cache
@require_POST
def reserver_places(request, pk):
    maintenant = timezone.now()
    # Une réservation encore active est réaffichée plutôt que prolongée au rechargement.
    en_cours = reservations_provisoires_actives(maintenant).filter(
        pk=request.session.get("reservation_provisoire_id"),
    ).first()
    if en_cours:
        return redirect("reservations:detail_reservation", pk=en_cours.pk)
    seance = _seance_publique(pk, maintenant)
    formulaire = ReservationProvisoireForm(request.POST, seance=seance, maintenant=maintenant)
    if not formulaire.is_valid():
        if "places" in formulaire.errors:
            return _afficher_plan(request, seance, maintenant, formulaire)
        places = formulaire.cleaned_data["places"]
        return render(request, "reservations/selection.html", {
            "seance": seance, "places": places, "total": seance.prix * len(places),
            "formulaire": formulaire,
        }, status=400)
    try:
        reservation = creer_reservation_provisoire(
            seance, [place.pk for place in formulaire.cleaned_data["places"]], formulaire.cleaned_data["email"],
        )
    except ValidationError as erreur:
        formulaire.add_error(None, erreur)
        return _afficher_plan(request, seance, timezone.now(), formulaire)
    request.session["reservation_provisoire_id"] = reservation.pk
    return redirect("reservations:detail_reservation", pk=reservation.pk)


@never_cache
@require_safe
def detail_reservation(request, pk):
    reservation = get_object_or_404(
        Reservation.objects.select_related("seance__film", "seance__salle").prefetch_related("places__place"),
        pk=pk if request.session.get("reservation_provisoire_id") == pk else None,
    )
    maintenant = timezone.now()
    paiement = reservation.paiements.filter(statut=Paiement.Statut.EN_ATTENTE).order_by("-pk").first()
    retour = request.GET.get("retour")
    if (paiement and paiement.stripe_session_id and paiement.date_expiration
            and (retour in ("succes", "annulation", "verification")
                 or reservation.date_expiration <= maintenant)):
        try:
            actualiser_paiement(paiement)
        except (StripeError, ImproperlyConfigured, ValidationError):
            messages.error(request, "La vérification du paiement est momentanément indisponible. Actualisez la réservation.")
        reservation.refresh_from_db()
        paiement.refresh_from_db()
        if paiement.statut != Paiement.Statut.EN_ATTENTE:
            paiement = None
    if reservation.statut == Reservation.Statut.EN_ATTENTE and reservation.date_expiration <= maintenant:
        reservations_a_expirer(maintenant).filter(pk=pk).update(statut=Reservation.Statut.EXPIREE)
        reservation.refresh_from_db()
    active = reservation.statut == Reservation.Statut.EN_ATTENTE and reservation.date_expiration > maintenant
    seance = reservation.seance
    paiement_possible = (
        reservation.statut == Reservation.Statut.EN_ATTENTE
        and seance.statut == Seance.Statut.PROGRAMMEE and seance.debut > maintenant
        and seance.film.actif and seance.salle.active
        and (paiement is not None or (
            active and seance.debut >= maintenant + DUREE_PAIEMENT_CHECKOUT
            and reservation.montant_total > 0
        ))
    )
    billets_disponibles = obtenir_billets_valides(reservation).exists()
    return render(request, "reservations/detail_reservation.html", {
        "reservation": reservation,
        "url_billets": creer_url_billets(reservation) if billets_disponibles else None,
        "envoi_billets": EnvoiBillets.objects.filter(reservation=reservation).first() if billets_disponibles else None,
        "email_console": settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend",
        "active": active,
        "paiement_possible": paiement_possible,
        "paiement_deja_demarre": paiement is not None,
        "retour_annulation": paiement is not None and retour == "annulation",
        "retour_paiement": paiement is not None and retour in ("succes", "verification"),
        "secondes_restantes": max(0, ceil((reservation.date_expiration - maintenant).total_seconds())),
        "verification_paiement": (reservation.statut == Reservation.Statut.EN_ATTENTE
                                 and reservation.date_expiration <= maintenant),
    })


def _reservation_et_billets(jeton):
    """Partage les contrôles du lien et des statuts entre la page et le PDF."""
    try:
        pk = int(signing.Signer(salt=SALT_LIEN_BILLETS).unsign(jeton))
    except (signing.BadSignature, ValueError):
        raise Http404("Ce lien de billets est invalide.") from None
    if not 0 < pk < 2**63:
        raise Http404("Ce lien de billets est invalide.")

    reservation = get_object_or_404(
        Reservation.objects.select_related("seance__film", "seance__salle"), pk=pk,
    )
    billets = list(obtenir_billets_valides(reservation))
    if not billets:
        raise Http404("Ces billets ne sont pas disponibles.")

    return reservation, billets


def _proteger_reponse_billets(response):
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@never_cache
@require_safe
def consulter_billets(request, jeton):
    reservation, billets = _reservation_et_billets(jeton)
    return _proteger_reponse_billets(render(request, "reservations/billets.html", {
        "reservation": reservation, "billets": billets,
        "url_pdf": reverse("reservations:telecharger_billets", kwargs={"jeton": jeton}),
    }))


@never_cache
@require_safe
def telecharger_billets(request, jeton):
    reservation, billets = _reservation_et_billets(jeton)
    response = HttpResponse(generer_pdf_billets(reservation, billets), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="billets-reservation-{reservation.pk}.pdf"'
    return _proteger_reponse_billets(response)


@never_cache
@require_POST
def renvoyer_email_billets(request, pk):
    reservation = get_object_or_404(
        Reservation, pk=pk if request.session.get("reservation_provisoire_id") == pk else None,
    )
    try:
        envoye = envoyer_billets(reservation.pk, renvoi=True)
    except ValidationError as erreur:
        messages.error(request, " ".join(erreur.messages))
    else:
        if not envoye:
            messages.error(request, "L’envoi de l’email a échoué. Vos billets restent disponibles sur le site.")
        elif settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend":
            messages.success(request, "Le lien vers vos billets a été envoyé à l’adresse de la réservation.")
        else:
            messages.success(request, "Le lien vers vos billets a été envoyé à l’adresse de la réservation.")
    return redirect("reservations:detail_reservation", pk=reservation.pk)
