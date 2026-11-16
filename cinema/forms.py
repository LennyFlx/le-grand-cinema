from datetime import timedelta

from django import forms
from django.db import connection
from django.db.models import Q
from django.utils import timezone

from .models import Film, Salle, Seance
from .statistiques import valider_periode_affluence


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


class StatistiquesRemplissageForm(forms.Form):
    film = forms.ModelChoiceField(
        label="Film", queryset=Film.objects.order_by("titre", "pk"),
        required=False, empty_label="Tous les films",
    )
    salle = forms.ModelChoiceField(
        label="Salle", queryset=Salle.objects.order_by("nom", "pk"),
        required=False, empty_label="Toutes les salles",
    )
    date_debut = forms.DateField(
        label="Date de début", required=False, input_formats=("%Y-%m-%d",),
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    date_fin = forms.DateField(
        label="Date de fin", required=False, input_formats=("%Y-%m-%d",),
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )

    def clean(self):
        donnees = super().clean()
        debut = donnees.get("date_debut")
        fin = donnees.get("date_fin")
        if debut and fin and fin < debut:
            self.add_error(
                "date_fin", "La date de fin doit être postérieure ou égale à la date de début.",
            )
        return donnees


class StatistiquesAffluenceForm(StatistiquesRemplissageForm):
    comparaison_debut = forms.DateField(
        label="Début de la période de référence", required=False, input_formats=("%Y-%m-%d",),
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    comparaison_fin = forms.DateField(
        label="Fin de la période de référence", required=False, input_formats=("%Y-%m-%d",),
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date_debut"].required = True
        self.fields["date_fin"].required = True

    def clean(self):
        donnees = super().clean()
        for champ_debut, champ_fin in (
            ("date_debut", "date_fin"), ("comparaison_debut", "comparaison_fin"),
        ):
            debut = donnees.get(champ_debut)
            fin = donnees.get(champ_fin)
            if champ_debut in self.errors or champ_fin in self.errors:
                continue
            if champ_debut == "comparaison_debut" and bool(debut) != bool(fin):
                self.add_error(
                    champ_fin if debut else champ_debut,
                    "Renseignez les deux dates de la période de référence.",
                )
            elif debut and fin:
                try:
                    valider_periode_affluence(debut, fin)
                except forms.ValidationError as erreur:
                    self.add_error(champ_fin, erreur)
        return donnees
