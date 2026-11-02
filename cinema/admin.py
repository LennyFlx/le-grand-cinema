from django.contrib import admin
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.template.response import TemplateResponse

from .models import Film


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
