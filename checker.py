#!/usr/bin/env python3
"""Surveille les offres d'emploi publiees par une ou plusieurs entreprises
sur LinkedIn (API publique invitee) et pousse une notification ntfy.sh
pour chaque nouvelle offre detectee.

Configuration par variables d'environnement (secrets GitHub Actions) :
  COMPANY_ID  - une ou plusieurs entreprises separees par des virgules,
                chacune sous la forme "id" ou "id=Nom affiche",
                ex. "12345=Ma Boite,67890=Autre Agence"
  NTFY_TOPIC  - canal ntfy.sh ou envoyer les notifications
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
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
# ~3 h d'echecs consecutifs (a 10 min d'intervalle) avant d'alerter
SEUIL_ALERTE_ECHECS = 18


def http_get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def notify(topic, title, body, click=None, priority="urgent"):
    headers = {"Title": title, "Priority": priority, "Tags": "briefcase"}
    if click:
        headers["Click"] = click
    req = urllib.request.Request(
        "https://ntfy.sh/" + topic, data=body.encode("utf-8"), headers=headers)
    urllib.request.urlopen(req, timeout=30).read()


def sauve(etat):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(etat, f, indent=2, sort_keys=True)
        f.write("\n")


def parse_offres(page):
    offres = {}
    for m in re.finditer(
            r'data-entity-urn="urn:li:jobPosting:(\d+)"[\s\S]*?'
            r'base-search-card__title[^>]*>\s*([\s\S]*?)\s*</', page):
        titre = htmllib.unescape(re.sub(r"\s+", " ", m.group(2)).strip())
        offres.setdefault(m.group(1), titre)
    return offres


def main():
    brut = os.environ.get("COMPANY_ID", "").strip()
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not brut or not topic:
        print("ERREUR : secrets COMPANY_ID / NTFY_TOPIC manquants")
        return 1

    societes = []
    for morceau in brut.split(","):
        morceau = morceau.strip()
        if morceau:
            cid, _, label = morceau.partition("=")
            societes.append((cid.strip(), label.strip()))

    etat = {"ids": [], "echecs": 0, "keepalive": "", "init": []}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            etat.update(json.load(f))

    connus = set(etat["ids"])
    echec = False

    for rang, (cid, label) in enumerate(societes):
        if rang:
            time.sleep(3)  # courtoisie entre deux requetes LinkedIn
        url = ("https://www.linkedin.com/jobs-guest/jobs/api/"
               "seeMoreJobPostings/search?f_C=" + cid + "&start=0")
        try:
            page = http_get(url)
        except Exception as e:
            print("[%s] Echec de la requete : %s" % (cid, e))
            echec = True
            continue

        offres = parse_offres(page)
        # reponse vide legitime de l'API = juste "<!DOCTYPE html>" + "<!---->"
        vide_legitime = re.fullmatch(r"\s*<!DOCTYPE html>\s*(<!---->)?\s*", page)
        if not offres and not vide_legitime:
            print("[%s] Reponse suspecte (%d caracteres) - possible blocage"
                  % (cid, len(page)))
            echec = True
            continue

        # premiere fois qu'on surveille cette entreprise :
        # on memorise ses offres actuelles sans alerter
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
            corps = ("%s : %s" % (label, titre)) if label else titre
            print("[%s] NOUVELLE OFFRE : %s -> %s" % (cid, corps, lien))
            notify(topic, "Nouvelle offre d'emploi !", corps, click=lien)
        if premiere_fois:
            etat["init"].append(cid)
            print("[%s] Initialisation : %d offre(s) en ligne, pas d'alerte."
                  % (cid, len(offres)))
        elif not neuves:
            print("[%s] RAS (%d offre(s) en ligne)." % (cid, len(offres)))

    if echec:
        etat["echecs"] += 1
        print("Echecs consecutifs :", etat["echecs"])
        if etat["echecs"] == SEUIL_ALERTE_ECHECS:
            notify(topic, "Veille LinkedIn en panne",
                   "%d verifications consecutives ont echoue. "
                   "Voir les logs GitHub Actions." % etat["echecs"],
                   priority="default")
    else:
        etat["echecs"] = 0

    # seuls les identifiants sont conserves (jamais les titres)
    etat["ids"] = sorted(connus, key=int)
    etat["init"] = sorted(set(etat["init"]), key=int)

    # un commit par mois minimum pour que GitHub ne desactive pas
    # le planning apres 60 jours sans activite
    mois = datetime.date.today().strftime("%Y-%m")
    if etat.get("keepalive") != mois:
        etat["keepalive"] = mois

    sauve(etat)
    return 0


if __name__ == "__main__":
    sys.exit(main())
