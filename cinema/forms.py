from datetime import timedelta

from django import forms
from django.db import connection
from django.utils import timezone

from .models import Film, Salle, Seance


class SeanceAdminForm(forms.ModelForm):
    prix = forms.DecimalField(max_digits=8, decimal_places=2, min_value=0)

    class Meta:
        model = Seance
        fields = ("film", "salle", "debut", "prix")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["film"].queryset = Film.objects.filter(actif=True)
        self.fields["salle"].queryset = Salle.objects.filter(active=True)

    def clean(self):
        cleaned_data = super().clean()
        film = cleaned_data.get("film")
        debut = cleaned_data.get("debut")
        salle = cleaned_data.get("salle")
        if debut and not self.instance.pk and debut <= timezone.now():
            self.add_error("debut", "La séance doit commencer dans le futur.")
        if film and debut:
            self.instance.fin = debut + timedelta(minutes=film.duree)
        # Django Admin enveloppe la validation et la sauvegarde dans une transaction.
        # Le verrou sérialise les créations dans une même salle avant full_clean().
        if salle and connection.in_atomic_block:
            Salle.objects.select_for_update().get(pk=salle.pk)
        return cleaned_data
