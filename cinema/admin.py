from datetime import timedelta

from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone
from django.views.decorators.http import require_safe

from reservations.services import compter_places_disponibles

from .forms import SeanceAdminForm, StatistiquesAffluenceForm, StatistiquesRemplissageForm
from .models import Film, Genre, Participation, Personne, Seance
from .services import annuler_seance, enregistrer_seance
from .statistiques import obtenir_statistiques_affluence, obtenir_statistiques_remplissage


@admin.register(Personne)
class PersonneAdmin(admin.ModelAdmin):
    list_display = ("nom", "prenom")
    search_fields = ("nom", "prenom")
    fields = ("nom", "prenom")


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ("nom",)
    search_fields = ("nom",)


class ParticipationInline(admin.TabularInline):
    model = Participation
    fields = ("personne", "fonction")
    autocomplete_fields = ("personne",)
    extra = 1

@admin.register(Film)
class FilmAdmin(admin.ModelAdmin):
    list_display = ("titre", "duree", "actif")
    search_fields = ("titre",)
    list_filter = ("actif", "genres")
    autocomplete_fields = ("genres",)
    inlines = (ParticipationInline,)
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
    change_list_template = "admin/cinema/seance/change_list.html"
    list_display = ("film", "salle", "debut", "fin", "prix", "statut", "places_disponibles")
    list_filter = ("salle", "statut", "debut")
    list_select_related = ("film", "salle")
    search_fields = ("film__titre", "salle__nom")
    fields = ("film", "salle", "debut", "prix", "fin", "statut", "places_disponibles")
    readonly_fields = ("fin", "statut", "places_disponibles")
    actions = ("annuler_les_seances",)

    def get_urls(self):
        return [
            path(
                "statistiques/affluence/",
                self.admin_site.admin_view(require_safe(self.statistiques_affluence)),
                name="cinema_seance_affluence",
            ),
            path(
                "statistiques/",
                self.admin_site.admin_view(require_safe(self.statistiques_remplissage)),
                name="cinema_seance_statistiques",
            ),
        ] + super().get_urls()

    def has_statistiques_permission(self, request):
        return (
            request.user.has_perm("cinema.view_seance")
            and request.user.has_perm("reservations.view_reservation")
        )

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(request, {
            **(extra_context or {}),
            "peut_consulter_statistiques": self.has_statistiques_permission(request),
        })

    def statistiques_remplissage(self, request):
        if not self.has_statistiques_permission(request):
            raise PermissionDenied
        formulaire = StatistiquesRemplissageForm(request.GET)
        contexte = {
            **self.admin_site.each_context(request),
            "title": "Taux de remplissage des séances",
            "opts": self.model._meta,
            "formulaire": formulaire,
        }
        statut = 200
        if formulaire.is_valid():
            statistiques = obtenir_statistiques_remplissage(**formulaire.cleaned_data)
            pagination = Paginator(statistiques["seances"], 50)
            filtres = request.GET.copy()
            filtres.pop("page", None)
            contexte.update(
                synthese=statistiques["synthese"],
                page_obj=pagination.get_page(request.GET.get("page")),
                filtres_query=filtres.urlencode(),
            )
        else:
            statut = 400
        return TemplateResponse(
            request, "admin/cinema/seance/statistiques.html", contexte, status=statut,
        )

    def statistiques_affluence(self, request):
        if not self.has_statistiques_permission(request):
            raise PermissionDenied
        filtres = request.GET.copy()
        if "date_debut" not in filtres and "date_fin" not in filtres:
            aujourd_hui = timezone.localdate()
            filtres["date_debut"] = (aujourd_hui - timedelta(days=29)).isoformat()
            filtres["date_fin"] = aujourd_hui.isoformat()
        formulaire = StatistiquesAffluenceForm(filtres)
        contexte = {
            **self.admin_site.each_context(request),
            "title": "Périodes de forte affluence",
            "opts": self.model._meta,
            "formulaire": formulaire,
        }
        statut = 200
        if formulaire.is_valid():
            statistiques = obtenir_statistiques_affluence(**formulaire.cleaned_data)
            contexte.update(statistiques)
            contexte["donnees_graphiques"] = {
                "principale": statistiques["principale"]["jours"],
                "reference": statistiques["reference"]["jours"] if statistiques["reference"] else [],
            }
        else:
            statut = 400
        return TemplateResponse(
            request, "admin/cinema/seance/affluence.html", contexte, status=statut,
        )

    @admin.display(description="Places disponibles")
    def places_disponibles(self, obj):
        if not obj or not obj.pk:
            return "Selon les places actives de la salle sélectionnée."
        return compter_places_disponibles(obj)

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
