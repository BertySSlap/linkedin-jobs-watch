# Veille d'offres d'emploi LinkedIn

Vérifie automatiquement les offres publiées par une ou plusieurs entreprises
sur LinkedIn, via l'API publique invitée. Chaque nouvelle offre déclenche une
notification push ntfy.sh cliquable.

## Fonctionnement

- Exécution aux minutes **03, 13, 23, 33, 43 et 53** de chaque heure.
- Aucun filtre de contrat : CDI, CDD, stage, alternance, apprentissage et autres.
- Les erreurs apparaissent en rouge dans les journaux GitHub Actions.
- La date UTC de la dernière vérification réussie est conservée dans `etat.json`.
- Aucune alerte lors de la première surveillance d'une entreprise.

## Configuration

Deux secrets sont nécessaires dans **Settings → Secrets and variables → Actions** :

| Secret | Contenu |
|---|---|
| `COMPANY_ID` | Une ou plusieurs entreprises : `id=Nom,id=Autre nom` |
| `NTFY_TOPIC` | Nom du canal ntfy.sh |

## Fichiers

- `checker.py` : vérificateur Python sans dépendance externe.
- `etat.json` : mémoire des offres et état de fonctionnement.
- `.github/workflows/veille.yml` : planification GitHub Actions.
