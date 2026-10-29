"""Points d'entrée pour les écritures nécessitant une validation métier.

Les écritures directes via update/bulk_create contournent ces contrôles.
"""

from django.db import transaction

from .models import Salle


@transaction.atomic
def enregistrer_seance(seance):
    """Valide une séance ; la contrainte PostgreSQL protège les créneaux concurrents."""
    Salle.objects.select_for_update().get(pk=seance.salle_id)
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
