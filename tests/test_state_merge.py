"""Protection de l'historique et des échéances lors des sauvegardes concurrentes."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from fusionne_etat import fusionne


def succes(date, suivant):
    return {
        "derniere_tentative": date,
        "derniere_verification_reussie": date,
        "dernier_statut": "succes",
        "prochain_controle": suivant,
        "limitation": {},
        "echecs": 0,
    }


def limite(date, reprise, alerte=False):
    return {
        "derniere_tentative": date,
        "dernier_statut": "limite",
        "prochain_controle": reprise,
        "limitation": {
            "depuis": date,
            "derniere": date,
            "reprise_apres": reprise,
            "tentatives": 1,
            "alerte_envoyee": alerte,
        },
        "echecs": 2,
    }


class FusionEtatTest(unittest.TestCase):
    def test_push_ancien_necrase_pas_limitation_plus_recente(self):
        distant = limite("2026-10-20T14:00:00Z", "2026-10-21T14:00:00Z", True)
        distant.update(ids=["10"], init=["1"], post_ids=["30"], posts_init=["atelier"])
        local = succes("2026-10-20T12:00:00Z", "2026-10-20T18:00:00Z")
        local.update(ids=["2"], init=["2"], post_ids=["20"], posts_init=["studio"])
        resultat = fusionne(distant, local)
        self.assertEqual(resultat["dernier_statut"], "limite")
        self.assertEqual(resultat["prochain_controle"], "2026-10-21T14:00:00+00:00")
        self.assertTrue(resultat["limitation"]["alerte_envoyee"])
        self.assertEqual(resultat["echecs"], 2)
        self.assertEqual(resultat["ids"], ["2", "10"])
        self.assertEqual(resultat["init"], ["1", "2"])
        self.assertEqual(resultat["post_ids"], ["20", "30"])
        self.assertEqual(resultat["posts_init"], ["atelier", "studio"])
        self.assertEqual(resultat, fusionne(local, distant))

    def test_succes_reel_plus_recent_reinitialise_limitation(self):
        distant = limite("2026-10-20T14:00:00Z", "2026-10-23T14:00:00Z", True)
        local = succes("2026-10-21T15:00:00Z", "2026-10-21T21:00:00Z")
        resultat = fusionne(distant, local)
        self.assertEqual(resultat["limitation"], {})
        self.assertEqual(resultat["dernier_statut"], "succes")
        self.assertEqual(resultat["prochain_controle"], "2026-10-21T21:00:00+00:00")
        self.assertEqual(resultat["echecs"], 0)

    def test_comparaison_des_dates_avec_fuseaux(self):
        distant = limite("2026-10-20T10:30:00Z", "2026-10-21T10:30:00Z")
        distant["derniere_verification_reussie"] = "2026-10-19T10:30:00Z"
        local = succes("2026-10-20T12:00:00+02:00", "2026-10-20T18:00:00+02:00")
        local["derniere_verification_reussie"] = "2026-10-19T12:00:00+02:00"
        resultat = fusionne(distant, local)
        self.assertEqual(resultat["dernier_statut"], "limite")
        self.assertEqual(resultat["derniere_verification_reussie"], "2026-10-19T10:30:00+00:00")

    def test_observation_limitation_peut_etre_plus_recente_que_tentative(self):
        distant = limite("2026-10-20T14:00:00Z", "2026-10-21T14:00:00Z")
        distant["derniere_tentative"] = "2026-10-20T10:00:00Z"
        local = succes("2026-10-20T12:00:00Z", "2026-10-20T18:00:00Z")
        self.assertEqual(fusionne(distant, local)["dernier_statut"], "limite")

    def test_execution_differee_ne_rajeunie_pas_observation(self):
        distant = succes("2026-10-21T15:00:00Z", "2026-10-21T21:00:00Z")
        local = limite("2026-10-20T14:00:00Z", "2026-10-23T14:00:00Z")
        local["dernier_statut"] = "differe"
        self.assertEqual(fusionne(distant, local)["limitation"], {})

    def test_egalite_prefere_reprise_maximale_et_preserve_alerte(self):
        distant = limite("2026-10-20T14:00:00Z", "2026-10-22T14:00:00Z")
        local = limite("2026-10-20T16:00:00+02:00", "2026-10-21T14:00:00Z", True)
        local["limitation"]["tentatives"] = 3
        resultat = fusionne(distant, local)
        self.assertEqual(resultat["prochain_controle"], "2026-10-22T14:00:00+00:00")
        self.assertTrue(resultat["limitation"]["alerte_envoyee"])
        self.assertEqual(resultat["limitation"]["tentatives"], 3)

    def test_limitation_reprise_apres_protege_contre_echeance_incoherente(self):
        distant = limite("2026-10-20T14:00:00Z", "2026-10-21T14:00:00Z")
        distant["prochain_controle"] = "2026-10-20T16:00:00Z"
        resultat = fusionne(distant, {})
        self.assertEqual(resultat["prochain_controle"], "2026-10-21T14:00:00+00:00")

    def test_egalite_preserve_signal_github_et_echeance_alerte_ntfy(self):
        for envoyee in (False, True):
            distant = limite("2026-10-20T14:00:00Z", "2026-10-22T14:00:00Z")
            distant["limitation"].update(
                alerte_github_signalee=False,
                prochaine_tentative_alerte="2026-10-20T14:30:00Z",
            )
            local = limite("2026-10-20T16:00:00+02:00", "2026-10-21T14:00:00Z", envoyee)
            local["limitation"].update(
                alerte_github_signalee=True,
                prochaine_tentative_alerte="2026-10-20T16:45:00+02:00",
            )
            for premier, second in ((distant, local), (local, distant)):
                with self.subTest(envoyee=envoyee, distant_en_premier=premier is distant):
                    limitation = fusionne(premier, second)["limitation"]
                    self.assertTrue(limitation["alerte_github_signalee"])
                    self.assertEqual(limitation["alerte_envoyee"], envoyee)
                    if envoyee:
                        self.assertNotIn("prochaine_tentative_alerte", limitation)
                    else:
                        self.assertEqual(
                            limitation["prochaine_tentative_alerte"], "2026-10-20T14:45:00+00:00",
                        )

    def test_etat_legacy_ne_peut_pas_effacer_un_cooldown(self):
        distant = limite("2026-10-20T14:00:00Z", "2026-10-23T14:00:00Z")
        distant.update(post_ids=["42"], posts_init=["atelier"])
        legacy = {
            "ids": ["9"], "init": ["5"], "echecs": 0,
            "derniere_verification_reussie": "2026-10-21T12:00:00Z",
        }
        resultat = fusionne(distant, legacy)
        self.assertEqual(resultat["dernier_statut"], "limite")
        self.assertEqual(resultat["prochain_controle"], "2026-10-23T14:00:00+00:00")
        self.assertEqual(resultat["post_ids"], ["42"])
        self.assertEqual(resultat["posts_init"], ["atelier"])
        self.assertEqual(resultat["derniere_verification_reussie"], "2026-10-21T12:00:00+00:00")

    def test_deux_etats_legacy_conservent_historique_et_cles_inconnues(self):
        distant = {
            "ids": ["10"], "post_ids": ["31"], "posts_init": ["a"],
            "derniere_verification_reussie": "2026-10-20T12:00:00Z",
            "echecs": 0, "distant_seulement": {"a": 1},
        }
        local = {
            "ids": ["2"], "init": ["3"], "post_ids": ["32"], "posts_init": ["b"],
            "derniere_verification_reussie": "2026-10-19T12:00:00Z",
            "echecs": 5, "local_seulement": "conserver", "keepalive": "2026-10",
        }
        resultat = fusionne(distant, local)
        self.assertEqual(resultat["post_ids"], ["31", "32"])
        self.assertEqual(resultat["posts_init"], ["a", "b"])
        self.assertEqual(resultat["distant_seulement"], {"a": 1})
        self.assertEqual(resultat["local_seulement"], "conserver")
        self.assertEqual(resultat["keepalive"], "2026-10")
        self.assertEqual(resultat["echecs"], 0)

    def test_dates_invalides_ignorees_et_anciennes_dates_utc(self):
        resultat = fusionne(
            {"derniere_verification_reussie": "invalide"},
            {"derniere_verification_reussie": "2026-10-20T12:00:00"},
        )
        self.assertEqual(resultat["derniere_verification_reussie"], "2026-10-20T12:00:00+00:00")
        self.assertEqual(fusionne({}, {})["derniere_verification_reussie"], "")

    def test_fusion_ne_modifie_pas_les_etats_entree(self):
        distant = limite("2026-10-20T14:00:00Z", "2026-10-22T14:00:00Z")
        local = limite("2026-10-20T14:00:00Z", "2026-10-21T14:00:00Z", True)
        originals = copy.deepcopy((distant, local))
        fusionne(distant, local)
        self.assertEqual((distant, local), originals)

    def test_commande_ecrit_uniquement_le_fichier_distant(self):
        with tempfile.TemporaryDirectory() as dossier:
            distant = Path(dossier) / "distant.json"
            local = Path(dossier) / "local.json"
            distant.write_text(json.dumps({"ids": ["1"], "post_ids": ["10"]}), encoding="utf-8")
            contenu_local = json.dumps({"ids": ["2"], "post_ids": ["20"]})
            local.write_text(contenu_local, encoding="utf-8")
            script = Path(__file__).resolve().parents[1] / "fusionne_etat.py"
            subprocess.run([sys.executable, str(script), str(distant), str(local)], check=True)
            self.assertEqual(json.loads(distant.read_text(encoding="utf-8"))["post_ids"], ["10", "20"])
            self.assertEqual(local.read_text(encoding="utf-8"), contenu_local)


if __name__ == "__main__":
    unittest.main()
