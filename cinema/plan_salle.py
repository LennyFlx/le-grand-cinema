def organiser_plan(rangees):
    """Ajoute les allées visuelles sans modifier les places et leurs numéros.

    Le plan de référence est composé de 9 rangées, réparties en 2 / 5 / 2.
    Les blocs avant et arrière sont adaptés au nombre de rangées existantes.
    """
    nombre_rangees = len(rangees)
    extremite = max(1, round(nombre_rangees * 2 / 9)) if nombre_rangees >= 3 else 0
    separations = {extremite, nombre_rangees - extremite} if extremite else set()
    plan = []
    for index_rangee, rangee in enumerate(rangees):
        nombre_places = len(rangee["places"])
        centre = min(10, nombre_places) if nombre_places <= 20 else nombre_places - 10
        gauche = (nombre_places - centre) // 2
        allees = {position for position in (gauche, gauche + centre) if 0 < position < nombre_places}
        plan.append({
            **rangee,
            "separation_avant": index_rangee in separations,
            "places": [
                {**place, "allee_avant": index_place in allees}
                for index_place, place in enumerate(rangee["places"])
            ],
        })
    return plan
