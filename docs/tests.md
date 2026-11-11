# Stratégie de tests — Le Grand Cinéma

## 1. Objectif

Les tests ont été ajoutés progressivement pendant le développement de **Le Grand Cinéma** afin de vérifier le fonctionnement des parcours métier, la cohérence des données et la gestion des situations d'erreur.

La stratégie repose sur trois approches complémentaires : les tests automatisés Django, l'analyse statique avec Ruff et les tests de performance HTTP avec k6.

## 2. Environnement et outils

| Outil | Utilisation dans le projet |
|---|---|
| `django.test` | Tests des modèles, services, formulaires, vues et parcours métier |
| PostgreSQL | Vérification des contraintes, transactions et accès concurrents |
| `unittest.mock` | Simulation des échanges externes, notamment avec Stripe |
| Coverage | Mesure de la couverture des lignes et des branches Python |
| Ruff | Analyse statique du code Python |
| k6 | Simulation de consultations HTTP avec plusieurs utilisateurs virtuels |

Les dépendances de test et de qualité sont déclarées dans [`requirements-dev.txt`](../requirements-dev.txt), à l'exception de k6, installé séparément.

## 3. Tests fonctionnels et métier

Les tests Django se trouvent dans [`tests/`](../tests/) et sont répartis par application.

### 3.1 Catalogue et programmation — `tests/cinema/`

Les tests vérifient notamment :

- L'affichage des films, leurs informations et les séances proposées.
- Les contraintes de programmation : horaires, chevauchements, salles et films inactifs.
- La création, la modification et l'annulation des séances dans l'administration.
- La génération du plan de salle et le chargement des fixtures.

### 3.2 Réservations et billets — `tests/reservations/`

Les scénarios couvrent notamment :

- La sélection des places et le calcul de leur disponibilité.
- La création et l'expiration des réservations provisoires.
- Le refus d'une place déjà occupée et la gestion de demandes simultanées.
- Le contrôle des accès aux réservations, la protection CSRF et les permissions.
- La génération, la consultation et le contrôle des billets PDF et QR code.
- L'envoi et le renvoi des billets par e-mail, y compris certains cas de concurrence.
- La conservation des données lors de migrations entre applications Django.

Les tests de concurrence utilisent notamment `TransactionTestCase` et des appels simultanés pour contrôler le comportement des transactions PostgreSQL.

### 3.3 Paiements — `tests/paiements/`

Les tests portent sur :

- La création des sessions de paiement Stripe Checkout.
- La vérification des notifications Stripe (*webhooks* signés).
- Le traitement des confirmations, expirations et événements répétés.
- L'idempotence des opérations, afin d'éviter une double confirmation ou émission de billets.
- Les erreurs réseau et les réponses inattendues du prestataire.

Les appels Stripe sont simulés dans les tests : **aucun paiement réel n'est nécessaire**. Les tests d'envoi d'e-mails utilisent également un backend de test en mémoire lorsqu'ils vérifient les messages produits.

## 4. Exécution des tests Django

### 4.1 Préparation

Depuis la racine du dépôt, dans l'environnement virtuel Python :

```powershell
python -m pip install -r requirements-dev.txt
```

Le fichier `.env` doit contenir une configuration PostgreSQL valide. L'utilisateur PostgreSQL doit pouvoir créer une base de test : Django crée une base dédiée pour exécuter les tests, puis la supprime à la fin de l'exécution normale.

### 4.2 Commandes utilisées

```powershell
# Exécuter l'ensemble des tests Django
python manage.py test

# Exécuter les tests d'une application
python manage.py test tests.reservations

# Cibler un module particulier
python manage.py test tests.paiements.test_webhook
```

Ces commandes ne nécessitent pas le lancement de `runserver`. Elles doivent être exécutées sur un **environnement de développement ou de test**, jamais en utilisant les identifiants de la base de production.

## 5. Couverture du code

Coverage est configuré dans [`.coveragerc`](../.coveragerc). La mesure inclut les **lignes et les branches**, concerne les trois applications métier et exclut les migrations Django.

```powershell
coverage run manage.py test
coverage report
coverage html
```

Le rapport HTML est généré dans `htmlcov/index.html`. Le projet ne définit pas actuellement de pourcentage minimal de couverture ; les résultats doivent être consultés à chaque exécution, sans annoncer un taux non mesuré.

## 6. Analyse statique — Ruff

Ruff permet de repérer des problèmes de style, d'importation et certaines erreurs potentielles dans le code Python.

```powershell
ruff check .
```

Les règles et exclusions spécifiques au projet sont centralisées dans [`ruff.toml`](../ruff.toml), notamment l'exclusion des migrations générées par Django. Ruff complète les tests fonctionnels, mais ne les remplace pas.

## 7. Tests de performance — k6

Quatre scripts sont disponibles dans [`tests-performance/`](../tests-performance/) :

| Script | Scénario |
|---|---|
| `accueil.js` | Consultation répétée de la page d'accueil |
| `montee_charge.js` | Augmentation progressive de la charge sur l'accueil |
| `parcours_consultation.js` | Catalogue, fiche film et détail d'une séance |
| `disponibilites.js` | Consultation concurrente du plan de salle d'une même séance |

Ils ont été exécutés **en local**, sur le serveur de développement Django, avec un maximum de dix utilisateurs virtuels. Les quatre exécutions documentées représentent **816 requêtes HTTP sans erreur** ; les seuils configurés ont été respectés.

Ces résultats ne constituent pas une validation des performances du VPS en production et ne remplacent pas les tests transactionnels de réservation simultanée.

Les commandes, métriques et limites figurent dans le [rapport des tests de performance](tests-performance.md).

## 8. Vérifications avant intégration

Avant de proposer une modification via une Pull Request, la procédure retenue est de :

1. Exécuter les tests Django concernés, puis la suite complète lorsque c'est pertinent.
2. Vérifier l'analyse statique avec Ruff.
3. Examiner les modifications Git et s'assurer qu'aucun secret ou fichier généré n'est ajouté.
4. Vérifier manuellement le parcours utilisateur impacté, si nécessaire.

Les tests de navigation automatisés avec Cypress sont envisagés, mais **ne sont pas encore intégrés** dans la version du dépôt examinée.
