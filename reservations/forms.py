from django import forms

from .services import _places_avec_occupation


class SelectionPlacesForm(forms.Form):
    places = forms.TypedMultipleChoiceField(
        coerce=int, widget=forms.MultipleHiddenInput,
        error_messages={"required": "Sélectionnez au moins une place.",
                        "invalid_choice": "Une des places sélectionnées ne fait pas partie de cette salle."},
    )

    def __init__(self, *args, seance, maintenant, **kwargs):
        super().__init__(*args, **kwargs)
        self.seance = seance
        self.maintenant = maintenant
        self.fields["places"].choices = [(place.pk, str(place)) for place in
                                        seance.salle.places.select_related("salle").all()]

    def clean_places(self):
        identifiants = self.cleaned_data["places"]
        if len(identifiants) > 8:
            raise forms.ValidationError("Vous pouvez sélectionner jusqu’à 8 places par réservation.")
        if len(set(identifiants)) != len(identifiants):
            raise forms.ValidationError("Une place ne peut être sélectionnée qu’une seule fois.")
        places = list(_places_avec_occupation(self.seance, self.maintenant)
                      .filter(pk__in=identifiants).order_by("rangee", "numero"))
        indisponibles = [f"{place.rangee}{place.numero}" for place in places if not place.active or place.occupee]
        if indisponibles:
            raise forms.ValidationError("Ces places ne sont plus disponibles : %(places)s. Modifiez votre sélection.",
                                        params={"places": ", ".join(indisponibles)})
        return places


class ReservationProvisoireForm(SelectionPlacesForm):
    email = forms.EmailField(
        label="Adresse email", max_length=254,
        widget=forms.EmailInput(attrs={
            "autocomplete": "email",
            "class": "mt-2 min-h-11 w-full rounded-md border border-zinc-300 px-3 py-2",
        }),
    )
