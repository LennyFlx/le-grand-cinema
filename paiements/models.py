from django.db import models
from django.db.models import Q


class Paiement(models.Model):
    class Statut(models.TextChoices):
        EN_ATTENTE = "en_attente", "En attente"
        REUSSI = "reussi", "Réussi"
        ECHOUE = "echoue", "Échoué"
        REMBOURSE = "rembourse", "Remboursé"

    reservation = models.ForeignKey(
        "reservations.Reservation",
        on_delete=models.PROTECT,
        related_name="paiements"
    )
    stripe_session_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True)
    montant = models.DecimalField(max_digits=8, decimal_places=2)
    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.EN_ATTENTE
    )
    date_creation = models.DateTimeField(auto_now_add=True)
    date_confirmation = models.DateTimeField(null=True, blank=True)
    date_expiration = models.DateTimeField(null=True, blank=True)
    url_retour = models.URLField(max_length=500, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=Q(montant__gte=0),
            name="paiement_montant_non_negatif")
        ]
