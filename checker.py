#!/usr/bin/env python3
"""Surveille les offres d'emploi publiées par une ou plusieurs entreprises
sur LinkedIn (API publique invitée) et pousse une notification ntfy.sh
pour chaque nouvelle offre détectée.

Configuration par variables d'environnement (secrets GitHub Actions) :
  COMPANY_ID  - une ou plusieurs entreprises séparées par des virgules,
                chacune sous la forme "id" ou "id=Nom affiché",
                ex. "12345=Ma Boite,67890=Autre Agence"
  NTFY_TOPIC  - canal ntfy.sh où envoyer les notifications
"""
import datetime
import html as htmllib
import json
import os
import re
import sys
import time
import urllib.request

STATE_FILE = "etat.json"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# Environ 3 h d'échecs consécutifs à 10 min d'intervalle.
SEUIL_ALERTE_ECHECS = 18


def erreur(message, titre="Veille LinkedIn"):
    """Affiche une annotation rouge dans les journaux GitHub Actions."""
    propre = str(message).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::error title={titre}::{propre}")


def http_get(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def notify(topic, title, body, click=None, priority="urgent"):
    headers = {"Title": title, "Priority": priority, "Tags": "briefcase"}
    if click:
        headers["Click"] = click
    req = urllib.request.Request(
        "https://ntfy.sh/" + topic,
        data=body.encode("utf-8"),
        headers=headers,
    )
    urllib.request.urlopen(req, timeout=30).read()


def sauve(etat):
    with open(STATE_FILE, "w", encoding="utf-8") as fichier:
        json.dump(etat, fichier, indent=2, sort_keys=True, ensure_ascii=False)
        fichier.write("\n")


def parse_offres(page):
    offres = {}
    for match in re.finditer(
        r'data-entity-urn="urn:li:jobPosting:(\d+)"[\s\S]*?'
        r'base-search-card__title[^>]*>\s*([\s\S]*?)\s*</',
        page,
    ):
        titre = htmllib.unescape(re.sub(r"\s+", " ", match.group(2)).strip())
        offres.setdefault(match.group(1), titre)
    return offres


def main():
    brut = os.environ.get("COMPANY_ID", "").strip()
    topic = os.environ.get("NTFY_TOPIC", "").strip()

    if not brut or not topic:
        erreur("Secrets COMPANY_ID / NTFY_TOPIC manquants", "Configuration manquante")
        return 1

    societes = []
    for morceau in brut.split(","):
        morceau = morceau.strip()
        if morceau:
            cid, _, label = morceau.partition("=")
            societes.append((cid.strip(), label.strip()))

    etat = {
        "ids": [],
        "echecs": 0,
        "keepalive": "",
        "init": [],
        "derniere_verification_reussie": "",
    }
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as fichier:
            etat.update(json.load(fichier))

    connus = set(etat["ids"])
    echec = False

    for rang, (cid, label) in enumerate(societes):
        if rang:
            time.sleep(3)

        # Aucun filtre de contrat n'est appliqué : CDI, CDD, stage,
        # alternance, apprentissage et autres offres sont inclus.
        url = (
            "https://www.linkedin.com/jobs-guest/jobs/api/"
            "seeMoreJobPostings/search?f_C=" + cid + "&start=0"
        )

        try:
            page = http_get(url)
        except Exception as exc:
            erreur(f"[{cid}] Échec de la requête : {exc}", "Erreur LinkedIn")
            echec = True
            continue

        offres = parse_offres(page)
        vide_legitime = re.fullmatch(r"\s*<!DOCTYPE html>\s*(<!---->)?\s*", page)

        if not offres and not vide_legitime:
            erreur(
                f"[{cid}] Réponse suspecte ({len(page)} caractères) — blocage possible",
                "Réponse LinkedIn anormale",
            )
            echec = True
            continue

        premiere_fois = cid not in etat["init"]
        neuves = 0

        for jid, titre in sorted(offres.items()):
            if jid in connus:
                continue

            connus.add(jid)
            neuves += 1

            if premiere_fois:
                continue

            lien = "https://www.linkedin.com/jobs/view/" + jid
            corps = f"{label} : {titre}" if label else titre
            print(f"[{cid}] NOUVELLE OFFRE : {corps} -> {lien}")
            notify(topic, "Nouvelle offre d'emploi !", corps, click=lien)

        if premiere_fois:
            etat["init"].append(cid)
            print(f"[{cid}] Initialisation : {len(offres)} offre(s), sans alerte.")
        elif not neuves:
            print(f"[{cid}] RAS ({len(offres)} offre(s) en ligne).")

    if echec:
        etat["echecs"] += 1
        erreur(
            f"{etat['echecs']} vérification(s) consécutive(s) avec erreur",
            "Échecs consécutifs",
        )
        if etat["echecs"] == SEUIL_ALERTE_ECHECS:
            notify(
                topic,
                "Veille LinkedIn en panne",
                f"{etat['echecs']} vérifications consécutives ont échoué. "
                "Voir les journaux GitHub Actions.",
                priority="default",
            )
    else:
        etat["echecs"] = 0
        maintenant = (
            datetime.datetime.now(datetime.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        )
        # Une trace quotidienne suffit. Écrire l'heure à chaque passage
        # créait un commit à chaque contrôle et des conflits Git.
        if etat.get("derniere_verification_reussie", "")[:10] != maintenant[:10]:
            etat["derniere_verification_reussie"] = maintenant

    etat["ids"] = sorted(connus, key=int)
    etat["init"] = sorted(set(etat["init"]), key=int)

    mois = datetime.date.today().strftime("%Y-%m")
    if etat.get("keepalive") != mois:
        etat["keepalive"] = mois

    sauve(etat)
    return 1 if echec else 0


if __name__ == "__main__":
    sys.exit(main())
