from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.template.response import TemplateResponse

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from .forms import SeanceAdminForm
from .models import Film, Reservation, ReservationPlace, Seance
from .services import annuler_seance, enregistrer_seance


@admin.register(Film)
class FilmAdmin(admin.ModelAdmin):
    list_display = ("titre", "duree", "actif")
    search_fields = ("titre",)
    actions = ("retirer_de_la_programmation",)

    @admin.action(
        description="Retirer de la programmation",
        permissions=["change"],
    )
    def retirer_de_la_programmation(self, request, queryset):
        if request.POST.get("confirmer") == "oui":
            nombre = queryset.filter(actif=True).update(actif=False)
            self.message_user(
                request,
                f"Retrait effectué : {nombre} film(s) désactivé(s). Aucun film supprimé.",
            )
            return None

        contexte = {
            **self.admin_site.each_context(request),
            "title": "Confirmer le retrait de la programmation",
            "opts": self.model._meta,
            "films": queryset,
            "action_checkbox_name": ACTION_CHECKBOX_NAME,
            "select_across": request.POST.get("select_across", "0"),
        }
        return TemplateResponse(
            request,
            "admin/cinema/film/confirmer_retrait.html",
            contexte,
        )


@admin.register(Seance)
class SeanceAdmin(admin.ModelAdmin):
    form = SeanceAdminForm
    list_display = ("film", "salle", "debut", "fin", "prix", "statut", "places_disponibles")
    list_filter = ("salle", "statut", "debut")
    list_select_related = ("film", "salle")
    search_fields = ("film__titre", "salle__nom")
    fields = ("film", "salle", "debut", "prix", "fin", "statut", "places_disponibles")
    readonly_fields = ("fin", "statut", "places_disponibles")
    actions = ("annuler_les_seances",)

    @admin.display(description="Places disponibles")
    def places_disponibles(self, obj):
        if not obj or not obj.pk:
            return "Selon les places actives de la salle sélectionnée."
        occupees = ReservationPlace.objects.filter(
            reservation__seance=obj,
        ).filter(
            Q(reservation__statut=Reservation.Statut.CONFIRMEE)
            | Q(
                reservation__statut=Reservation.Statut.EN_ATTENTE,
                reservation__date_expiration__gt=timezone.now(),
            )
        ).values("place_id")
        return obj.salle.places.filter(active=True).exclude(pk__in=occupees).count()

    def save_model(self, request, obj, form, change):
        enregistrer_seance(obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        autorise = super().has_change_permission(request, obj)
        return autorise and (
            obj is None
            or (obj.statut == Seance.Statut.PROGRAMMEE and obj.debut > timezone.now())
        )

    @admin.action(description="Annuler les séances sélectionnées", permissions=["change"])
    def annuler_les_seances(self, request, queryset):
        if request.POST.get("confirmer") == "oui":
            try:
                with transaction.atomic():
                    nombre = 0
                    paiements = 0
                    for seance in queryset.order_by("salle_id", "pk"):
                        paiements += annuler_seance(seance)
                        self.log_change(request, seance, "Annulation de la séance et de ses réservations actives.")
                        nombre += 1
            except ValidationError as erreur:
                self.message_user(request, " ".join(erreur.messages), level=messages.ERROR)
                return None
            self.message_user(request, f"{nombre} séance(s) annulée(s). Historique conservé.")
            self.message_user(
                request,
                "Informer les spectateurs concernés. "
                f"{paiements} paiement(s) réussi(s) ou en attente à vérifier manuellement "
                "auprès de Stripe (remboursement ou clôture de session). Aucun remboursement automatique effectué.",
                level=messages.WARNING,
            )
            return None

        contexte = {
            **self.admin_site.each_context(request),
            "title": "Confirmer l'annulation des séances",
            "opts": self.model._meta,
            "seances": queryset.annotate(nombre_reservations=Count("reservations")),
            "action_checkbox_name": ACTION_CHECKBOX_NAME,
            "select_across": request.POST.get("select_across", "0"),
        }
        return TemplateResponse(request, "admin/cinema/seance/confirmer_annulation.html", contexte)
