"""Statistiques de remplissage, en lecture seule, à partir des places confirmées."""

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db.models import (
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    IntegerField,
    OuterRef,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Cast, Coalesce, Round

from reservations.models import Reservation, ReservationPlace

from .models import Seance


def obtenir_statistiques_remplissage(*, film=None, salle=None, date_debut=None, date_fin=None):
    """Retourne les séances filtrées et leur synthèse, sans modifier les réservations.

    Les dates portent sur le début de séance dans le fuseau Django courant.
    La capacité déclarée de la salle est comptée une fois par séance.
    """
    if date_debut and date_fin and date_debut > date_fin:
        raise ValidationError("La date de fin doit être postérieure ou égale à la date de début.")

    seances = Seance.objects.select_related("film", "salle").order_by("debut", "pk")
    if film is not None:
        seances = seances.filter(film=film)
    if salle is not None:
        seances = seances.filter(salle=salle)
    if date_debut:
        seances = seances.filter(debut__date__gte=date_debut)
    if date_fin:
        seances = seances.filter(debut__date__lte=date_fin)

    places_confirmees = (
        ReservationPlace.objects.filter(
            reservation__seance_id=OuterRef("pk"),
            reservation__statut=Reservation.Statut.CONFIRMEE,
        )
        .order_by()
        .values("reservation__seance_id")
        .annotate(nombre=Count("place_id", distinct=True))
        .values("nombre")
    )
    # La sous-requête évite de multiplier les séances et leurs capacités par
    # les réservations, places ou tentatives de paiement associées.
    seances = seances.annotate(
        places_reservees=Coalesce(
            Subquery(places_confirmees, output_field=IntegerField()), Value(0),
        ),
        capacite=F("salle__capacite"),
    ).annotate(
        taux_remplissage=Round(
            ExpressionWrapper(
                Cast("places_reservees", DecimalField(max_digits=12, decimal_places=2))
                * Value(Decimal(100)) / F("capacite"),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            precision=2,
        ),
    )

    totaux = seances.aggregate(
        nombre_seances=Count("pk"),
        total_places=Sum("places_reservees", default=0),
        capacite_totale=Sum("capacite", default=0),
    )
    synthese = {
        "nombre_seances": totaux["nombre_seances"],
        "places_reservees": totaux["total_places"],
        "capacite_totale": totaux["capacite_totale"],
    }
    capacite_totale = synthese["capacite_totale"]
    synthese["taux_global"] = (
        (Decimal(synthese["places_reservees"]) * 100 / capacite_totale).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        )
        if capacite_totale else Decimal("0.00")
    )
    return {"seances": seances, "synthese": synthese}
