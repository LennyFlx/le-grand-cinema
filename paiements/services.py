"""Consultation des transactions nécessitant un suivi après annulation."""

from .models import Paiement


def compter_paiements_a_traiter(seance):
    """Compte les transactions à vérifier manuellement, sans modifier leur statut."""
    return Paiement.objects.filter(
        reservation__seance=seance,
        statut__in=(Paiement.Statut.REUSSI, Paiement.Statut.EN_ATTENTE),
    ).count()
