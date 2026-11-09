"""Consultation des réservations par le personnel, sans modification métier."""

from django.contrib import admin
from django.db.models import Count
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import format_html, format_html_join

from cinema.models import Seance

from .models import Reservation


class SeanceReservationFilter(admin.SimpleListFilter):
    title = "séance"
    parameter_name = "seance"

    def lookups(self, request, model_admin):
        seances = (
            Seance.objects.filter(reservations__isnull=False)
            .select_related("film", "salle").distinct().order_by("debut", "pk")
        )
        return [
            (str(seance.pk), (
                f"N°{seance.pk} — {seance.film.titre} — {seance.salle.nom} — "
                f"{date_format(timezone.localtime(seance.debut), 'DATETIME_FORMAT')}"
            ))
            for seance in seances
        ]

    def queryset(self, request, queryset):
        if self.value() is None:
            return queryset
        try:
            identifiant = int(self.value())
        except (ValueError, TypeError):
            return queryset.none()
        if not 0 < identifiant < 2**63:
            return queryset.none()
        return queryset.filter(seance_id=identifiant)


class DateSeanceFilter(admin.DateFieldListFilter):
    def __init__(self, field, request, params, model, model_admin, field_path):
        super().__init__(field, request, params, model, model_admin, field_path)
        self.title = "date de séance"


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = (
        "id", "film", "salle", "debut_seance", "nombre_places",
        "statut_reservation", "montant_total", "date_creation",
    )
    list_display_links = ("id",)
    list_filter = (
        ("seance__film", admin.RelatedOnlyFieldListFilter),
        SeanceReservationFilter,
        ("seance__debut", DateSeanceFilter),
        "statut",
    )
    date_hierarchy = "seance__debut"
    search_fields = ("=id", "email", "seance__film__titre")
    search_help_text = "Rechercher par numéro de réservation, email ou titre du film."
    ordering = ("-date_creation", "-pk")
    actions = None
    change_list_template = "admin/reservations/reservation/change_list.html"
    readonly_fields = (
        "id", "email", "statut_reservation", "montant_total",
        "date_creation", "date_expiration", "film", "salle",
        "debut_seance", "nombre_places", "places_reservees",
    )
    fieldsets = (
        ("Réservation", {
            "fields": ("id", "email", "statut_reservation", "montant_total"),
        }),
        ("Séance", {"fields": ("film", "salle", "debut_seance")}),
        ("Dates de réservation", {"fields": ("date_creation", "date_expiration")}),
        ("Places réservées", {"fields": ("nombre_places", "places_reservees")}),
    )

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .select_related("seance__film", "seance__salle")
            .annotate(nombre_places_reservees=Count("places", distinct=True))
        )

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("reservations.view_reservation")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(request, {
            **(extra_context or {}), "title": "Réservations",
        })

    def change_view(self, request, object_id, form_url="", extra_context=None):
        return super().change_view(request, object_id, form_url, {
            **(extra_context or {}), "title": f"Consulter la réservation n°{object_id}",
        })

    @admin.display(description="Film", ordering="seance__film__titre")
    def film(self, obj):
        return obj.seance.film.titre

    @admin.display(description="Salle", ordering="seance__salle__nom")
    def salle(self, obj):
        return obj.seance.salle.nom

    @admin.display(description="Date et heure de séance", ordering="seance__debut")
    def debut_seance(self, obj):
        return date_format(timezone.localtime(obj.seance.debut), "DATETIME_FORMAT")

    @admin.display(description="Nombre de places", ordering="nombre_places_reservees")
    def nombre_places(self, obj):
        return obj.nombre_places_reservees

    @admin.display(description="Statut", ordering="statut")
    def statut_reservation(self, obj):
        if obj.statut == Reservation.Statut.EN_ATTENTE:
            return "En attente de paiement"
        return obj.get_statut_display()

    @admin.display(description="Places attribuées")
    def places_reservees(self, obj):
        associations = obj.places.select_related("place").order_by(
            "place__rangee", "place__numero",
        )
        lignes = format_html_join("", (
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{} €</td></tr>"
        ), (
            (f"{association.place.rangee}{association.place.numero}",
             association.place.rangee, association.place.numero,
             f"{association.prix_unitaire:.2f}".replace(".", ","))
            for association in associations
        ))
        if not lignes:
            return "Aucune place associée à cette réservation."
        return format_html(
            '<table><thead><tr><th scope="col">Place</th>'
            '<th scope="col">Rangée</th><th scope="col">Numéro</th>'
            '<th scope="col">Prix unitaire</th></tr></thead>'
            "<tbody>{}</tbody></table>",
            lignes,
        )
