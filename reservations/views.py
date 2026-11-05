from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from cinema.models import Seance
from cinema.plan_salle import organiser_plan
from .forms import SelectionPlacesForm
from .services import obtenir_disponibilites


@never_cache
@require_POST
def valider_selection(request, pk):
    maintenant = timezone.now()
    seance = get_object_or_404(
        Seance.objects.select_related("film", "salle"), pk=pk,
        statut=Seance.Statut.PROGRAMMEE, debut__gt=maintenant,
        film__actif=True, salle__active=True,
    )
    formulaire = SelectionPlacesForm(request.POST, seance=seance, maintenant=maintenant)
    if formulaire.is_valid():
        places = formulaire.cleaned_data["places"]
        return render(request, "reservations/selection.html", {
            "seance": seance, "places": places, "total": seance.prix * len(places),
        })
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
