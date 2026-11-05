from django.utils import timezone

from .models import Reservation


def reservation_en_cours(request):
    identifiant = request.session.get("reservation_provisoire_id")
    if identifiant is None:
        return {}
    maintenant = timezone.now()
    reservation = Reservation.objects.filter(
        pk=identifiant, statut=Reservation.Statut.EN_ATTENTE,
        date_expiration__gt=maintenant,
        seance__statut="programmee", seance__debut__gt=maintenant,
    ).only("pk").first()
    return {"reservation_a_reprendre": reservation}
