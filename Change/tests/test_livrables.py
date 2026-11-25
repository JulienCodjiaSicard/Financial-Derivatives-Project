"""Tests de l'étape 9 : run_all.py de bout en bout et A_LIRE.md."""

from __future__ import annotations

import csv
import importlib.util
import json
import re
from pathlib import Path

import pytest

from fx.config import lire_json
from fx.rapport import nombre, pct

RACINE = Path(__file__).resolve().parent.parent
FICHIERS = (
    "coupons_fx.csv", "coupons_fx_probabilites.svg", "sensibilites_fx.csv", "export_mc.json",
    "historique_barriere_fx.csv", "historique_barriere_fx.svg", "vol_historique_vs_implicite_fx.csv", "A_LIRE.md",
)


def charger_run_all():
    spec = importlib.util.spec_from_file_location("run_all", RACINE / "scripts" / "run_all.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sortie(tmp_path_factory):
    """Exécute run_all avec une copie de la config qui écrit dans un dossier temporaire."""
    tmp = tmp_path_factory.mktemp("valo")
    brut = lire_json(RACINE / "config" / "valo_2026-11-13.json")  # config par défaut de run_all
    for cle in ("fixings_bce", "marche", "historique_bce"):
        brut["chemins"][cle] = str(RACINE / brut["chemins"][cle])
    brut["chemins"]["resultats"] = str(tmp / "resultats_fx")
    # Fichier de marché recopié avec des chemins de courbes absolus (ils sont relatifs à la racine du projet)
    marche = lire_json(Path(brut["chemins"]["marche"]))
    for dev in ("EUR", "USD"):
        if marche["courbes"][dev].get("fichier"):
            marche["courbes"][dev]["fichier"] = str((RACINE / marche["courbes"][dev]["fichier"]).resolve())
    (tmp / "marche.json").write_text(json.dumps(marche, ensure_ascii=False), encoding="utf-8")
    brut["chemins"]["marche"] = str(tmp / "marche.json")
    (tmp / "config").mkdir()
    chemin = tmp / "config" / "valo_test.json"
    chemin.write_text(json.dumps(brut, ensure_ascii=False), encoding="utf-8")
    code = charger_run_all().main(["run_all.py", str(chemin)])
    assert code == 0
    return tmp / "resultats_fx"


def test_format_francais():
    assert nombre(21678708.6) == "21 678 709"
    assert nombre(1.177, 4) == "1,1770"
    assert pct(0.84389) == "84,39 %"


def test_run_all_produit_tous_les_livrables(sortie):
    for f in FICHIERS:
        assert (sortie / f).exists(), f
    assert sorted(p.name for p in sortie.iterdir()) == sorted(FICHIERS)


def test_a_lire_coherent_avec_les_csv(sortie):
    texte = (sortie / "A_LIRE.md").read_text(encoding="utf-8")
    with (sortie / "coupons_fx.csv").open(encoding="utf-8-sig") as f:
        lignes = list(csv.reader(f, delimiter=";"))
    total_smile = float(lignes[-1][-1])
    assert nombre(total_smile) in texte
    with (sortie / "sensibilites_fx.csv").open(encoding="utf-8-sig") as f:
        sens = list(csv.reader(f, delimiter=";"))
    total_delta = float(dict(zip(sens[0], sens[-1]))["delta_spot_+1pct_EUR"])
    assert nombre(total_delta) in texte
    export = json.loads((sortie / "export_mc.json").read_text(encoding="utf-8"))
    assert nombre(export["dates_coupon"][-1]["barriere_effective_a_vol_atm"], 4) in texte


def test_a_lire_contenu(sortie):
    texte = (sortie / "A_LIRE.md").read_text(encoding="utf-8")
    for section in ("## En bref", "## Données et sources", "## Hypothèses", "## Les 14 coupons FX",
                    "## Sensibilités", "## Paramètres pour le moteur", "## Vue de l'investisseur",
                    "## Limites", "## Questions ouvertes"):
        assert section in texte, section
    for mot in ("K = 1,1770", "07/05/2026", "coupon_par_periode", "bf25_manquant", "vols_au_dela_dernier_pilier",
                "plus haut intrajournalier", "cross-currency", "corrélation taux/change", "Vanna-Volga",
                "fixing BCE du 19/11/2026", "12 dates sur 14", "sous-groupe taux", "Courbes sans OIS", "13/11/2026"):
        assert mot in texte, mot
    assert len(re.findall(r"^\| \d+ \| \d{2}/\d{2}/\d{4} \|", texte, flags=re.M)) == 14
    assert "nan" not in texte.lower().replace("nanc", "")
