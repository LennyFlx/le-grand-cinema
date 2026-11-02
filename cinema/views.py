from datetime import date, datetime, time, timedelta

from django.db.models import F, Prefetch
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date

from .models import Film, Seance


def films_a_l_affiche(request):
    maintenant = timezone.now()
    aujourd_hui = timezone.localdate(maintenant)
    jours = [aujourd_hui + timedelta(days=ecart) for ecart in range(7)]
    try:
        jour_selectionne = parse_date(request.GET.get("date", ""))
    except ValueError:
        jour_selectionne = None
    if jour_selectionne is None or jour_selectionne < aujourd_hui or jour_selectionne == date.max:
        jour_selectionne = aujourd_hui

    debut_jour = timezone.make_aware(datetime.combine(jour_selectionne, time.min))
    fin_jour = timezone.make_aware(
        datetime.combine(jour_selectionne + timedelta(days=1), time.min)
    )
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
    return render(request, "cinema/films.html", {
        "films": films,
        "jours": jours,
        "aujourd_hui": aujourd_hui,
        "jour_selectionne": jour_selectionne,
        "date_personnalisee": jour_selectionne not in jours,
        "afficher_calendrier": jour_selectionne not in jours or request.GET.get("calendrier") == "1",
    })
