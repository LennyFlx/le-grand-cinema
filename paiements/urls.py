from django.urls import path

from . import views

app_name = "paiements"

urlpatterns = [
    path("webhook/", views.webhook_stripe, name="webhook_stripe"),
]
