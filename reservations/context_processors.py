from django.utils import timezone

from .services import reservations_provisoires_actives


def reservation_en_cours(request):
    identifiant = request.session.get("reservation_provisoire_id")
    if identifiant is None:
        return {}
    maintenant = timezone.now()
    reservation = reservations_provisoires_actives(maintenant).filter(
        pk=identifiant,
        seance__statut="programmee", seance__debut__gt=maintenant,
    ).only("pk").first()
    return {"reservation_a_reprendre": reservation}
