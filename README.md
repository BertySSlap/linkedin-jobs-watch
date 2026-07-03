# Veille d'offres d'emploi LinkedIn

Vérifie toutes les 10 minutes (GitHub Actions) si l'entreprise surveillée a
publié de nouvelles offres d'emploi sur LinkedIn, via l'API publique invitée.
Chaque nouvelle offre déclenche une notification push [ntfy.sh](https://ntfy.sh)
(cliquable, ouvre directement l'offre).

## Configuration

Deux secrets à définir dans *Settings → Secrets and variables → Actions* :

| Secret       | Contenu                                                |
|--------------|--------------------------------------------------------|
| `COMPANY_ID` | identifiant numérique LinkedIn de l'entreprise         |
| `NTFY_TOPIC` | nom du canal ntfy.sh où pousser les notifications      |

L'identifiant numérique d'une entreprise se trouve dans le HTML de sa page
LinkedIn publique : `data-semaphore-content-urn="urn:li:organization:<id>"`.

## Fichiers

- `checker.py` — le vérificateur (Python, sans dépendance externe)
- `etat.json` — mémoire des offres déjà vues (identifiants seulement),
  compteur d'échecs consécutifs, et jeton mensuel d'activité (évite la
  désactivation automatique du planning par GitHub après 60 jours)
- `.github/workflows/veille.yml` — planification toutes les 10 minutes

## Notes

- Aucune alerte au tout premier passage (initialisation de l'état).
- Une réponse LinkedIn anormale incrémente le compteur d'échecs ; une alerte
  « en panne » n'est envoyée qu'après ~3 h d'échecs consécutifs.
