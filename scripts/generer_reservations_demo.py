"""Génère des réservations de démonstration pour les séances d'une fixture existante."""

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from random import Random
from zoneinfo import ZoneInfo

RACINE = Path(__file__).resolve().parent.parent
FIXTURES = RACINE / "cinema" / "fixtures"
FUSEAU = ZoneInfo("Europe/Paris")


def entier_positif(valeur):
    nombre = int(valeur)
    if nombre <= 0:
        raise argparse.ArgumentTypeError("Le délai doit être strictement positif.")
    return nombre



def eparpiller_places(places, graine):
    """Forme des blocs de 2 ou 3 sièges séparés par une place libre et les allées."""
    hasard = Random(graine)
    par_rangee = defaultdict(list)
    for place in places:
        par_rangee[place["fields"]["rangee"]].append(place)
    blocs_par_rangee = []
    for rangee in par_rangee.values():
        segments = []
        segment = []
        for place in rangee:
            if segment:
                precedent = segment[-1]["fields"]["numero"]
                if place["fields"]["numero"] != precedent + 1 or precedent in (5, 15):
                    segments.append(segment)
                    segment = []
            segment.append(place)
        segments.append(segment)
        blocs = []
        for segment in segments:
            index = hasard.randrange(2) if len(segment) > 3 else 0
            while len(segment) - index >= 2:
                taille = min(hasard.choice((2, 3)), len(segment) - index)
                blocs.append(segment[index:index + taille])
                index += taille + 1
        hasard.shuffle(blocs)
        blocs_par_rangee.append(blocs)
    hasard.shuffle(blocs_par_rangee)
    return [blocs[tour]
            for tour in range(max((len(blocs) for blocs in blocs_par_rangee), default=0))
            for blocs in blocs_par_rangee if tour < len(blocs)]

def generer_reservations(seances, places, reference, attente_minutes=15):
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=FUSEAU)
    if attente_minutes <= 0:
        raise ValueError("Le délai doit être strictement positif.")
    places_par_salle = defaultdict(list)
    for place in places:
        if place["model"] == "cinema.place" and place["fields"]["active"]:
            places_par_salle[place["fields"]["salle"]].append(place)
    for salle_places in places_par_salle.values():
        salle_places.sort(key=lambda place: (place["fields"]["rangee"], place["fields"]["numero"]))
    if not places_par_salle:
        raise ValueError("La fixture des salles ne contient aucune place active.")

    aujourd_hui = reference.astimezone(FUSEAU).date()
    seances_du_jour = []
    for seance in sorted(seances, key=lambda seance: seance["pk"]):
        champs = seance["fields"]
        debut = datetime.fromisoformat(champs["debut"])
        if debut.tzinfo is None:
            raise ValueError("Les horaires des séances doivent préciser leur fuseau horaire.")
        if (debut.astimezone(FUSEAU).date() != aujourd_hui
                or debut <= reference or champs["statut"] != "programmee"):
            continue
        if not places_par_salle.get(champs["salle"]):
            raise ValueError(f"Aucune place active pour la salle {champs['salle']}.")
        seances_du_jour.append(seance)
    if not seances_du_jour:
        return []
    # La dernière séance à venir dans une des plus petites salles sera complète.
    seance_complete = min(seances_du_jour, key=lambda seance: (
        len(places_par_salle[seance["fields"]["salle"]]),
        -datetime.fromisoformat(seance["fields"]["debut"]).timestamp(),
        seance["pk"],
    ))["pk"]
    objets = []
    identifiant_reservation = 100000
    identifiant_association = 100000
    for seance in seances_du_jour:
        champs = seance["fields"]
        debut = datetime.fromisoformat(champs["debut"])
        salle_places = places_par_salle[champs["salle"]]
        heure = debut.astimezone(FUSEAU).hour
        groupes = []
        if seance["pk"] == seance_complete:
            groupes = [("confirmee", salle_places[index:index + 3], False)
                       for index in range(0, len(salle_places), 3)]
        elif not (heure < 18 and champs["salle"] == min(places_par_salle)):
            blocs = eparpiller_places(salle_places, seance["pk"])
            pourcentage = (20 if heure < 18 else 35 if heure < 21 else 45) + seance["pk"] % 11
            objectif = len(salle_places) * pourcentage // 100
            occupees = 0
            # Les trois derniers blocs servent à vérifier les réservations non bloquantes.
            for index, bloc in enumerate(blocs[:-3]):
                if occupees >= objectif:
                    break
                statut = "en_attente" if index % 4 == 3 else "confirmee"
                groupes.append((statut, bloc, False))
                occupees += len(bloc)
            for bloc, (statut, deja_expiree) in zip(
                blocs[-3:], (("expiree", False), ("annulee", False), ("en_attente", True)),
            ):
                groupes.append((statut, bloc, deja_expiree))
        prix = Decimal(champs["prix"])
        for statut, groupe, deja_expiree in groupes:
            en_attente = statut == "en_attente" and not deja_expiree
            creation = reference if en_attente else reference - timedelta(hours=2)
            expiration = (min(reference + timedelta(minutes=attente_minutes), debut)
                          if en_attente else reference - timedelta(hours=1))
            objets.append({
                "model": "reservations.reservation",
                "pk": identifiant_reservation,
                "fields": {
                    "seance": seance["pk"],
                    "email": f"demo-{identifiant_reservation}@example.com",
                    "statut": statut,
                    "date_creation": creation.isoformat(),
                    "date_expiration": expiration.isoformat(),
                    "montant_total": str(prix * len(groupe)),
                },
            })
            for place in groupe:
                objets.append({
                    "model": "reservations.reservationplace",
                    "pk": identifiant_association,
                    "fields": {"reservation": identifiant_reservation,
                               "place": place["pk"], "prix_unitaire": str(prix)},
                })
                identifiant_association += 1
            identifiant_reservation += 1
    return objets


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seances", type=Path, default=FIXTURES / "seances_demo.json",
                        help="Fixture des séances déjà générée et chargée.")
    parser.add_argument("--sortie", type=Path, default=FIXTURES / "reservations_demo.json")
    parser.add_argument("--date-reference", type=datetime.fromisoformat,
                        default=datetime.now(FUSEAU), help="Instant de référence ISO (défaut : maintenant, heure de Paris).")
    parser.add_argument("--attente-minutes", type=entier_positif, default=15,
                        help="Durée du blocage des réservations en attente (défaut : 15 minutes).")
    options = parser.parse_args(arguments)
    try:
        seances = json.loads(options.seances.read_text(encoding="utf-8"))
        places = json.loads((FIXTURES / "salles_initiales.json").read_text(encoding="utf-8"))
        objets = generer_reservations(seances, places, options.date_reference, options.attente_minutes)
        contenu = "[\n" + ",\n".join("  " + json.dumps(objet, ensure_ascii=False) for objet in objets) + "\n]\n"
        options.sortie.write_text(contenu, encoding="utf-8")
    except (OSError, ValueError, KeyError, OverflowError) as erreur:
        parser.error(str(erreur))
    nombre = sum(objet["model"] == "reservations.reservation" for objet in objets)
    print(f"{nombre} réservations et {len(objets) - nombre} places associées générées : {options.sortie}")
    print("Réservations uniquement pour les séances encore à venir le jour de référence (heure de Paris).")
    if not objets:
        print("Aucune séance à venir ce jour-là : le fichier généré est vide.")
    else:
        print("Une séance est complète ; les autres ont des blocs dispersés de 2 ou 3 places.")
    print(f"Les réservations en attente expireront {options.attente_minutes} minutes après l'instant de référence.")
    print("Pour charger ce fichier : python manage.py loaddata", options.sortie)


if __name__ == "__main__":
    main()
