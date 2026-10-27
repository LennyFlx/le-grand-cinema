# Convention de gestion des branches Git

## 1. Objectif

Ce document définit les conventions Git utilisées dans le cadre du projet **Le Grand Cinéma**.

L'objectif est de garantir une organisation cohérente du développement, de faciliter le suivi des modifications et de conserver un historique clair des fonctionnalités développées.

Le projet repose sur un workflow Git simplifié, inspiré de Git Flow, avec deux branches principales et des branches de travail temporaires.

## 2. Organisation des branches

### `main` — Branche stable

La branche `main` contient les versions stables et validées de l'application.

Aucun développement ne doit être réalisé directement sur cette branche.

Les modifications y sont intégrées depuis `develop`, lorsqu'un ensemble de fonctionnalités a été testé et validé.

### `develop` — Branche d'intégration

La branche `develop` centralise les fonctionnalités en cours de développement.

Elle constitue la base de création des nouvelles branches de travail.

Les fonctionnalités terminées sont intégrées à `develop` au moyen de Pull Requests, après vérification du code et des tests.

### Branches de travail

Chaque fonctionnalité ou correction est développée sur une branche dédiée, créée à partir de `develop`.

Les conventions de nommage sont les suivantes :

| Préfixe | Utilisation | Exemple |
|---|---|---|
| `feature/` | Nouvelle fonctionnalité ou User Story | `feature/reservation-places` |
| `fix/` | Correction d'un bug | `fix/double-reservation` |
| `docs/` | Documentation | `docs/documentation-api` |
| `chore/` | Configuration ou maintenance technique | `chore/configuration-backend` |
| `test/` | Ajout ou amélioration de tests | `test/service-reservation` |

Les noms des branches doivent être explicites, rédigés en minuscules, sans accents ni espaces. Les mots sont séparés par des tirets.

### Convention linguistique

Afin de conserver un historique homogène et facilement compréhensible dans le cadre de l'évaluation, les noms des branches et les descriptions des commits sont rédigés en **français**.

Les préfixes techniques (`feature/`, `fix/`, `feat:`, etc.) restent en anglais, conformément aux conventions couramment utilisées dans les projets Git.

## 3. Cycle de développement

Le développement d'une fonctionnalité suit les étapes suivantes :

1. Se positionner sur `develop` et récupérer les dernières modifications du dépôt distant.
2. Créer une branche `feature/nom-fonctionnalite` à partir de `develop`.
3. Développer la fonctionnalité en respectant les conventions du projet.
4. Réaliser les tests nécessaires et vérifier le fonctionnement.
5. Effectuer des commits réguliers avec des messages explicites.
6. Publier la branche sur GitHub.
7. Créer une Pull Request vers `develop`.
8. Vérifier les modifications et les éventuels conflits.
9. Fusionner la Pull Request après validation.
10. Supprimer la branche de travail devenue inutile, après vérification de la fusion.

Les fonctionnalités doivent être développées progressivement, conformément aux User Stories définies dans le bloc 2.

## 4. Convention des commits

Les messages de commit suivent une convention inspirée de **Conventional Commits**.

Format utilisé :

`type: description courte et explicite`

| Type | Utilisation | Exemple |
|---|---|---|
| `feat` | Ajout d'une fonctionnalité | `feat: ajouter la liste des films` |
| `fix` | Correction d'un bug | `fix: empêcher les réservations en double` |
| `docs` | Documentation | `docs: documenter les conventions Git` |
| `test` | Tests | `test: ajouter les tests de réservation` |
| `refactor` | Réorganisation sans changement fonctionnel | `refactor: simplifier le service de réservation` |
| `chore` | Configuration et maintenance | `chore: configurer l'environnement Python` |

Chaque commit doit correspondre à une modification identifiable, afin de faciliter la compréhension et le suivi de l'historique.

Les descriptions sont rédigées en français, de préférence à l'infinitif, afin de conserver une formulation cohérente.

## 5. Pull Requests et validation

Les Pull Requests permettent de vérifier les modifications avant leur intégration à la branche `develop`.

Avant toute fusion, les points suivants doivent être contrôlés :

- La fonctionnalité correspond aux besoins définis dans la User Story.
- Le code respecte les conventions du projet.
- Les tests pertinents sont exécutés avec succès.
- Aucun fichier sensible ou inutile n'est ajouté au dépôt.
- Les éventuels conflits Git sont résolus.
- La documentation est actualisée si nécessaire.

Chaque Pull Request doit posséder un titre explicite et une description résumant les modifications apportées ainsi que les tests réalisés.

Dans le cadre de ce projet individuel, la validation est réalisée par le développeur lui-même.

## 6. Synchronisation entre les environnements de travail

Le projet peut être développé depuis plusieurs postes de travail.

GitHub constitue le dépôt distant central permettant de synchroniser le code.

Avant de commencer une session de développement, le développeur vérifie l'état de son dépôt et récupère les dernières modifications de la branche concernée.

À la fin d'une session, les modifications terminées sont enregistrées dans des commits et publiées sur GitHub.

Les modifications non terminées peuvent également être conservées sur une branche de travail dédiée, à condition qu'elles ne perturbent pas les branches principales.

Les fichiers propres à chaque environnement local, notamment les environnements virtuels Python, les dépendances installées et les fichiers contenant des secrets, ne doivent pas être versionnés.

## 7. Gestion des versions stables

Lorsqu'un ensemble de fonctionnalités est terminé, testé et validé, la branche `develop` peut être fusionnée dans `main` au moyen d'une Pull Request.

Cette opération permet de conserver une branche principale stable, distincte des développements en cours.

Des tags Git pourront être utilisés pour identifier les versions importantes de l'application, par exemple `v1.0.0` pour une première version stable.

---

Ce workflow permet d'assurer la traçabilité des développements, de limiter les risques liés aux modifications simultanées et de faciliter la maintenance du projet Le Grand Cinéma.