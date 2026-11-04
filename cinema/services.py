"""Services métier de programmation, de réservation et de disponibilité.

Les écritures directes via update/bulk_create contournent ces contrôles.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .models import Billet, Paiement, Reservation, ReservationPlace, Salle, Seance


@transaction.atomic
def enregistrer_seance(seance):
    """Valide une séance ; la contrainte PostgreSQL protège les créneaux concurrents."""
    Salle.objects.select_for_update().get(pk=seance.salle_id)
    if seance.pk:
        Seance.objects.select_for_update().get(pk=seance.pk)
    seance.full_clean()
    seance.save()
    return seance


@transaction.atomic
def enregistrer_salle(salle):
    if salle.pk:
        Salle.objects.select_for_update().get(pk=salle.pk)
    salle.full_clean()
    salle.save()
    return salle


@transaction.atomic
def enregistrer_place(place):
    Salle.objects.select_for_update().get(pk=place.salle_id)
    place.full_clean()
    place.save()
    return place


@transaction.atomic
def annuler_seance(seance):
    """Annule sans supprimer l'historique ni simuler un remboursement Stripe."""
    Salle.objects.select_for_update().get(pk=seance.salle_id)
    actuelle = Seance.objects.select_for_update().get(pk=seance.pk)
    if actuelle.statut != Seance.Statut.PROGRAMMEE or actuelle.debut <= timezone.now():
        raise ValidationError("Seules les séances programmées à venir peuvent être annulées.")

    reservations = actuelle.reservations.all()
    # Verrouiller les réservations avant leurs changements de statut.
    list(reservations.select_for_update().values_list("pk", flat=True))
    paiements_a_traiter = Paiement.objects.filter(
        reservation__seance=actuelle,
        statut__in=(Paiement.Statut.REUSSI, Paiement.Statut.EN_ATTENTE),
    ).count()
    reservations.filter(
        statut__in=(Reservation.Statut.EN_ATTENTE, Reservation.Statut.CONFIRMEE)
    ).update(statut=Reservation.Statut.ANNULEE)
    Billet.objects.filter(
        reservation_place__reservation__seance=actuelle,
        statut=Billet.Statut.VALIDE,
    ).update(statut=Billet.Statut.ANNULE)
    actuelle.statut = Seance.Statut.ANNULEE
    actuelle.save(update_fields=["statut"])
    return paiements_a_traiter


def obtenir_disponibilites(seance, maintenant=None):
    """Lit l'état des places, sans changer le statut des réservations expirées."""
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
    places = seance.salle.places.annotate(occupee=Exists(occupations)).order_by("rangee", "numero")
    rangees = {}
    total = 0
    disponibles = 0
    for place in places:
        if not place.active:
            statut, libelle = "inactive", "Hors service"
        else:
            total += 1
            if place.occupee:
                statut, libelle = "occupee", "Indisponible"
            else:
                statut, libelle = "disponible", "Disponible"
                disponibles += 1
        rangees.setdefault(place.rangee, []).append({
            "numero": place.numero, "statut": statut, "libelle": libelle,
        })
    return {
        "rangees": [{"nom": nom, "places": places} for nom, places in rangees.items()],
        "total": total,
        "disponibles": disponibles,
        "complet": total > 0 and disponibles == 0,
    }
