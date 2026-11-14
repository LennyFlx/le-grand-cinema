from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower, Trim
from django.utils import timezone


class Personne(models.Model):
    nom = models.CharField(max_length=150)
    prenom = models.CharField(max_length=150, blank=True)

    def clean(self):
        super().clean()
        self.nom = (self.nom or "").strip()
        self.prenom = (self.prenom or "").strip()
        if not self.nom:
            raise ValidationError({"nom": "Le nom est obligatoire."})

    def save(self, *args, **kwargs):
        self.nom = (self.nom or "").strip()
        self.prenom = (self.prenom or "").strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.prenom} {self.nom}".strip()

    class Meta:
        ordering = ["nom", "prenom", "pk"]
        verbose_name = "Personne"
        verbose_name_plural = "Personnes"
        constraints = [
            models.CheckConstraint(condition=Q(nom__regex=r"\S"), name="personne_nom_non_vide"),
        ]


class Genre(models.Model):
    nom = models.CharField(max_length=100)

    def clean(self):
        super().clean()
        self.nom = (self.nom or "").strip()
        if not self.nom:
            raise ValidationError({"nom": "Le nom du genre est obligatoire."})

    def save(self, *args, **kwargs):
        self.nom = (self.nom or "").strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nom

    class Meta:
        ordering = ["nom"]
        verbose_name = "Genre"
        verbose_name_plural = "Genres"
        constraints = [
            models.UniqueConstraint(Lower(Trim("nom")), name="genre_nom_unique_normalise"),
            models.CheckConstraint(condition=Q(nom__regex=r"\S"), name="genre_nom_non_vide"),
        ]

class Film(models.Model):
    titre = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    duree = models.PositiveIntegerField(help_text="Durée en minutes")
    date_sortie = models.DateField(null=True, blank=True)
    affiche = models.ImageField(
        upload_to="affiches/",
        blank=True,
        max_length=255,
    )
    genres = models.ManyToManyField(Genre, related_name="films", blank=True)
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


class Participation(models.Model):
    class Fonction(models.TextChoices):
        ACTEUR = "acteur", "Acteur"
        REALISATEUR = "realisateur", "Réalisateur"

    film = models.ForeignKey(Film, on_delete=models.CASCADE, related_name="participations")
    personne = models.ForeignKey(Personne, on_delete=models.PROTECT, related_name="participations")
    fonction = models.CharField(max_length=20, choices=Fonction.choices)

    def __str__(self):
        return f"{self.personne} — {self.get_fonction_display()} — {self.film}"

    class Meta:
        ordering = ["film", "fonction", "personne"]
        verbose_name = "Participation"
        verbose_name_plural = "Participations"
        constraints = [
            models.UniqueConstraint(
                fields=["film", "personne", "fonction"], name="participation_fonction_unique",
            ),
            models.CheckConstraint(
                condition=Q(fonction__in=["acteur", "realisateur"]), name="participation_fonction_valide",
            ),
        ]

class Salle(models.Model):
    nom = models.CharField(max_length=100, unique=True)
    capacite = models.PositiveIntegerField()
    active = models.BooleanField(default=True)

    def clean(self):
        super().clean()
        if self.pk and self.capacite is not None and self.places.count() > self.capacite:
            raise ValidationError({
                "capacite": "La capacité est inférieure au nombre de places physiques."
            })

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
        if self.pk:
            salle_initiale = Place.objects.filter(pk=self.pk).values_list(
                "salle_id", flat=True
            ).first()
            if self.salle_id != salle_initiale and self.reservations.exists():
                raise ValidationError({
                    "salle": "Une place associée à une réservation ne peut pas changer de salle."
                })

        salle = Salle.objects.filter(pk=self.salle_id).first()
        if salle and salle.places.exclude(pk=self.pk).count() >= salle.capacite:
            raise ValidationError(
                {"salle": "La capacité de cette salle est atteinte."})

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

        if self.pk:
            salle_initiale = Seance.objects.filter(pk=self.pk).values_list(
                "salle_id", flat=True).first()
            if self.salle_id != salle_initiale and self.reservations.filter(
                    places__isnull=False,
            ).exists():
                raise ValidationError({
                    "salle": "Une séance avec des places réservées ne peut pas changer de salle."
                })

        if self.pk:
            initiale = Seance.objects.get(pk=self.pk)
            champs_modifies = any(
                getattr(self, champ) != getattr(initiale, champ)
                for champ in ("film_id", "salle_id", "debut", "fin", "prix")
            )
            if champs_modifies:
                if initiale.debut <= timezone.now() or initiale.statut != self.Statut.PROGRAMMEE:
                    raise ValidationError(
                        "Seules les séances programmées à venir peuvent être modifiées."
                    )
                if self.debut and self.debut <= timezone.now():
                    raise ValidationError({"debut": "La séance doit commencer dans le futur."})
                if self.reservations.exists():
                    raise ValidationError(
                        "Cette séance possède des réservations : le film, la salle, "
                        "les horaires et le tarif ne peuvent plus être modifiés. "
                        "Une annulation avec traitement des réservations reste possible."
                    )

        if self.debut and self.fin and self.fin <= self.debut:
            raise ValidationError({
                "__all__": "La fin doit être postérieure au début."
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
                    (models.Func(
                        "debut", "fin", models.Value("[)"),
                                 function="TSTZRANGE",
                                 output_field=DateTimeRangeField()),
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
