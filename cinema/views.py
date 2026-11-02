from django.shortcuts import render
from django.utils import timezone

from .models import Film, Seance


def films_a_l_affiche(request):
    films = Film.objects.filter(
        actif=True,
        seances__statut=Seance.Statut.PROGRAMMEE,
        seances__debut__gt=timezone.now(),
        seances__salle__active=True,
    ).distinct()
    return render(request, "cinema/films.html", {"films": films})
