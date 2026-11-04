"""Services de programmation et coordination de l'annulation des séances."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from paiements.services import compter_paiements_a_traiter
from reservations.services import annuler_reservations_seance

from .models import Salle, Seance


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
    """Coordonne l'annulation sans supprimer l'historique ni rembourser automatiquement."""
    Salle.objects.select_for_update().get(pk=seance.salle_id)
    actuelle = Seance.objects.select_for_update().get(pk=seance.pk)
    if actuelle.statut != Seance.Statut.PROGRAMMEE or actuelle.debut <= timezone.now():
        raise ValidationError("Seules les séances programmées à venir peuvent être annulées.")
    annuler_reservations_seance(actuelle)
    paiements_a_traiter = compter_paiements_a_traiter(actuelle)
    actuelle.statut = Seance.Statut.ANNULEE
    actuelle.save(update_fields=["statut"])
    return paiements_a_traiter
