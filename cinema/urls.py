from django.urls import path

from . import views

app_name = "cinema"

urlpatterns = [
    path("", views.films_a_l_affiche, name="films"),
    path("films/<int:pk>/", views.detail_film, name="detail_film"),
]
