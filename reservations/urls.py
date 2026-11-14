from django.urls import path

from . import views

app_name = "reservations"

urlpatterns = [
    path("billets/<uuid:reference>/qr.svg", views.qr_billet, name="qr_billet"),
    path("billets/<uuid:reference>/controle/", views.controler_billet, name="controler_billet"),
    path("billets/<str:jeton>/", views.consulter_billets, name="consulter_billets"),
    path("billets/<str:jeton>/pdf/", views.telecharger_billets, name="telecharger_billets"),
    path("seances/<int:pk>/reserver/", views.reserver_places, name="reserver_places"),
    path("<int:pk>/", views.detail_reservation, name="detail_reservation"),
    path("<int:pk>/email/", views.renvoyer_email_billets, name="renvoyer_email_billets"),
    path("seances/<int:pk>/selection/", views.valider_selection, name="valider_selection"),
]
