from django.urls import path

from . import views

app_name = "reservations"

urlpatterns = [
    path("seances/<int:pk>/reserver/", views.reserver_places, name="reserver_places"),
    path("<int:pk>/", views.detail_reservation, name="detail_reservation"),
    path("seances/<int:pk>/selection/", views.valider_selection, name="valider_selection"),
]
