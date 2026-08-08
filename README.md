# Veille LinkedIn : offres et publications

Le robot vérifie à la fois :

- les offres officielles publiées dans LinkedIn Jobs ;
- les publications publiques d'entreprises annonçant un recrutement.

Chaque nouvelle détection déclenche une notification ntfy.sh cliquable. La
première exécution pose seulement l'état initial afin de ne pas notifier toutes
les anciennes offres et publications.

## Configuration

Variables utilisées par le programme :

| Variable | Contenu |
|---|---|
| `COMPANY_ID` | Identifiants LinkedIn Jobs : `id=Nom,id=Autre nom` |
| `COMPANY_POSTS` | Noms présents dans l'adresse LinkedIn : `vraiment-vraiment=Vraiment Vraiment` |
| `NTFY_TOPIC` | Nom du canal ntfy.sh |
| `SEUIL_ALERTE_ECHECS` | Facultatif, `6` par défaut |
| `POST_KEYWORDS` | Facultatif, mots personnalisés séparés par des virgules |

Il faut renseigner au moins `COMPANY_ID` ou `COMPANY_POSTS`. Pour surveiller les
deux sources, renseigner les deux.

Exemple Termux dans `config.env` :

```sh
export COMPANY_ID='18245467=Vraiment Vraiment'
export COMPANY_POSTS='vraiment-vraiment=Vraiment Vraiment'
export NTFY_TOPIC='votre-topic-secret'
export SEUIL_ALERTE_ECHECS='6'
```

La source des publications est la page publique française de l'entreprise. Le
robot ne se connecte pas à un compte LinkedIn et ne demande aucun mot de passe.
LinkedIn pouvant limiter les consultations répétées, un intervalle de dix
minutes est recommandé.

## Mémoire

`etat.json` conserve séparément les identifiants des offres et ceux des
publications. Une publication déjà analysée ne produit donc jamais deux alertes.

