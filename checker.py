#!/usr/bin/env python3
"""Surveille LinkedIn Jobs et les publications de pages d'entreprises."""
import datetime
import html as htmllib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

STATE_FILE = "etat.json"
UA = (
    "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)
SEUIL_ALERTE_ECHECS = int(os.environ.get("SEUIL_ALERTE_ECHECS", "6"))
MOTS_RECRUTEMENT = tuple(
    mot.strip().lower()
    for mot in os.environ.get(
        "POST_KEYWORDS",
        "stage,stagiaire,recrutement,recrute,recrutons,alternance,alternant,"
        "apprentissage,apprenti,emploi,cdi,cdd,freelance,rejoignez",
    ).split(",")
    if mot.strip()
)


def erreur(message, titre="Veille LinkedIn"):
    propre = str(message).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::error title={titre}::{propre}")


def http_get(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
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


def parse_liste(brut):
    elements = []
    for morceau in brut.split(","):
        morceau = morceau.strip()
        if morceau:
            identifiant, _, label = morceau.partition("=")
            elements.append((identifiant.strip(), label.strip()))
    return elements


def texte_html(fragment):
    fragment = re.sub(r"<(script|style)\b[\s\S]*?</\1>", " ", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", htmllib.unescape(fragment)).strip()


def sans_accents(texte):
    return "".join(
        caractere for caractere in unicodedata.normalize("NFD", texte.lower())
        if unicodedata.category(caractere) != "Mn"
    )


def est_recrutement(texte):
    normalise = sans_accents(texte)
    return any(
        re.search(r"(?<!\w)" + re.escape(sans_accents(mot)) + r"(?!\w)", normalise)
        for mot in MOTS_RECRUTEMENT
    )


def parse_offres(page):
    offres = {}
    for match in re.finditer(
        r'data-entity-urn="urn:li:jobPosting:(\d+)"[\s\S]*?'
        r'base-search-card__title[^>]*>\s*([\s\S]*?)\s*</',
        page,
    ):
        offres.setdefault(match.group(1), texte_html(match.group(2)))
    return offres


def parse_publications(page):
    """Retourne {id: (texte, lien)} depuis la page publique d'une entreprise."""
    publications = {}
    # La page publique contient les publications sous forme de données JSON-LD.
    # Cette source est plus stable que les classes CSS de la page.
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>',
        page,
        flags=re.I,
    ):
        try:
            donnees = json.loads(htmllib.unescape(match.group(1)))
        except (json.JSONDecodeError, TypeError):
            continue
        pile = donnees if isinstance(donnees, list) else [donnees]
        while pile:
            element = pile.pop()
            if isinstance(element, list):
                pile.extend(element)
                continue
            if not isinstance(element, dict):
                continue
            pile.extend(element.values())
            if element.get("@type") != "DiscussionForumPosting":
                continue
            lien = str(element.get("url") or element.get("mainEntityOfPage") or "")
            identifiant = re.search(r"activity[-:](\d+)", lien)
            if not identifiant:
                continue
            texte = str(element.get("text") or element.get("headline") or "").strip()
            publications.setdefault(identifiant.group(1), (texte, lien))
    if publications:
        return publications

    # Repli si LinkedIn modifie ou retire temporairement les données JSON-LD.
    motifs = (
        r'href=["\']([^"\']*linkedin\.com/posts/[^"\']*activity[-:](\d+)[^"\']*)["\']',
        r'urn:li:activity:(\d+)',
    )
    occurrences = []
    for match in re.finditer(motifs[0], page, flags=re.I):
        occurrences.append((match.start(), match.group(2), htmllib.unescape(match.group(1))))
    if not occurrences:
        for match in re.finditer(motifs[1], page, flags=re.I):
            occurrences.append((match.start(), match.group(1), ""))

    for position, identifiant, lien in occurrences:
        if identifiant in publications:
            continue
        debut = max(0, position - 2500)
        fin = min(len(page), position + 6500)
        texte = texte_html(page[debut:fin])
        if len(texte) > 1200:
            texte = texte[:1197].rstrip() + "..."
        if lien.startswith("/"):
            lien = "https://www.linkedin.com" + lien
        elif lien and not lien.startswith("http"):
            lien = "https://www.linkedin.com/" + lien.lstrip("/")
        if not lien:
            lien = "https://www.linkedin.com/feed/update/urn:li:activity:" + identifiant
        publications[identifiant] = (texte, lien)
    return publications


def verifier_jobs(societes, connus, init, topic):
    echec = False
    for rang, (cid, label) in enumerate(societes):
        if rang:
            time.sleep(3)
        url = (
            "https://www.linkedin.com/jobs-guest/jobs/api/"
            "seeMoreJobPostings/search?f_C=" + urllib.parse.quote(cid) + "&start=0"
        )
        try:
            page = http_get(url)
            offres = parse_offres(page)
        except Exception as exc:
            erreur(f"Jobs [{cid}] : {exc}", "Erreur LinkedIn")
            echec = True
            continue
        vide_legitime = re.fullmatch(r"\s*<!DOCTYPE html>\s*(<!---->)?\s*", page)
        if not offres and not vide_legitime:
            erreur(f"Jobs [{cid}] : réponse suspecte ({len(page)} caractères)", "Réponse LinkedIn anormale")
            echec = True
            continue
        premiere_fois = cid not in init
        neuves = 0
        for jid, titre in sorted(offres.items()):
            if jid in connus:
                continue
            connus.add(jid)
            neuves += 1
            if not premiere_fois:
                lien = "https://www.linkedin.com/jobs/view/" + jid
                corps = f"{label} : {titre}" if label else titre
                print(f"[{cid}] NOUVELLE OFFRE : {corps} -> {lien}")
                notify(topic, "Nouvelle offre LinkedIn !", corps, click=lien)
        if premiere_fois:
            init.add(cid)
            print(f"Jobs [{cid}] : initialisation de {len(offres)} offre(s), sans alerte.")
        elif not neuves:
            print(f"Jobs [{cid}] : RAS ({len(offres)} offre(s) en ligne).")
    return echec


def verifier_publications(pages, connus, init, topic):
    echec = False
    for rang, (slug, label) in enumerate(pages):
        if rang or pages:
            time.sleep(3)
        # Cette version publique contient les publications dans son JSON-LD,
        # contrairement à /posts qui demande souvent une connexion LinkedIn.
        url = f"https://fr.linkedin.com/company/{urllib.parse.quote(slug)}"
        try:
            page = http_get(url)
            publications = parse_publications(page)
        except Exception as exc:
            erreur(f"Publications [{slug}] : {exc}", "Erreur LinkedIn")
            echec = True
            continue
        if not publications:
            erreur(
                f"Publications [{slug}] : aucune publication lisible; connexion ou blocage possible",
                "Réponse LinkedIn anormale",
            )
            echec = True
            continue
        premiere_fois = slug not in init
        nouvelles_pertinentes = 0
        for pid, (texte, lien) in publications.items():
            if pid in connus:
                continue
            connus.add(pid)
            if premiere_fois or not est_recrutement(texte):
                continue
            nouvelles_pertinentes += 1
            extrait = texte[:350].strip()
            corps = f"{label} : {extrait}" if label else extrait
            print(f"Publications [{slug}] : RECRUTEMENT DÉTECTÉ -> {lien}")
            notify(topic, "Nouvelle publication de recrutement !", corps, click=lien)
        if premiere_fois:
            init.add(slug)
            pertinents = sum(est_recrutement(texte) for texte, _ in publications.values())
            print(
                f"Publications [{slug}] : initialisation de {len(publications)} publication(s), "
                f"dont {pertinents} liée(s) au recrutement, sans alerte."
            )
        elif not nouvelles_pertinentes:
            print(f"Publications [{slug}] : RAS ({len(publications)} publication(s) visible(s)).")
    return echec


def main():
    brut_jobs = os.environ.get("COMPANY_ID", "").strip()
    brut_posts = os.environ.get("COMPANY_POSTS", "").strip()
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic or (not brut_jobs and not brut_posts):
        erreur("NTFY_TOPIC et au moins COMPANY_ID ou COMPANY_POSTS sont requis", "Configuration manquante")
        return 1

    etat = {
        "ids": [], "init": [], "post_ids": [], "posts_init": [],
        "echecs": 0, "keepalive": "", "derniere_verification_reussie": "",
    }
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as fichier:
            etat.update(json.load(fichier))

    connus = set(etat.get("ids", []))
    init = set(etat.get("init", []))
    post_connus = set(etat.get("post_ids", []))
    posts_init = set(etat.get("posts_init", []))
    echec = verifier_jobs(parse_liste(brut_jobs), connus, init, topic) if brut_jobs else False
    if brut_posts:
        echec = verifier_publications(parse_liste(brut_posts), post_connus, posts_init, topic) or echec

    if echec:
        etat["echecs"] = int(etat.get("echecs", 0)) + 1
        erreur(f"{etat['echecs']} vérification(s) consécutive(s) avec erreur", "Échecs consécutifs")
        if etat["echecs"] == SEUIL_ALERTE_ECHECS:
            notify(topic, "Veille LinkedIn en panne", f"{etat['echecs']} vérifications consécutives ont échoué.", priority="default")
    else:
        etat["echecs"] = 0
        maintenant = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
        if etat.get("derniere_verification_reussie", "")[:10] != maintenant[:10]:
            etat["derniere_verification_reussie"] = maintenant

    etat["ids"] = sorted(connus, key=int)
    etat["init"] = sorted(init, key=int)
    etat["post_ids"] = sorted(post_connus, key=int)
    etat["posts_init"] = sorted(posts_init)
    mois = datetime.date.today().strftime("%Y-%m")
    if etat.get("keepalive") != mois:
        etat["keepalive"] = mois
    sauve(etat)
    return 1 if echec else 0


if __name__ == "__main__":
    sys.exit(main())

