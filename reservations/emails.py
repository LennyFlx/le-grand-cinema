"""Envoi des billets PDF et de leur lien après confirmation, avec suivi des relances."""

import logging
from datetime import timedelta
from functools import partial

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from .models import EnvoiBillets, Reservation
from .pdf import generer_pdf_billets
from .services import creer_url_absolue, creer_url_billets, obtenir_billets_valides

logger = logging.getLogger(__name__)
DELAI_RELANCE_EMAIL = timedelta(minutes=1)


def programmer_envoi_billets(reservation):
    """Conserve la demande dans la transaction du paiement avant de programmer l'envoi."""
    _, cree = EnvoiBillets.objects.get_or_create(reservation=reservation)
    if cree:
        transaction.on_commit(partial(_envoyer_apres_confirmation, reservation.pk), robust=True)


def _envoyer_apres_confirmation(reservation_id):
    try:
        envoyer_billets(reservation_id)
    except Exception as erreur:  # noqa: BLE001 - Isoler l'échec email du paiement
        # Le paiement est acquis ; les détails de connexion et le lien restent hors des logs.
        logger.warning(
            "Email billets non envoyé : reservation=%s type=%s",
            reservation_id, type(erreur).__name__,
        )


@transaction.atomic
def _preparer_envoi(reservation_id, renvoi):
    reservation = Reservation.objects.select_related("seance__film", "seance__salle").get(
        pk=reservation_id,
    )
    if not obtenir_billets_valides(reservation).exists():
        raise ValidationError("Les billets de cette réservation ne sont pas disponibles.")

    envoi, _ = EnvoiBillets.objects.select_for_update().get_or_create(reservation=reservation)
    if envoi.statut == EnvoiBillets.Statut.ENVOYE and not renvoi:
        return None
    maintenant = timezone.now()
    if (envoi.date_derniere_tentative is not None
            and maintenant < envoi.date_derniere_tentative + DELAI_RELANCE_EMAIL):
        raise ValidationError("Patientez une minute entre deux demandes d’email.")

    # Le créneau reste occupé une minute, y compris si le processus est interrompu.
    envoi.statut = EnvoiBillets.Statut.EN_COURS
    envoi.date_derniere_tentative = maintenant
    envoi.save(update_fields=["statut", "date_derniere_tentative"])
    return envoi, reservation, maintenant



def envoyer_billets(reservation_id, *, renvoi=False):
    """Réserve une tentative courte, puis contacte le service mail hors transaction."""
    preparation = _preparer_envoi(reservation_id, renvoi)
    if preparation is None:
        return False
    envoi, reservation, date_tentative = preparation
    tentative = EnvoiBillets.objects.filter(
        pk=envoi.pk, date_derniere_tentative=date_tentative, statut=EnvoiBillets.Statut.EN_COURS,
    )
    try:
        billets = list(obtenir_billets_valides(reservation))
        if not billets:
            raise ValidationError("Les billets ne sont plus disponibles.")
        contexte = {
            "reservation": reservation, "billets": billets,
            "url_billets": creer_url_absolue(creer_url_billets(reservation)),
        }
        message = EmailMultiAlternatives(
            subject="Vos billets — Le Grand Cinéma",
            body=render_to_string("reservations/emails/billets.txt", contexte),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[reservation.email],
        )
        message.attach_alternative(
            render_to_string("reservations/emails/billets.html", contexte), "text/html",
        )
        message.attach(
            f"billets-reservation-{reservation.pk}.pdf",
            generer_pdf_billets(reservation, billets),
            "application/pdf",
        )
        if message.send(fail_silently=False) != 1:
            raise RuntimeError("Le service mail n’a pas accepté le message.")
    except Exception as erreur:  # noqa: BLE001 - Enregistrer tout échec d'envoi
        tentative.update(statut=EnvoiBillets.Statut.ECHOUE)
        logger.warning(
            "Échec email billets : reservation=%s type=%s",
            reservation_id, type(erreur).__name__,
        )
        return False
    tentative.update(statut=EnvoiBillets.Statut.ENVOYE, date_envoi=timezone.now())
    return True
