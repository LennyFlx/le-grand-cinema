# Le Grand Cinéma - Tableau de traçabilité des User Stories

Ce tableau permet de retrouver, pour chaque User Story du backlog, la branche utilisée et la ou les Pull Requests associées.
Les liens renvoient directement aux éléments d'origine. 

| User Story | Branche(s) Git | Pull Request(s) fusionnée(s) |
|---|---|---|
| [US-01 - Consulter les films à l'affiche](https://trello.com/c/I3LvGtt0) | `feature/consultation-films` ; `feature/informations-films` (complément) | [#9](https://github.com/LennyFlx/le-grand-cinema/pull/9) ; [#10](https://github.com/LennyFlx/le-grand-cinema/pull/10) (complément) |
| [US-02 - Consulter les horaires des séances](https://trello.com/c/cv4QNS0z) | `feature/consultation-films` | [#9](https://github.com/LennyFlx/le-grand-cinema/pull/9) |
| [US-03 - Visualiser les disponibilités](https://trello.com/c/uh2YPKmp) | `feature/disponibilites-seances` | [#11](https://github.com/LennyFlx/le-grand-cinema/pull/11) |
| [US-04 - Sélectionner une ou plusieurs place](https://trello.com/c/5zM2rLUs) | `feature/selection-place` | [#14](https://github.com/LennyFlx/le-grand-cinema/pull/14) |
| [US-05 - Confirmer une réservation](https://trello.com/c/cietOr50) | `feature/reservation-provisoire` | [#15](https://github.com/LennyFlx/le-grand-cinema/pull/15) |
| [US-06 - Payer sa réservation en ligne](https://trello.com/c/eekgZa03) | `feature/paiement-stripe` | [#16](https://github.com/LennyFlx/le-grand-cinema/pull/16) |
| [US-07 - Accéder à son billet dématérialisé](https://trello.com/c/Q8ehJKZn) | `feature/billets-dematerialises` ; `feature/qr-code-billets` (complément) | [#17](https://github.com/LennyFlx/le-grand-cinema/pull/17) ; [#18](https://github.com/LennyFlx/le-grand-cinema/pull/18) (complément) |
| [US-08 - Ajouter un film à la programmation](https://trello.com/c/5vkLPSaq) | `feature/ajout-film` ; `feat/ajout-affiches-admin` (complément) | [#4](https://github.com/LennyFlx/le-grand-cinema/pull/4) ; [#31](https://github.com/LennyFlx/le-grand-cinema/pull/31) (complément) |
| [US-09 - Modifier ou retirer un film de la programmation](https://trello.com/c/GDvQxDwT) | `feature/modification-retrait-film` | [#5](https://github.com/LennyFlx/le-grand-cinema/pull/5) |
| [US-10 - Créer une séance](https://trello.com/c/93fnwJ50) | `feature/creation-seance` | [#6](https://github.com/LennyFlx/le-grand-cinema/pull/6) |
| [US-11 - Modifier ou annuler une séance](https://trello.com/c/HXw8WVSy) | `feature/modification-annulation-seance` | [#8](https://github.com/LennyFlx/le-grand-cinema/pull/8) |
| [US-12 - Consulter les réservations](https://trello.com/c/2SSxIIOi) | `feature/consultation-reservations` | [#19](https://github.com/LennyFlx/le-grand-cinema/pull/19) |
| [US-13 - Annuler une réservation](https://trello.com/c/Fh4y0L7A) | `feature/annulation-reservation` | [#20](https://github.com/LennyFlx/le-grand-cinema/pull/20) |

## Précisions

- **US-01 et US-02** partagent la PR [#9](https://github.com/LennyFlx/le-grand-cinema/pull/9), car la page de programmation permet à la fois de consulter les films et 
leurs séances. La PR #10 enrichit les informations des films.
- **US-07 et US-08** comprennent des PR complémentaires (#18 pour la vérification par QR code, #31 pour l'ajout d'affiches dans l'administration).
- Les autres PR du dépôt portent principalement sur les **fondations, la modélisation, les tests, la qualité, la sécurité,
la documentation et le déploiement**. Elles ne sont pas artificiellement rattachées à une seule User Story métier.
