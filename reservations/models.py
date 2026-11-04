import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from cinema.models import Place

class Reservation(models.Model):
    class Statut(models.TextChoices):
        EN_ATTENTE = "en_attente", "En attente"
        CONFIRMEE = "confirmee", "Confirmée"
        EXPIREE = "expiree", "Expirée"
        ANNULEE = "annulee", "Annulée"

    seance = models.ForeignKey(
        "cinema.Seance",
        on_delete=models.PROTECT,
        related_name="reservations"
    )
    email = models.EmailField()
    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.EN_ATTENTE)

    date_creation = models.DateTimeField(auto_now_add=True)
    date_expiration = models.DateTimeField()
    montant_total = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        db_table = "cinema_reservation"
        ordering = ["-date_creation"]
        constraints = [
            models.CheckConstraint(condition=Q(montant_total__gte=0),
            name="reservation_total_non_negatif")
        ]


class ReservationPlace(models.Model):
    reservation = models.ForeignKey(
        Reservation,
        on_delete=models.PROTECT,
        related_name="places"
    )
    place = models.ForeignKey(
        "cinema.Place",
        on_delete=models.PROTECT,
        related_name="reservations"
    )
    prix_unitaire = models.DecimalField(max_digits=8, decimal_places=2)

    def clean(self):
        super().clean()
        reservation = Reservation.objects.select_related("seance").filter(
            pk=self.reservation_id
        ).first()
        place = Place.objects.filter(pk=self.place_id).first()
        if reservation and place:
            if place.salle_id != reservation.seance.salle_id:
                raise ValidationError({
                    "place": "La place doit appartenir à la salle de la séance."
                })
            if not place.active:
                association_initiale = ReservationPlace.objects.filter(
                    pk=self.pk).values("place_id", "reservation_id",).first()
                if association_initiale != {
                    "place_id": self.place_id,
                    "reservation_id": self.reservation_id,
                }:
                    raise ValidationError(
                        {"place": "Cette place est inactive."})

    class Meta:
        db_table = "cinema_reservationplace"
        constraints = [
            models.UniqueConstraint(
                fields=["reservation", "place"],
                name="reservation_place_unique"
            ),
            models.CheckConstraint(
                condition=Q(prix_unitaire__gte=0),
                name="reservation_place_prix_non_negatif"
            ),
        ]


class Billet(models.Model):
    class Statut(models.TextChoices):
        VALIDE = "valide", "Valide"
        ANNULE = "annule", "Annulé"

    reservation_place = models.OneToOneField(
        ReservationPlace,
        on_delete=models.PROTECT,
        related_name="billet"
    )
    reference = models.CharField(
        max_length=100, unique=True,
        default=uuid.uuid4, editable=False
    )
    date_emission = models.DateTimeField(auto_now_add=True)
    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.VALIDE
    )

    def clean(self):
        super().clean()
        association = (
            ReservationPlace.objects.select_related("reservation").filter(
                pk=self.reservation_place_id
            ).first()
        )
        if self.statut == self.Statut.VALIDE and association and association.reservation.statut != Reservation.Statut.CONFIRMEE:
            raise ValidationError({
                "reservation_place": "La réservation doit être confirmée pour émettre un billet valide."
            })

    class Meta:
        db_table = "cinema_billet"
