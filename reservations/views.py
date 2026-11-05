from math import ceil

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_safe

from cinema.models import Seance
from cinema.plan_salle import organiser_plan
from .forms import ReservationProvisoireForm, SelectionPlacesForm
from .models import Reservation
from .services import creer_reservation_provisoire, obtenir_disponibilites


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
    en_cours = Reservation.objects.filter(
        pk=request.session.get("reservation_provisoire_id"),
        statut=Reservation.Statut.EN_ATTENTE, date_expiration__gt=maintenant,
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
    if reservation.statut == Reservation.Statut.EN_ATTENTE and reservation.date_expiration <= maintenant:
        Reservation.objects.filter(pk=pk, statut=Reservation.Statut.EN_ATTENTE,
                                   date_expiration__lte=maintenant).update(statut=Reservation.Statut.EXPIREE)
        reservation.refresh_from_db()
    return render(request, "reservations/detail_reservation.html", {
        "reservation": reservation,
        "active": reservation.statut == Reservation.Statut.EN_ATTENTE and reservation.date_expiration > maintenant,
        "secondes_restantes": max(0, ceil((reservation.date_expiration - maintenant).total_seconds())),
    })
