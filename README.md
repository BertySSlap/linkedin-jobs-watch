# Veille LinkedIn : offres et publications

Le robot consulte les offres LinkedIn Jobs et, si elles sont configurées, les
publications publiques d'entreprises annonçant un recrutement. Les nouvelles
détections produisent des notifications ntfy.sh cliquables. La première lecture
d'une source initialise les éléments connus sans notifier ses anciennes offres.
Le robot utilise les pages publiques, sans connexion à un compte LinkedIn.

## Configuration

| Variable | Contenu |
|---|---|
| `COMPANY_ID` | Identifiants LinkedIn Jobs : `id=Nom,id=Autre nom` |
| `COMPANY_POSTS` | Facultatif : noms de pages, par exemple `vraiment-vraiment=Vraiment Vraiment` |
| `NTFY_TOPIC` | Canal ntfy.sh recevant les notifications |
| `INTERVALLE_CONTROLE_MINUTES` | Intervalle normal minimal entre deux contrôles, `10` par défaut |
| `SEUIL_ALERTE_ECHECS` | Seuil de tentatives bloquées avant alerte persistante, `6` par défaut |
| `FORCE_CHECK` | `true` pour ignorer l'intervalle normal ; ne contourne jamais une pause HTTP 429 |
| `POST_KEYWORDS` | Facultatif : mots de recrutement personnalisés, séparés par des virgules |

Au moins `COMPANY_ID` ou `COMPANY_POSTS` est nécessaire, ainsi que `NTFY_TOPIC`.
Dans GitHub Actions, renseigner ces valeurs dans les secrets du dépôt. Un secret
`COMPANY_POSTS` absent laisse la surveillance des publications désactivée. Les
valeurs de cadence et de seuil du workflow sont respectivement `10` et `6`.

Exemple local dans `config.env` :

```sh
export COMPANY_ID='18245467=Vraiment Vraiment'
export COMPANY_POSTS='vraiment-vraiment=Vraiment Vraiment'
export NTFY_TOPIC='votre-topic-secret'
export INTERVALLE_CONTROLE_MINUTES='10'
export SEUIL_ALERTE_ECHECS='6'
```

## Cadence et limites LinkedIn

La cadence dépend de l'heure du dernier contrôle enregistré dans `etat.json`,
pas du chiffre des minutes de l'horloge. Les lancements trop rapprochés sont
différés sans consulter LinkedIn.

En cas de réponse HTTP 429, la première pause est de 30 minutes. Les refus
successifs allongent la pause à 1 heure, 2 heures, 4 heures puis 6 heures au
maximum. Un délai `Retry-After` plus long annoncé par LinkedIn est respecté.
L'heure de reprise est conservée dans l'état : un nouveau lancement, même
forcé, respecte cette pause. Cette temporisation limite les requêtes répétées ;
elle ne garantit pas que LinkedIn acceptera la prochaine consultation.

Une limitation persistante déclenche une alerte ntfy après 6 tentatives
bloquées ou 24 heures depuis le premier refus, au prochain lancement. Le
workflow est alors signalé en échec une seule fois pour cet épisode. Les
lancements suivants continuent de montrer la limitation dans leur résumé, sans
renvoyer cette alerte. Un contrôle complet réussi termine l'épisode. Les
lancements simplement différés ne constituent pas de nouvelles tentatives
auprès de LinkedIn.

Si ntfy est indisponible lors de cette alerte, le signal GitHub n'est pas
répété à chaque lancement. L'envoi ntfy est retenté au plus tôt une heure
plus tard, jusqu'à sa réussite ou au rétablissement de la veille.

## Lire le résultat

Le résumé de chaque exécution GitHub Actions indique le résultat du programme
et, lorsqu'elle est connue, l'heure de la prochaine tentative.

| Statut | Signification |
|---|---|
| `succes` | Les sources configurées ont été contrôlées avec succès ; aucune nouveauté est un résultat normal. |
| `limite` | LinkedIn a répondu HTTP 429 ; une pause est enregistrée. |
| `differe` | Aucun contrôle des sources n'a été lancé car la cadence ou une pause n'autorise pas encore la tentative. |
| `echec` | Une autre erreur a empêché le contrôle de se terminer correctement. |

Un workflow vert accompagné de `limite` ou `differe` ne signifie donc pas
qu'un contrôle a réussi. La date du dernier succès n'avance qu'après un
contrôle complet réussi. Une erreur autre qu'une limitation HTTP 429 reste
signalée en échec ; l'alerte de limitation persistante décrite ci-dessus est
également signalée en échec lors de son émission.

## Exécution et mémoire

Le workflow `veille-offres` reste déclenché par `workflow_dispatch`, via le
service externe existant ou manuellement. Aucun nouveau calendrier de lancement
n'est ajouté. L'option manuelle `force` ignore seulement l'intervalle normal.

Les exécutions du workflow sont sérialisées. Chacune charge le dernier état de
`main`, puis sauvegarde `etat.json`, même après une erreur du contrôle, si le
chargement du dépôt a réussi. Les modifications concurrentes de l'état sont
fusionnées avant une nouvelle tentative de sauvegarde.

L'état conserve les offres et publications déjà connues, les horaires des
contrôles et les informations de reprise après une limitation. Ne pas le
supprimer pour résoudre un HTTP 429 : la pause et la mémoire des détections
seraient perdues.

## Tests

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Les tests utilisent la bibliothèque standard Python et des réponses simulées,
sans consulter LinkedIn ni envoyer de notification. Le workflow `tests`
s'exécute sur les modifications du code, des tests et des workflows concernés,
ainsi que sur demande manuelle. Il n'utilise aucun secret et n'exécute pas une
veille réelle.
