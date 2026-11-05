from django.urls import path

from . import views

app_name = "paiements"

urlpatterns = [
    path("webhook/", views.webhook_stripe, name="webhook_stripe"),
    path("reservations/<int:pk>/payer/", views.payer_reservation, name="payer_reservation"),
]
