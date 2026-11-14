# Tests de performance — k6

## Objectif

Évaluer les temps de réponse et la stabilité de l'application Le Grand Cinéma sous une charge modérée, à l'aide de l'outil Grafana k6.

## Environnement de test

- Application Django exécutée localement avec `runserver`.
- Base de données PostgreSQL.
- k6 version 2.3.0.
- Jusqu'à 10 utilisateurs virtuels simultanés.

## Scénarios réalisés

| Scénario | Requêtes | Moyenne | p95 | Erreurs HTTP |
|---|---:|---:|---:|---:|
| Accueil | 75 | 44,46 ms | 88,24 ms | 0 % |
| Montée en charge | 246 | 38,22 ms | 39,51 ms | 0 % |
| Parcours de consultation | 255 | 47,84 ms | 138,73 ms | 0 % |
| Disponibilités des places | 240 | 57,65 ms | 156,40 ms | 0 % |

**Total : 816 requêtes HTTP exécutées, sans erreur.**

Les scénarios couvrent la consultation du catalogue, des fiches de films, des séances et du plan de salle.

## Critères de validation

Pour les scénarios comportant des seuils :

- Temps de réponse au 95e percentile inférieur à 500 ms.
- Taux d'échec HTTP inférieur à 1 %.
- Taux de vérifications supérieur à 99 % pour les scénarios concernés.

Tous les seuils configurés ont été respectés.

## Analyse

Les résultats montrent que l'application répond correctement à une charge modérée dans l'environnement local utilisé.

La consultation des disponibilités présente les temps de réponse moyens les plus élevés parmi les scénarios étudiés, sans dépassement des seuils définis.

## Limites

Les tests ont été réalisés sur le serveur de développement Django. Ils ne permettent donc pas de mesurer les performances réelles du déploiement Gunicorn/Nginx sur VPS.

Les scénarios portent sur des consultations HTTP et ne constituent pas des tests de réservation concurrente ni de paiement.

Les performances pourront être réévaluées sur un environnement de préproduction représentatif.

## Conclusion

Les tests k6 constituent une première validation des performances de consultation de l'application. Aucun échec HTTP ni dépassement de seuil n'a été observé dans les scénarios exécutés.