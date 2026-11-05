from django.urls import path

from . import views

app_name = "reservations"

urlpatterns = [
    path("seances/<int:pk>/selection/", views.valider_selection, name="valider_selection"),
]
