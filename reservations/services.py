"""Disponibilités des places et gestion des réservations et billets associés."""

from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .models import Billet, Reservation, ReservationPlace


@transaction.atomic
def annuler_reservations_seance(seance):
    """Annule les réservations actives et invalide les billets en conservant l'historique."""
    reservations = Reservation.objects.filter(seance=seance)
    list(reservations.select_for_update().values_list("pk", flat=True))
    reservations.filter(
        statut__in=(Reservation.Statut.EN_ATTENTE, Reservation.Statut.CONFIRMEE),
    ).update(statut=Reservation.Statut.ANNULEE)
    Billet.objects.filter(
        reservation_place__reservation__seance=seance,
        statut=Billet.Statut.VALIDE,
    ).update(statut=Billet.Statut.ANNULE)


def _places_avec_occupation(seance, maintenant=None):
    maintenant = maintenant if maintenant is not None else timezone.now()
    occupations = ReservationPlace.objects.filter(
        place_id=OuterRef("pk"), reservation__seance=seance,
    ).filter(
        Q(reservation__statut=Reservation.Statut.CONFIRMEE)
        | Q(
            reservation__statut=Reservation.Statut.EN_ATTENTE,
            reservation__date_expiration__gt=maintenant,
        )
    )
    return seance.salle.places.annotate(occupee=Exists(occupations))


def compter_places_disponibles(seance, maintenant=None):
    """Compte les places actives et libres sans charger le plan de salle."""
    return _places_avec_occupation(seance, maintenant).filter(active=True, occupee=False).count()


def obtenir_disponibilites(seance, maintenant=None):
    """Lit l'état des places, sans changer le statut des réservations expirées."""
    places = _places_avec_occupation(seance, maintenant).order_by("rangee", "numero")
    rangees = {}
    total = 0
    disponibles = 0
    for place in places:
        if not place.active:
            statut, libelle = "inactive", "Hors service"
        else:
            total += 1
            if place.occupee:
                statut, libelle = "occupee", "Réservée"
            else:
                statut, libelle = "disponible", "Disponible"
                disponibles += 1
        rangees.setdefault(place.rangee, []).append({
            "id": place.pk, "numero": place.numero, "statut": statut, "libelle": libelle,
        })
    return {
        "rangees": [{"nom": nom, "places": places} for nom, places in rangees.items()],
        "total": total,
        "disponibles": disponibles,
        "complet": total > 0 and disponibles == 0,
    }
