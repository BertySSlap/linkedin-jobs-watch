#!/usr/bin/env python3
"""Surveille les offres d'emploi publiees par une entreprise sur LinkedIn
(API publique invitee) et pousse une notification ntfy.sh pour chaque
nouvelle offre detectee.

Configuration par variables d'environnement (secrets GitHub Actions) :
  COMPANY_ID  - identifiant numerique LinkedIn de l'entreprise surveillee
  NTFY_TOPIC  - canal ntfy.sh ou envoyer les notifications
"""
import datetime
import html as htmllib
import json
import os
import re
import sys
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


def main():
    company = os.environ.get("COMPANY_ID", "").strip()
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not company or not topic:
        print("ERREUR : secrets COMPANY_ID / NTFY_TOPIC manquants")
        return 1

    premier = not os.path.exists(STATE_FILE)
    etat = {"ids": [], "echecs": 0, "keepalive": ""}
    if not premier:
        with open(STATE_FILE, encoding="utf-8") as f:
            etat.update(json.load(f))

    url = ("https://www.linkedin.com/jobs-guest/jobs/api/"
           "seeMoreJobPostings/search?f_C=" + company + "&start=0")
    try:
        page = http_get(url)
    except Exception as e:
        page = None
        print("Echec de la requete :", e)

    offres = {}
    if page is not None:
        for m in re.finditer(
                r'data-entity-urn="urn:li:jobPosting:(\d+)"[\s\S]*?'
                r'base-search-card__title[^>]*>\s*([\s\S]*?)\s*</', page):
            titre = htmllib.unescape(re.sub(r"\s+", " ", m.group(2)).strip())
            offres.setdefault(m.group(1), titre)
        # reponse vide legitime de l'API = juste "<!DOCTYPE html>" + "<!---->"
        vide_legitime = re.fullmatch(r"\s*<!DOCTYPE html>\s*(<!---->)?\s*", page)
        if not offres and not vide_legitime:
            print("Reponse suspecte (%d caracteres) - possible blocage" % len(page))
            page = None

    if page is None:
        etat["echecs"] += 1
        print("Echecs consecutifs :", etat["echecs"])
        if etat["echecs"] == SEUIL_ALERTE_ECHECS:
            notify(topic, "Veille LinkedIn en panne",
                   "%d verifications consecutives ont echoue. "
                   "Voir les logs GitHub Actions." % etat["echecs"],
                   priority="default")
        sauve(etat)
        return 0

    etat["echecs"] = 0
    connus = set(etat["ids"])
    nouvelles = [(i, t) for i, t in sorted(offres.items()) if i not in connus]

    if premier:
        print("Initialisation : %d offre(s) en ligne, pas d'alerte." % len(offres))
    elif nouvelles:
        for jid, titre in nouvelles:
            lien = "https://www.linkedin.com/jobs/view/" + jid
            print("NOUVELLE OFFRE :", titre, "->", lien)
            notify(topic, "Nouvelle offre d'emploi !", titre, click=lien)
    else:
        print("RAS (%d offre(s) en ligne)." % len(offres))

    # seuls les identifiants sont conserves (jamais les titres)
    etat["ids"] = sorted(connus | set(offres), key=int)

    # un commit par mois minimum pour que GitHub ne desactive pas
    # le planning apres 60 jours sans activite
    mois = datetime.date.today().strftime("%Y-%m")
    if etat.get("keepalive") != mois:
        etat["keepalive"] = mois

    sauve(etat)
    return 0


if __name__ == "__main__":
    sys.exit(main())
