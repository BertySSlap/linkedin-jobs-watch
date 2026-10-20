#!/usr/bin/env python3
"""Fusionne sans perte deux états produits par des exécutions concurrentes."""
import copy
import datetime
import json
import sys


CHAMPS_EXECUTION = (
    "prochain_controle", "derniere_tentative", "dernier_statut", "limitation", "echecs",
)
CHAMPS_CONTROLE = CHAMPS_EXECUTION[:-1]
UTC = datetime.timezone.utc


def instant(valeur):
    """Lit les dates ISO anciennes et nouvelles dans un même fuseau."""
    if not isinstance(valeur, str) or not valeur.strip():
        return None
    try:
        date = datetime.datetime.fromisoformat(valeur.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    # Les anciennes dates sans fuseau étaient écrites par le processus UTC.
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    return date.astimezone(UTC)


def limite(etat):
    valeur = etat.get("limitation")
    return valeur if isinstance(valeur, dict) else {}


def derniere_observation(etat):
    dates = [
        instant(etat.get("derniere_tentative")),
        instant(limite(etat).get("derniere")),
    ]
    valides = [date for date in dates if date is not None]
    if valides:
        return max(valides)
    return instant(etat.get("derniere_verification_reussie"))


def prochain_controle(etat):
    dates = [
        instant(etat.get("prochain_controle")),
        instant(limite(etat).get("reprise_apres")),
    ]
    return max((date for date in dates if date is not None), default=None)


def choix_execution(distant, local):
    """Choisit l'observation réelle la plus récente, jamais l'ordre du push."""
    distant_controle = any(champ in distant for champ in CHAMPS_CONTROLE)
    local_controle = any(champ in local for champ in CHAMPS_CONTROLE)
    if distant_controle != local_controle:
        # Un ancien script ignore la limitation : son état ne peut pas l'effacer.
        return (distant, local) if distant_controle else (local, distant)

    minimum = datetime.datetime.min.replace(tzinfo=UTC)
    distant_date = derniere_observation(distant) or minimum
    local_date = derniere_observation(local) or minimum
    if distant_date != local_date:
        return (distant, local) if distant_date > local_date else (local, distant)

    distant_reprise = prochain_controle(distant) or minimum
    local_reprise = prochain_controle(local) or minimum
    if distant_reprise != local_reprise:
        return (distant, local) if distant_reprise > local_reprise else (local, distant)
    if bool(limite(distant)) != bool(limite(local)):
        return (distant, local) if limite(distant) else (local, distant)
    return local, distant


def cle_identifiant(valeur):
    texte = str(valeur)
    return (0, int(texte), texte) if texte.isdigit() else (1, 0, texte)


def fusionne(distant, local):
    fusion = copy.deepcopy(distant)
    fusion.update(copy.deepcopy(local))
    for champ in ("ids", "init", "post_ids", "posts_init"):
        valeurs = set(distant.get(champ, [])) | set(local.get(champ, []))
        fusion[champ] = sorted(valeurs, key=cle_identifiant)

    succes = [
        instant(distant.get("derniere_verification_reussie")),
        instant(local.get("derniere_verification_reussie")),
    ]
    dernier_succes = max((date for date in succes if date is not None), default=None)
    fusion["derniere_verification_reussie"] = dernier_succes.isoformat() if dernier_succes else ""

    recent, ancien = choix_execution(distant, local)
    for champ in CHAMPS_EXECUTION:
        # Une valeur explicite vide, notamment limitation={}, reste significative.
        if champ in recent:
            fusion[champ] = copy.deepcopy(recent[champ])
        elif champ in ancien:
            fusion[champ] = copy.deepcopy(ancien[champ])

    if derniere_observation(distant) == derniere_observation(local) and limite(distant) and limite(local):
        # Deux sauvegardes de la même observation peuvent différer après envoi
        # d'une alerte. Conserver le reçu évite de la notifier à nouveau.
        limitation = fusion["limitation"]
        limitation["alerte_envoyee"] = bool(
            limite(distant).get("alerte_envoyee") or limite(local).get("alerte_envoyee")
        )
        limitation["alerte_github_signalee"] = bool(
            limite(distant).get("alerte_github_signalee")
            or limite(local).get("alerte_github_signalee")
        )
        reessais = [
            instant(limite(etat).get("prochaine_tentative_alerte"))
            for etat in (distant, local)
        ]
        prochain_reessai = max((date for date in reessais if date is not None), default=None)
        if prochain_reessai is not None and not limitation["alerte_envoyee"]:
            limitation["prochaine_tentative_alerte"] = prochain_reessai.isoformat()
        tentatives = [limite(etat).get("tentatives", 0) for etat in (distant, local)]
        limitation["tentatives"] = max(
            (valeur for valeur in tentatives if isinstance(valeur, int)), default=0,
        )

    if limite(fusion).get("alerte_envoyee"):
        fusion["limitation"].pop("prochaine_tentative_alerte", None)

    # L'échéance du blocage reste un minimum, y compris pour les états partiels.
    reprise = prochain_controle(fusion)
    if reprise is not None:
        fusion["prochain_controle"] = reprise.isoformat()
    tentative = instant(fusion.get("derniere_tentative"))
    if tentative is not None:
        fusion["derniere_tentative"] = tentative.isoformat()
    for champ in ("depuis", "derniere", "reprise_apres", "prochaine_tentative_alerte"):
        date = instant(limite(fusion).get(champ))
        if date is not None:
            fusion["limitation"][champ] = date.isoformat()
    return fusion


def charge(chemin):
    with open(chemin, encoding="utf-8") as fichier:
        return json.load(fichier)


def main():
    distant_path, local_path = sys.argv[1:3]
    distant = charge(distant_path)
    local = charge(local_path)

    fusion = fusionne(distant, local)

    with open(distant_path, "w", encoding="utf-8") as fichier:
        json.dump(fusion, fichier, indent=2, sort_keys=True, ensure_ascii=False)
        fichier.write("\n")


if __name__ == "__main__":
    main()

