# Le Grand Cinéma

Application web de réservation de places de cinéma, développée avec **Django** dans le cadre d'un projet d'évaluation en développement d'applications Python. Elle permet aux spectateurs de consulter la programmation et de réserver leurs places en ligne, sans créer de compte.

## Fonctionnalités

- Consultation des films à l'affiche et des séances par date.
- Affichage du plan de salle et des disponibilités pour chaque séance.
- Sélection de 1 à 8 places et réservation provisoire avec contrôle des conflits.
- Paiement par carte via **Stripe Checkout** et confirmation par webhook signé.
- Génération de billets numériques (**PDF et QR code**) et envoi par e-mail.
- Consultation des billets à partir d'un lien personnel signé et contrôle par le personnel autorisé.
- Administration des films, des séances et des réservations via Django Admin.

## Technologies

| Domaine | Technologies |
|---|---|
| Backend | Python, Django 5.2 |
| Base de données | PostgreSQL, ORM Django |
| Interface | Templates Django, Tailwind CSS 4, JavaScript |
| Paiement | Stripe Checkout |
| Qualité et tests | Tests Django, Coverage, Ruff, k6 |

L'application est organisée autour de trois modules métier : `cinema` (catalogue et séances), `reservations` (places, réservations et billets) et `paiements` (transactions Stripe).

## Installation locale

**Prérequis :** Python 3.12 ou 3.13 et PostgreSQL. Les commandes ci-dessous sont adaptées à PowerShell sous Windows.

Depuis la racine du projet :

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Créer une base de données PostgreSQL et un utilisateur disposant des droits nécessaires, puis compléter le fichier `.env` avec au minimum :

- `DJANGO_SECRET_KEY` : clé secrète Django propre à l'environnement.
- `DB_NAME`, `DB_USER`, `DB_PASSWORD` : paramètres de connexion PostgreSQL.
- `DJANGO_DEBUG=True` et `DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost` pour le développement local.

Les migrations utilisent notamment l'extension PostgreSQL `btree_gist` ; sa création nécessite les droits appropriés.

Initialiser la base et charger les données de démonstration :

```powershell
python manage.py migrate
python manage.py loaddata salles_initiales catalogue_initial
python scripts/generer_fixtures_demo.py
python manage.py loaddata seances_demo
python manage.py runserver
```

L'application est alors accessible sur **http://127.0.0.1:8000/**. L'administration se trouve sur `/admin/` ; un compte administrateur peut être créé avec `python manage.py createsuperuser`.

> Les séances de démonstration sont générées à partir de la date du jour. Les affiches de films, référencées dans les fixtures, doivent être placées séparément dans le répertoire `media/affiches/` si elles sont utilisées.

## Configuration complémentaire

Les paramètres de Stripe et de l'envoi d'e-mails sont décrits dans [`.env.example`](.env.example). Sans configuration Stripe, le paiement en ligne n'est pas utilisable. Par défaut, les e-mails sont affichés dans la console du serveur Django.

Le CSS compilé est présent dans le dépôt. Pour le régénérer après modification des styles, installer Node.js/npm puis exécuter :

```powershell
npm ci
npm run build:css
```

**Important :** conserver les secrets dans `.env`, jamais dans Git. Le serveur `runserver` est réservé au développement local.

## Tests et qualité

Installer les outils de développement :

```powershell
python -m pip install -r requirements-dev.txt
```

Puis lancer les vérifications :

```powershell
python manage.py test
ruff check .
coverage run manage.py test
coverage report
```

Des scénarios de performance k6 sont également disponibles dans `tests-performance/`. Ils nécessitent une installation distincte de k6 et un serveur Django local en cours d'exécution. Leur protocole et les résultats obtenus sont détaillés dans [la documentation des tests de performance](docs/tests-performance.md).

## Tester le paiement Stripe

Le paiement est intégré via **Stripe Checkout en mode test** : aucun argent réel n'est débité.

Pour tester le parcours, sélectionner une séance, choisir des places, créer une réservation provisoire, puis accéder au formulaire de paiement Stripe.

Renseigner les informations suivantes :

| Champ | Valeur à saisir |
|---|---|
| Numéro de carte | `4242 4242 4242 4242` |
| Date d'expiration | Une date future, par exemple `12/34` |
| Code CVC | `123` (ou trois chiffres quelconques) |
| Nom du titulaire | `Test Utilisateur` |
| Autres informations | Des données fictives |

Cette carte simule un **paiement accepté**.

Pour tester un **paiement refusé**, utiliser le numéro `4000 0000 0000 0002` avec les mêmes informations.

> **Important :** ces numéros fonctionnent uniquement avec Stripe en mode test. La confirmation de la réservation et la génération des billets nécessitent également que le webhook Stripe soit correctement configuré.

Pour d'autres scénarios, consulter la [documentation officielle des cartes de test Stripe](https://docs.stripe.com/testing).


### Réception des billets par e-mail

Sur la **version hébergée de l'application**, l'envoi des e-mails est opérationnel via SMTP.

Pour tester l'intégralité du parcours de réservation, il est nécessaire de renseigner **une adresse e-mail valide à laquelle vous avez accès** lors de la réservation.

Après la validation du paiement en mode test, un e-mail de confirmation contenant les informations de réservation et les billets numériques est envoyé à cette adresse.

> **Remarque :** pensez à vérifier le dossier des courriers indésirables si l'e-mail n'apparaît pas dans votre boîte de réception.
## Documentation

- [Architecture technique](docs/architecture.md)
- [Guide de déploiement](docs/deploiement.md)
- [Stratégie de tests](docs/tests.md)
- [Tests de performance k6](docs/tests-performance.md)
- [Modélisation des données](docs/modele-donnees.md)
- [Workflow Git](docs/git-workflow.md)

Projet pédagogique : l'utilisation du paiement Stripe doit être configurée en **mode test** pour les essais et les démonstrations.