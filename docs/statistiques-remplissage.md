# US-14 — Consulter les taux de remplissage des séances

Dans Django Admin, ouvrir **Cinéma → Séances → Taux de remplissage**.
Adresse : `/admin/cinema/seance/statistiques/`.

## Permissions

L’accès nécessite un compte actif du personnel avec les deux permissions
`cinema.view_seance` et `reservations.view_reservation`. Un superutilisateur actif
du personnel dispose également de cet accès. La permission de modification seule
ne donne pas accès aux statistiques. La page est en lecture seule (GET et HEAD).

## Calculs et périmètre

- Chaque ligne correspond à une séance, avec son film, sa salle, sa date, son statut,
  ses places réservées, sa capacité et son taux de remplissage.
- Un siège est compté une seule fois par séance, uniquement dans les réservations
  confirmées. Les réservations en attente, expirées ou annulées sont exclues.
- Les paiements et billets ne sont pas joints au comptage.
- La capacité utilisée est la capacité déclarée de la salle (`Salle.capacite`),
  même si certaines places physiques sont désactivées.
- Taux d’une séance : `100 × places confirmées distinctes / capacité de la salle`.
- Taux global pondéré : `100 × total des places confirmées / somme des capacités
  des salles pour chaque séance filtrée`. Une même salle contribue donc plusieurs
  fois si plusieurs de ses séances correspondent aux filtres.
- Les pourcentages sont affichés avec deux décimales ; sans séance, les compteurs
  et le taux global valent zéro.
- Toutes les séances correspondant aux filtres sont incluses, y compris les
  séances terminées ou annulées ; leur statut reste visible. Les films et salles
  désactivés restent consultables pour préserver l’analyse de l’historique.

Le service dédié est `cinema/statistiques.py`. Les sous-requêtes ORM comptent les
sièges confirmés sans multiplier les capacités lors des agrégations.

## Filtres et pagination

Les filtres par film, salle, date de début et date de fin se combinent.
Les bornes portent sur la date de début de séance, dans le fuseau Django
(`Europe/Paris` par défaut), et sont inclusives. Chaque borne peut être utilisée seule.

Une période inversée, une date invalide ou un film/salle inexistant produit une
erreur de formulaire (HTTP 400), sans afficher de statistiques trompeuses.

Le tableau présente 50 séances par page. La synthèse porte sur tous les résultats
filtrés, indépendamment de la page. Les filtres sont conservés lors de la navigation.
Un message explicite apparaît si aucune séance ne correspond.

## Vérification

```powershell
python manage.py test tests.cinema.test_statistiques --noinput
```

Pour la recette manuelle : ouvrir la page avec un compte autorisé, combiner les
filtres, contrôler les places de plusieurs séances et vérifier le taux pondéré
avec deux salles de capacités différentes. Tester également une période sans résultat
et un compte ne possédant que la permission de consultation des séances.
