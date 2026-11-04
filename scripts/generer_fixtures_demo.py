"""Génère une fixture de séances à partir du catalogue et des salles du projet."""

import argparse
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


RACINE = Path(__file__).resolve().parent.parent
FIXTURES = RACINE / "cinema" / "fixtures"
FUSEAU = ZoneInfo("Europe/Paris")


def entier_positif(valeur):
    nombre = int(valeur)
    if nombre <= 0:
        raise argparse.ArgumentTypeError("Le nombre de jours doit être strictement positif.")
    return nombre


def generer_seances(films, salles, date_debut, jours):
    if jours <= 0:
        raise ValueError("Le nombre de jours doit être strictement positif.")
    films = sorted((film for film in films if film["fields"]["actif"]), key=lambda film: film["pk"])
    salles = sorted((salle for salle in salles if salle["fields"]["active"]), key=lambda salle: salle["pk"])
    if not films or not salles:
        raise ValueError("Le catalogue doit contenir des films actifs et des salles actives.")
    seances = []
    for jour in range(jours):
        date_seance = date_debut + timedelta(days=jour)
        for index_salle, salle in enumerate(salles):
            prochaine = None
            for index_horaire, heure in enumerate((14, 18, 21)):
                film = films[(jour + index_salle + index_horaire) % len(films)]
                duree = film["fields"]["duree"]
                if duree <= 0:
                    raise ValueError("La durée des films doit être strictement positive.")
                debut = datetime.combine(date_seance, time(heure), tzinfo=FUSEAU)
                if prochaine is not None:
                    debut = max(debut, prochaine)
                fin = debut + timedelta(minutes=duree)
                if fin.date() != date_seance:
                    raise ValueError("Les durées du catalogue ne permettent pas trois séances dans la journée.")
                # Identifiants réservés à la démonstration, distincts des premières saisies manuelles.
                seances.append({
                    "model": "cinema.seance",
                    "pk": 100000 + len(seances),
                    "fields": {
                        "film": film["pk"],
                        "salle": salle["pk"],
                        "debut": debut.isoformat(),
                        "fin": fin.isoformat(),
                        "prix": "12.00",
                        "statut": "programmee",
                    },
                })
                prochaine = fin + timedelta(minutes=30)
    return seances


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date-debut", type=date.fromisoformat,
                        default=datetime.now(FUSEAU).date(), help="Premier jour au format AAAA-MM-JJ (défaut : aujourd'hui).")
    parser.add_argument("--jours", type=entier_positif, default=14, help="Durée en jours (défaut : 14).")
    parser.add_argument("--sortie", type=Path, default=FIXTURES / "seances_demo.json",
                        help="Chemin du fichier JSON à générer.")
    options = parser.parse_args(arguments)
    try:
        catalogue = json.loads((FIXTURES / "catalogue_initial.json").read_text(encoding="utf-8"))
        salles = json.loads((FIXTURES / "salles_initiales.json").read_text(encoding="utf-8"))
        seances = generer_seances(
            [objet for objet in catalogue if objet["model"] == "cinema.film"],
            [objet for objet in salles if objet["model"] == "cinema.salle"],
            options.date_debut,
            options.jours,
        )
    except (OSError, ValueError, KeyError, OverflowError) as erreur:
        parser.error(str(erreur))
    contenu = "[\n" + ",\n".join("  " + json.dumps(seance, ensure_ascii=False) for seance in seances) + "\n]\n"
    options.sortie.write_text(contenu, encoding="utf-8")
    print(f"{len(seances)} séances générées du {options.date_debut} au "
          f"{options.date_debut + timedelta(days=options.jours - 1)} : {options.sortie}")
    print("Les séances du jour déjà commencées ne sont pas affichées sur le site.")
    print("Pour charger ce fichier : python manage.py loaddata", options.sortie)


if __name__ == "__main__":
    main()
