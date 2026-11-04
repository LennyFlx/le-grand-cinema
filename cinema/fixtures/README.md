## Données de démonstration

Après les migrations, charger les salles, places et le catalogue :

```powershell
python manage.py loaddata salles_initiales catalogue_initial
```

Générer les séances pour 14 jours à compter d'aujourd'hui, puis les charger :

```powershell
python scripts/generer_fixtures_demo.py
python manage.py loaddata seances_demo
```

Pour choisir une autre période :

```powershell
python scripts/generer_fixtures_demo.py --date-debut 2026-11-05 --jours 14
```

### Réservations

Une fois `seances_demo` chargée, générer les réservations à partir du même fichier :

```powershell
python scripts/generer_reservations_demo.py
python manage.py loaddata reservations_demo
```

Les séances couvrent deux semaines : 420 séances avec les 10 salles actuelles.
Les réservations concernent uniquement les séances encore à venir aujourd'hui,
en heure de Paris. Les autres jours restent sans réservations générées.
Les places sont occupées par blocs dispersés de 2 ou 3, sur plusieurs rangées.
Une séance dans une petite salle est entièrement réservée par des réservations
confirmées, et reste complète après expiration des réservations en attente.

Si aucune séance n'est encore à venir aujourd'hui, le script écrit une fixture vide.
`--date-reference` change à la fois le jour ciblé et l'instant de création des réservations.
Pour remplacer un ancien jeu contenant des réservations sur plusieurs jours, il faut
retirer ces anciennes données avant de charger le nouveau : `loaddata` ne les supprime pas.

Le blocage provisoire dure 15 minutes à compter de la génération. Pour choisir sa durée
et, si nécessaire, l'instant de référence des réservations :

```powershell
python scripts/generer_reservations_demo.py --attente-minutes 60
python scripts/generer_reservations_demo.py --date-reference 2026-11-04T12:00:00+01:00 --attente-minutes 60
```
