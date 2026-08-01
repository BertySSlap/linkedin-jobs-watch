#!/usr/bin/env python3
"""Fusionne sans perte deux états produits par des exécutions concurrentes."""
import json
import sys


def charge(chemin):
    with open(chemin, encoding="utf-8") as fichier:
        return json.load(fichier)


def main():
    distant_path, local_path = sys.argv[1:3]
    distant = charge(distant_path)
    local = charge(local_path)

    fusion = dict(distant)
    fusion.update(local)
    fusion["ids"] = sorted(
        set(distant.get("ids", [])) | set(local.get("ids", [])), key=int
    )
    fusion["init"] = sorted(
        set(distant.get("init", [])) | set(local.get("init", [])), key=int
    )
    fusion["derniere_verification_reussie"] = max(
        distant.get("derniere_verification_reussie", ""),
        local.get("derniere_verification_reussie", ""),
    )

    with open(distant_path, "w", encoding="utf-8") as fichier:
        json.dump(fusion, fichier, indent=2, sort_keys=True, ensure_ascii=False)
        fichier.write("\n")


if __name__ == "__main__":
    main()
