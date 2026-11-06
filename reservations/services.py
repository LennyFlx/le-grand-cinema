"""Disponibilités des places et gestion des réservations et billets associés."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from cinema.models import Salle, Seance
from paiements.models import Paiement

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


@transaction.atomic
def generer_billets(reservation):
    """Émet un billet par place payée, sans dupliquer ni réactiver les billets."""
    # Le verrou sérialise l'émission avec les autres émissions et l'annulation.
    actuelle = (
        Reservation.objects.select_for_update(of=("self",))
        .select_related("seance").get(pk=reservation.pk)
    )
    if (
        actuelle.statut != Reservation.Statut.CONFIRMEE
        or actuelle.seance.statut == Seance.Statut.ANNULEE
        or not actuelle.paiements.filter(statut=Paiement.Statut.REUSSI).exists()
    ):
        raise ValidationError(
            "Les billets nécessitent une réservation confirmée et un paiement réussi."
        )

    billets = []
    for association in actuelle.places.order_by("pk"):
        billet, _ = Billet.objects.get_or_create(reservation_place=association)
        billets.append(billet)
    return billets


def reservations_provisoires_actives(maintenant=None):
    """Inclut les paiements en attente tant que Stripe ne les a pas clôturés."""
    maintenant = maintenant if maintenant is not None else timezone.now()
    return Reservation.objects.filter(statut=Reservation.Statut.EN_ATTENTE).filter(
        Q(date_expiration__gt=maintenant) | Q(paiements__statut=Paiement.Statut.EN_ATTENTE),
    ).distinct()


def reservations_a_expirer(maintenant=None):
    """Sélectionne les réservations échues sans transaction encore en attente."""
    maintenant = maintenant if maintenant is not None else timezone.now()
    return Reservation.objects.filter(
        statut=Reservation.Statut.EN_ATTENTE, date_expiration__lte=maintenant,
    ).exclude(paiements__statut=Paiement.Statut.EN_ATTENTE)


def _places_avec_occupation(seance, maintenant=None):
    maintenant = maintenant if maintenant is not None else timezone.now()
    occupations = ReservationPlace.objects.filter(
        place_id=OuterRef("pk"), reservation__seance=seance,
    ).filter(
        Q(reservation__statut=Reservation.Statut.CONFIRMEE)
        | Q(reservation_id__in=reservations_provisoires_actives(maintenant))
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

DUREE_RESERVATION_PROVISOIRE = timedelta(minutes=30)


def expirer_reservations():
    """Actualise les statuts sans libérer une transaction encore en attente."""
    return reservations_a_expirer().update(statut=Reservation.Statut.EXPIREE)


@transaction.atomic
def creer_reservation_provisoire(seance, identifiants_places, email):
    """Revérifie et bloque les places en une transaction, sans paiement ni billet."""
    identifiants = list(identifiants_places)
    if not 1 <= len(identifiants) <= 8:
        raise ValidationError("Sélectionnez entre 1 et 8 places par réservation.")
    if any(type(pk) is not int or not 0 < pk < 2**63 for pk in identifiants):
        raise ValidationError("La sélection contient un identifiant de place invalide.")
    if len(set(identifiants)) != len(identifiants):
        raise ValidationError("Une place ne peut être sélectionnée qu’une seule fois.")

    # Même ordre de verrouillage que la modification et l'annulation d'une séance.
    salle_id = Seance.objects.filter(pk=seance.pk).values_list("salle_id", flat=True).first()
    if salle_id is None:
        raise ValidationError("Cette séance n’est plus disponible.")
    Salle.objects.select_for_update().get(pk=salle_id)
    actuelle = (Seance.objects.select_for_update(of=("self",))
                .select_related("film", "salle").get(pk=seance.pk))
    # L'heure est prise après l'attente éventuelle des verrous.
    maintenant = timezone.now()
    if (actuelle.salle_id != salle_id or actuelle.statut != Seance.Statut.PROGRAMMEE
            or actuelle.debut <= maintenant or not actuelle.film.actif or not actuelle.salle.active):
        raise ValidationError("Cette séance n’est plus disponible.")
    places = list(_places_avec_occupation(actuelle, maintenant)
                  .filter(pk__in=identifiants).order_by("rangee", "numero"))
    if len(places) != len(identifiants):
        raise ValidationError("Une des places sélectionnées ne fait pas partie de cette salle.")
    indisponibles = [f"{place.rangee}{place.numero}" for place in places if not place.active or place.occupee]
    if indisponibles:
        raise ValidationError("Ces places ne sont plus disponibles : %(places)s.",
                              params={"places": ", ".join(indisponibles)})
    reservation = Reservation(
        seance=actuelle, email=(email or "").strip(), statut=Reservation.Statut.EN_ATTENTE,
        date_expiration=min(maintenant + DUREE_RESERVATION_PROVISOIRE, actuelle.debut),
        montant_total=actuelle.prix * len(places),
    )
    reservation.full_clean()
    reservations_a_expirer(maintenant).filter(seance=actuelle).update(statut=Reservation.Statut.EXPIREE)
    reservation.save()
    ReservationPlace.objects.bulk_create([
        ReservationPlace(reservation=reservation, place=place, prix_unitaire=actuelle.prix)
        for place in places
    ])
    return reservation
