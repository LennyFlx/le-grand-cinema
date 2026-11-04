from datetime import date, datetime, time, timedelta

from django.db.models import F, Prefetch
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from .models import Film, Participation, Seance
from reservations.services import obtenir_disponibilites
from .plan_salle import organiser_plan


def _calendrier(request, maintenant):
    aujourd_hui = timezone.localdate(maintenant)
    jours = [aujourd_hui + timedelta(days=ecart) for ecart in range(7)]
    try:
        jour_selectionne = parse_date(request.GET.get("date", ""))
    except ValueError:
        jour_selectionne = None
    if jour_selectionne is None or jour_selectionne < aujourd_hui or jour_selectionne == date.max:
        jour_selectionne = aujourd_hui
    return {
        "jours": jours,
        "aujourd_hui": aujourd_hui,
        "jour_selectionne": jour_selectionne,
        "date_personnalisee": jour_selectionne not in jours,
        "afficher_calendrier": jour_selectionne not in jours or request.GET.get("calendrier") == "1",
    }


def _bornes_jour(jour):
    return (
        timezone.make_aware(datetime.combine(jour, time.min)),
        timezone.make_aware(datetime.combine(jour + timedelta(days=1), time.min)),
    )


def films_a_l_affiche(request):
    maintenant = timezone.now()
    calendrier = _calendrier(request, maintenant)
    debut_jour, fin_jour = _bornes_jour(calendrier["jour_selectionne"])
    seances = Seance.objects.filter(
        statut=Seance.Statut.PROGRAMMEE,
        salle__active=True,
        debut__gte=debut_jour,
        debut__lt=fin_jour,
        debut__gt=maintenant,
    )
    films = (
        Film.objects.filter(actif=True, pk__in=seances.values("film_id"))
        .order_by(F("date_sortie").desc(nulls_last=True), "titre", "pk")
        .prefetch_related(Prefetch(
            "seances",
            queryset=seances.order_by("debut", "pk"),
            to_attr="seances_du_jour",
        ))
    )
    return render(request, "cinema/films.html", {"films": films, **calendrier})


def detail_film(request, pk):
    maintenant = timezone.now()
    calendrier = _calendrier(request, maintenant)
    catalogue = Film.objects.filter(actif=True).prefetch_related(
        "genres",
        Prefetch(
            "participations",
            queryset=Participation.objects.select_related("personne").order_by(
                "personne__nom", "personne__prenom", "pk",
            ),
            to_attr="equipe",
        ),
    )
    film = get_object_or_404(catalogue, pk=pk)
    premier_jour_ulterieur = calendrier["aujourd_hui"] + timedelta(days=7)
    debut_semaine, _ = _bornes_jour(calendrier["aujourd_hui"])
    debut_ulterieur, _ = _bornes_jour(premier_jour_ulterieur)
    prochaines_seances = film.seances.filter(
        statut=Seance.Statut.PROGRAMMEE,
        salle__active=True,
        debut__gt=maintenant,
    )
    seances_semaine = list(
        prochaines_seances.filter(debut__gte=debut_semaine, debut__lt=debut_ulterieur)
        .select_related("salle").order_by("debut", "salle__nom", "pk")
    )
    groupes = {}
    for seance in seances_semaine:
        jour = timezone.localdate(seance.debut)
        groupes.setdefault(jour, []).append(seance)
    seances_par_jour = [{"jour": jour, "seances": seances} for jour, seances in groupes.items()]

    afficher_date_ulterieure = calendrier["jour_selectionne"] >= premier_jour_ulterieur
    seances_ulterieures = []
    if afficher_date_ulterieure:
        debut_jour, fin_jour = _bornes_jour(calendrier["jour_selectionne"])
        seances_ulterieures = list(
            prochaines_seances.filter(debut__gte=debut_jour, debut__lt=fin_jour)
            .select_related("salle").order_by("debut", "salle__nom", "pk")
        )
    a_des_seances_a_venir = bool(seances_semaine or seances_ulterieures) or prochaines_seances.exists()
    return render(request, "cinema/detail_film.html", {
        "film": film,
        **calendrier,
        "seances_par_jour": seances_par_jour,
        "dernier_jour_semaine": premier_jour_ulterieur - timedelta(days=1),
        "premier_jour_ulterieur": premier_jour_ulterieur,
        "jour_calendrier": calendrier["jour_selectionne"] if afficher_date_ulterieure else premier_jour_ulterieur,
        "afficher_date_ulterieure": afficher_date_ulterieure,
        "seances_ulterieures": seances_ulterieures,
        "a_des_seances_a_venir": a_des_seances_a_venir,
        "realisateurs": [p.personne for p in film.equipe if p.fonction == Participation.Fonction.REALISATEUR],
        "acteurs": [p.personne for p in film.equipe if p.fonction == Participation.Fonction.ACTEUR],
    })


@never_cache
@require_safe
def detail_seance(request, pk):
    maintenant = timezone.now()
    seance = get_object_or_404(
        Seance.objects.select_related("film", "salle"),
        pk=pk, statut=Seance.Statut.PROGRAMMEE, debut__gt=maintenant,
        film__actif=True, salle__active=True,
    )
    disponibilites = obtenir_disponibilites(seance, maintenant)
    disponibilites["rangees"] = organiser_plan(disponibilites["rangees"])
    return render(request, "cinema/detail_seance.html", {
        "seance": seance,
        "disponibilites": disponibilites,
        "actualise_a": maintenant,
    })
