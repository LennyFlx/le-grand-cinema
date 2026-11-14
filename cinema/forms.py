from datetime import timedelta

from django import forms
from django.db import connection
from django.db.models import Q
from django.utils import timezone

from .models import Film, Salle, Seance


class SeanceAdminForm(forms.ModelForm):
    prix = forms.DecimalField(max_digits=8, decimal_places=2, min_value=0)

    class Meta:
        model = Seance
        fields = ("film", "salle", "debut", "prix")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["film"].queryset = Film.objects.filter(Q(actif=True) | Q(pk=self.instance.film_id))
        self.fields["salle"].queryset = Salle.objects.filter(Q(active=True) | Q(pk=self.instance.salle_id))

    def clean(self):
        cleaned_data = super().clean()
        film = cleaned_data.get("film")
        debut = cleaned_data.get("debut")
        salle = cleaned_data.get("salle")
        if debut and debut <= timezone.now():
            self.add_error("debut", "La séance doit commencer dans le futur.")
        # Garder les verrous jusqu'à la sauvegarde dans la transaction de l'admin.
        if connection.in_atomic_block:
            salles = {salle.pk} if salle else set()
            if self.instance.pk:
                salles.add(self.instance.salle_id)
            list(Salle.objects.select_for_update().filter(pk__in=salles).order_by("pk"))
            if self.instance.pk:
                Seance.objects.select_for_update().get(pk=self.instance.pk)
        if self.instance.pk:
            initiale = Seance.objects.get(pk=self.instance.pk)
            if initiale.debut <= timezone.now() or initiale.statut != Seance.Statut.PROGRAMMEE:
                raise forms.ValidationError(
                    "Seules les séances programmées à venir peuvent être modifiées."
                )
        if film and cleaned_data.get("debut"):
            self.instance.fin = debut + timedelta(minutes=film.duree)
        return cleaned_data
