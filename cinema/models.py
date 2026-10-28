
import uuid

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


class Film(models.Model):
    titre = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    duree = models.PositiveIntegerField(help_text="Durée en minutes")
    date_sortie = models.DateField(null=True, blank=True)
    affiche = models.CharField(max_length=255, blank=True)
    actif = models.BooleanField(default=True)

    def __str__(self):
        return self.titre

    class Meta:
        ordering = ["titre"]
        verbose_name = "Film"
        verbose_name_plural = "Films"
        constraints = [
            models.CheckConstraint(
                condition=Q(duree__gt=0),
                name="film_duree_positive",
            ),
        ]


class Salle(models.Model):
    nom = models.CharField(max_length=100, unique=True)
    capacite = models.PositiveIntegerField()
    active = models.BooleanField(default=True)

    def clean(self):
        super().clean()
        if self.pk and self.capacite is not None and self.places.count() > self.capacite:
            raise ValidationError({"capacite": "La capacité est inférieure au nombre de places physiques."})

    def __str__(self):
        return self.nom

    class Meta:
        ordering = ["nom"]
        verbose_name = "Salle"
        verbose_name_plural = "Salles"
        constraints = [
            models.CheckConstraint(
                condition=Q(capacite__gt=0),
                name="salle_capacite_positive",
            ),
        ]


class Place(models.Model):
    salle = models.ForeignKey(
        Salle,
        on_delete=models.PROTECT,
        related_name="places",
    )
    rangee = models.CharField(max_length=5)
    numero = models.PositiveIntegerField()
    active = models.BooleanField(default=True)

    def clean(self):
        super().clean()
        salle = Salle.objects.filter(pk=self.salle_id).first()
        if salle and salle.places.exclude(pk=self.pk).count() >= salle.capacite:
            raise ValidationError({"salle": "La capacité de cette salle est atteinte."})

    def __str__(self):
        return f"{self.salle.nom} - {self.rangee}{self.numero}"

    class Meta:
        ordering = ["salle", "rangee", "numero"]
        verbose_name = "Place"
        verbose_name_plural = "Places"
        constraints = [
            models.UniqueConstraint(
                fields=["salle", "rangee", "numero"],
                name="place_unique_par_salle",
            ),
            models.CheckConstraint(
                condition=Q(numero__gt=0),
                name="place_numero_positif",
            ),
        ]


class Seance(models.Model):
    class Statut(models.TextChoices):
        PROGRAMMEE = "programmee", "Programmée"
        ANNULEE = "annulee", "Annulée"
        TERMINEE = "terminee", "Terminée"

    film = models.ForeignKey(
        Film,
        on_delete=models.PROTECT,
        related_name="seances",
    )
    salle = models.ForeignKey(
        Salle,
        on_delete=models.PROTECT,
        related_name="seances",
    )
    debut = models.DateTimeField()
    fin = models.DateTimeField()
    prix = models.DecimalField(max_digits=8, decimal_places=2)
    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.PROGRAMMEE,
    )

    def clean(self):
        super().clean()

        if self.debut and self.fin and self.fin <= self.debut:
            raise ValidationError({
                "fin": "La fin doit être postérieure au début."
            })

        salle = Salle.objects.filter(pk=self.salle_id).first()
        if self.statut == self.Statut.PROGRAMMEE and salle and not salle.active:
            raise ValidationError({
                "salle": "Cette salle est inactive."
            })

        if self.salle_id and self.debut and self.fin:
            chevauchements = Seance.objects.filter(
                salle_id=self.salle_id,
                debut__lt=self.fin,
                fin__gt=self.debut,
            ).exclude(statut=self.Statut.ANNULEE).exclude(pk=self.pk)

            if self.statut != self.Statut.ANNULEE and chevauchements.exists():
                raise ValidationError(
                    "Une autre séance est déjà programmée dans cette salle "
                    "sur ce créneau."
                )

    def __str__(self):
        return f"{self.film.titre} - {self.debut:%d/%m/%Y %H:%M}"

    class Meta:
        ordering = ["debut"]
        verbose_name = "Séance"
        verbose_name_plural = "Séances"
        constraints = [
            ExclusionConstraint(
                name="seance_sans_chevauchement",
                expressions=[
                    ("salle", RangeOperators.EQUAL),
                    (models.Func("debut", "fin", models.Value("[)"),
                                 function="TSTZRANGE", output_field=DateTimeRangeField()),
                     RangeOperators.OVERLAPS),
                ],
                condition=~Q(statut="annulee"),
            ),
            models.CheckConstraint(
                condition=Q(fin__gt=F("debut")),
                name="seance_fin_apres_debut",
            ),
            models.CheckConstraint(
                condition=Q(prix__gte=0),
                name="seance_prix_non_negatif",
            ),
        ]


class Reservation(models.Model):
    class Statut(models.TextChoices):
        EN_ATTENTE = "en_attente", "En attente"
        CONFIRMEE = "confirmee", "Confirmée"
        EXPIREE = "expiree", "Expirée"
        ANNULEE = "annulee", "Annulée"

    seance = models.ForeignKey(Seance, on_delete=models.PROTECT, related_name="reservations")
    email = models.EmailField()
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.EN_ATTENTE)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_expiration = models.DateTimeField()
    montant_total = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        ordering = ["-date_creation"]
        constraints = [models.CheckConstraint(condition=Q(montant_total__gte=0), name="reservation_total_non_negatif")]


class ReservationPlace(models.Model):
    reservation = models.ForeignKey(Reservation, on_delete=models.PROTECT, related_name="places")
    place = models.ForeignKey(Place, on_delete=models.PROTECT, related_name="reservations")
    prix_unitaire = models.DecimalField(max_digits=8, decimal_places=2)

    def clean(self):
        super().clean()
        reservation = Reservation.objects.select_related("seance").filter(pk=self.reservation_id).first()
        place = Place.objects.filter(pk=self.place_id).first()
        if reservation and place:
            if place.salle_id != reservation.seance.salle_id:
                raise ValidationError({"place": "La place doit appartenir à la salle de la séance."})
            if not place.active and self._state.adding:
                raise ValidationError({"place": "Cette place est inactive."})

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["reservation", "place"], name="reservation_place_unique"),
            models.CheckConstraint(condition=Q(prix_unitaire__gte=0), name="reservation_place_prix_non_negatif"),
        ]


class Paiement(models.Model):
    class Statut(models.TextChoices):
        EN_ATTENTE = "en_attente", "En attente"
        REUSSI = "reussi", "Réussi"
        ECHOUE = "echoue", "Échoué"
        REMBOURSE = "rembourse", "Remboursé"

    reservation = models.ForeignKey(Reservation, on_delete=models.PROTECT, related_name="paiements")
    stripe_session_id = models.CharField(max_length=255, unique=True)
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True)
    montant = models.DecimalField(max_digits=8, decimal_places=2)
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.EN_ATTENTE)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_confirmation = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(montant__gte=0), name="paiement_montant_non_negatif")]


class Billet(models.Model):
    class Statut(models.TextChoices):
        VALIDE = "valide", "Valide"
        ANNULE = "annule", "Annulé"

    reservation_place = models.OneToOneField(ReservationPlace, on_delete=models.PROTECT, related_name="billet")
    reference = models.CharField(max_length=100, unique=True, default=uuid.uuid4, editable=False)
    date_emission = models.DateTimeField(auto_now_add=True)
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.VALIDE)

    def clean(self):
        super().clean()
        association = ReservationPlace.objects.select_related("reservation").filter(pk=self.reservation_place_id).first()
        if self.statut == self.Statut.VALIDE and association and association.reservation.statut != Reservation.Statut.CONFIRMEE:
            raise ValidationError({"reservation_place": "La réservation doit être confirmée pour émettre un billet valide."})
