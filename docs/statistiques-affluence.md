# US-16 — Identifier les périodes de forte affluence

Dans Django Admin, ouvrir **Cinéma → Séances → Affluence**, ou utiliser la
navigation **Statistiques** depuis la page de remplissage (US-15).
Adresse : `/admin/cinema/seance/statistiques/affluence/`.

## Accès et données

L'accès reprend les règles de l'US-15 : compte actif du personnel, avec les deux
permissions `cinema.view_seance` et `reservations.view_reservation`.
Un superutilisateur actif du personnel y a également accès. Une permission de
modification seule ne suffit pas. GET et HEAD sont autorisés ; aucune donnée
métier n'est modifiée et les réponses ne sont pas mises en cache.

Les statistiques utilisent uniquement les réservations **confirmées**. Les
réservations en attente, expirées ou annulées sont exclues. Le compteur partagé
avec l'US-15 décompte les sièges physiques distincts **par séance**, sans joindre
les paiements ni les billets. Le même fauteuil réservé pour deux séances compte
bien deux fois dans le total quotidien.

Les dates sont celles du **début des séances**, dans le fuseau Django courant
(`Europe/Paris` par défaut), et non celles de création ou de paiement des réservations.
Comme dans l'US-15, les séances terminées ou annulées et les films, salles ou places
désactivés restent dans l'historique ; seules les réservations encore confirmées
contribuent au compteur. Les chiffres représentent donc l'état actuel des réservations,
pas un historique figé, un contrôle de présence physique ou une prévision de demande.

## Sélection et comparaison

- La période initiale comprend les 30 derniers jours, aujourd'hui inclus.
- Les dates de début et de fin sont obligatoires, bornes incluses.
- Chaque période est limitée à **366 jours** pour garder une série quotidienne lisible,
  y compris pour une année bissextile complète.
- Les filtres facultatifs film et salle s'appliquent aux deux périodes.
- Pour comparer, renseigner les deux dates de référence.
- Les périodes peuvent se chevaucher et avoir des durées différentes. Chacune
  est calculée indépendamment.
- Les dates invalides, incomplètes, inversées, les périodes trop longues et les
  choix inexistants produisent une erreur de formulaire (HTTP 400), sans statistiques.

Pour analyser un événement comme le festival de Jazz, sélectionner ses dates,
puis les dates de l'édition de référence. Comparer également les moyennes par jour
si les éditions n'ont pas la même durée.

La comparaison affiche le nombre de jours, les totaux, les moyennes quotidiennes
et la variation du total :

```text
écart = total de la période analysée − total de la référence
variation (%) = 100 × écart / total de la référence
moyenne quotidienne = total / nombre de jours calendaires inclus
```

Les décimales sont arrondies à deux chiffres. Si la référence vaut zéro et que
la période analysée est positive, le pourcentage est explicitement non calculable.
Si les deux totaux valent zéro, l'écart et la variation valent zéro.

## Graphiques, indicateurs et classements

Chaque période dispose de sa courbe quotidienne avec ses dates réelles. Les jours
sans réservation sont explicitement complétés avec zéro, même sans séance.
Les deux courbes sont séparées pour conserver les dates propres à chaque période.

La synthèse indique le total, le jour le plus fréquenté et la séance la plus demandée.
Les classements présentent les **10** jours et les **10** séances les plus demandés,
sans limiter le total affiché. Les valeurs nulles sont exclues des classements.
En cas d'égalité : date la plus ancienne, puis numéro de séance pour un même horaire.

Sans place confirmée, un message indique qu'aucune donnée n'est disponible et
aucun faux pic n'est désigné. La période de référence peut avoir des données
même si la période principale est vide, et réciproquement.

Le tableau quotidien dépliable présente les mêmes données que la courbe,
y compris sans JavaScript. Les canevas possèdent un nom accessible. Si la
bibliothèque ne se charge pas, un message renvoie au tableau.

## Organisation technique

- `cinema/statistiques.py` partage le queryset filtré et annoté de l'US-15.
  L'ORM PostgreSQL agrège ensuite les compteurs par date locale de séance.
- Deux requêtes de calcul par période : série quotidienne et classement des
  séances. Quatre en comparaison. `select_related` charge films et salles pour
  éviter les requêtes supplémentaires dans les classements.
- `StatistiquesAffluenceForm` réutilise les champs et validations du formulaire
  de remplissage. La validation des périodes est également appliquée par le service.
- Les vues Admin partagent le contrôle des permissions. Les pages partagent
  une navigation Statistiques et le rendu d'une période est un template réutilisable.
- Les séries sont transmises avec `json_script` ; aucun HTML n'est construit à
  partir des données en JavaScript.
- Aucun modèle, migration ou parcours de réservation/paiement n'est ajouté ou modifié.

Chart.js **4.5.1**, version fixée dans `package.json` et `package-lock.json`,
est servi localement ; aucun CDN ni framework frontend n'est nécessaire.
La copie navigateur minifiée pèse environ 209 ko avant compression HTTP.
Le script de copie retire uniquement la référence à la source map et conserve
les licences MIT de Chart.js et de sa dépendance couleur.

```powershell
npm ci
npm run build:charts
```

La copie prête à servir est dans `cinema/static/cinema/vendor/chartjs/`.
Node.js n'est pas nécessaire au serveur Django pour afficher les graphiques.
Au déploiement, inclure ces ressources dans la collecte habituelle des fichiers
statiques (`python manage.py collectstatic --noinput`).

Références : [intégration Chart.js](https://www.chartjs.org/docs/latest/getting-started/integration.html)
et [accessibilité des canevas](https://www.chartjs.org/docs/latest/general/accessibility.html).

## Vérification

```powershell
python manage.py test tests.cinema.test_affluence tests.cinema.test_statistiques --noinput
python manage.py test --noinput
ruff check .
npm run test:charts
```

Les tests couvrent les statuts de réservation, doubles comptages, dates locales et
changement d'heure, jours à zéro, filtres, classements, comparaison et références
nulles, périodes invalides, permissions, absence de données et de mutations métier.
Ils vérifient également que les requêtes ne croissent pas avec le nombre de séances.

Recette manuelle : ouvrir les deux pages Statistiques avec un compte autorisé,
sélectionner deux périodes connues, vérifier les courbes et les totaux, puis essayer
une période vide, une référence vide, des filtres invalides et un compte sans
permission. Contrôler l'affichage sur petit écran et le tableau quotidien sans JavaScript.
