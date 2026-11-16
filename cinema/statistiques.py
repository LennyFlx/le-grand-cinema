"""Statistiques de remplissage, en lecture seule, à partir des places confirmées."""

from datetime import timedelta
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
from django.db.models.functions import Cast, Coalesce, Round, TruncDate

from reservations.models import Reservation, ReservationPlace

from .models import Seance


def _seances_avec_places_confirmees(*, film=None, salle=None, date_debut=None, date_fin=None):
    """Partage les filtres et le décompte par siège entre les statistiques."""
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
    return seances.annotate(
        places_reservees=Coalesce(
            Subquery(places_confirmees, output_field=IntegerField()), Value(0),
        ),
        capacite=F("salle__capacite"),
    )


def obtenir_statistiques_remplissage(*, film=None, salle=None, date_debut=None, date_fin=None):
    """Retourne les séances filtrées et leur synthèse pondérée, en lecture seule."""
    seances = _seances_avec_places_confirmees(
        film=film, salle=salle, date_debut=date_debut, date_fin=date_fin,
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


# Une année bissextile complète reste consultable, avec un point par jour.
MAX_JOURS_AFFLUENCE = 366
LIMITE_CLASSEMENT = 10


def valider_periode_affluence(date_debut, date_fin):
    """Vérifie les bornes avant toute requête ou construction de série."""
    if date_debut is None or date_fin is None:
        raise ValidationError("Renseignez les dates de début et de fin de la période.")
    if date_fin < date_debut:
        raise ValidationError("La date de fin doit être postérieure ou égale à la date de début.")
    if (date_fin - date_debut).days + 1 > MAX_JOURS_AFFLUENCE:
        raise ValidationError(
            f"Chaque période est limitée à {MAX_JOURS_AFFLUENCE} jours, bornes incluses.",
        )


def _analyser_periode(*, date_debut, date_fin, film, salle):
    seances = _seances_avec_places_confirmees(
        film=film, salle=salle, date_debut=date_debut, date_fin=date_fin,
    )
    # Additionner les compteurs par séance : un même fauteuil réservé pour
    # deux séances le même jour représente bien deux entrées.
    par_jour = dict(
        seances.order_by().annotate(jour=TruncDate("debut")).values("jour")
        .annotate(total_places=Sum("places_reservees"))
        .order_by("jour").values_list("jour", "total_places")
    )
    nombre_jours = (date_fin - date_debut).days + 1
    jours = [
        {
            "jour": date_debut + timedelta(days=numero),
            "places_reservees": par_jour.get(date_debut + timedelta(days=numero), 0),
        }
        for numero in range(nombre_jours)
    ]
    classement_jours = sorted(
        (jour for jour in jours if jour["places_reservees"]),
        key=lambda jour: (-jour["places_reservees"], jour["jour"]),
    )[:LIMITE_CLASSEMENT]
    classement_seances = list(
        seances.filter(places_reservees__gt=0)
        .order_by("-places_reservees", "debut", "pk")[:LIMITE_CLASSEMENT]
    )
    total = sum(jour["places_reservees"] for jour in jours)
    return {
        "date_debut": date_debut,
        "date_fin": date_fin,
        "nombre_jours": nombre_jours,
        "total_places": total,
        "moyenne_jour": (Decimal(total) / nombre_jours).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        ),
        "jours": jours,
        "classement_jours": classement_jours,
        "classement_seances": classement_seances,
        "jour_plus_frequente": classement_jours[0] if classement_jours else None,
        "seance_plus_demandee": classement_seances[0] if classement_seances else None,
    }


def obtenir_statistiques_affluence(
    *, date_debut, date_fin, comparaison_debut=None, comparaison_fin=None,
    film=None, salle=None,
):
    """Analyse les entrées confirmées aux dates des séances (deux requêtes par période).

    Les périodes sont indépendantes, éventuellement de durées différentes ou
    chevauchantes. Les jours sans entrée valent zéro ; aucune projection n'est faite.
    """
    valider_periode_affluence(date_debut, date_fin)
    comparer = comparaison_debut is not None or comparaison_fin is not None
    if comparer:
        valider_periode_affluence(comparaison_debut, comparaison_fin)
    principale = _analyser_periode(
        date_debut=date_debut, date_fin=date_fin, film=film, salle=salle,
    )
    reference = None
    variation = None
    if comparer:
        reference = _analyser_periode(
            date_debut=comparaison_debut, date_fin=comparaison_fin, film=film, salle=salle,
        )
        ecart = principale["total_places"] - reference["total_places"]
        base = reference["total_places"]
        variation = {
            "places": ecart,
            "pourcentage": (
                (Decimal(ecart) * 100 / base).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP,
                ) if base else (Decimal("0.00") if ecart == 0 else None)
            ),
        }
    return {"principale": principale, "reference": reference, "variation": variation}
