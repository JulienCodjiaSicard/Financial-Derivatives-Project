"""Tests de l'étape 1 : config, données de marché, barrière K."""

from __future__ import annotations

import copy
import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from fx.config import (
    DonneesManquantes,
    ErreurConfig,
    ajouter_mois,
    calcul_barriere,
    charger_config,
    charger_contexte,
    config_depuis_dict,
    echeancier_coupons,
    fixing_a_date,
    fraction_annee,
    lire_fixings_bce,
    lire_json,
    valider_marche,
    verifier_coherence,
)

RACINE = Path(__file__).resolve().parent.parent
CHEMIN_CONFIG = RACINE / "config" / "valo_2026-11-06.json"

# Courbes fictives, utilisées UNIQUEMENT pour tester le chemin "chargement réussi".
# Ce ne sont pas des données de marché.
COURBE_TEST = {
    "indice": "TEST",
    "type_taux": "zero",
    "base_calcul": "ACT/365F",
    "points": [{"maturite_annees": 1.0, "taux_pct": 2.0}, {"maturite_annees": 7.0, "taux_pct": 2.5}],
    "source": "valeurs de test",
    "hypothese": True,
    "justification": "test unitaire",
}


@pytest.fixture
def config_brute() -> dict[str, Any]:
    return lire_json(CHEMIN_CONFIG)


@pytest.fixture
def marche_brut() -> dict[str, Any]:
    return lire_json(RACINE / "data" / "marche_2026-11-06.json")


@pytest.fixture
def fixings() -> tuple[tuple[date, float], ...]:
    return lire_fixings_bce(RACINE / "data" / "eurusd_bce_2026-05-06_2026-11-06.csv")


def avec_courbes_test(config_brute: dict[str, Any]) -> dict[str, Any]:
    brut = copy.deepcopy(config_brute)
    brut["hypotheses_marche"]["courbe_EUR"] = dict(COURBE_TEST)
    brut["hypotheses_marche"]["courbe_USD"] = dict(COURBE_TEST)
    return brut


# --- Barrière et fixings --------------------------------------------------------------


def test_barriere_K_1_1770_au_07_05_2026(fixings):
    k = calcul_barriere(fixings, date(2026, 5, 6), date(2026, 11, 6))
    assert k.niveau == pytest.approx(1.1770, abs=1e-12)
    assert k.date_pic == date(2026, 5, 7)
    assert k.nb_observations == 133


def test_barriere_egalite_retient_la_premiere_date():
    fx = [(date(2026, 1, 1), 1.10), (date(2026, 1, 2), 1.20), (date(2026, 1, 3), 1.20)]
    assert calcul_barriere(fx, date(2026, 1, 1), date(2026, 1, 3)).date_pic == date(2026, 1, 2)


def test_barriere_bornes_incluses_et_fenetre_respectee():
    fx = [(date(2026, 1, 1), 1.30), (date(2026, 1, 2), 1.10), (date(2026, 1, 3), 1.25)]
    k = calcul_barriere(fx, date(2026, 1, 2), date(2026, 1, 3))
    assert (k.niveau, k.date_pic) == (1.25, date(2026, 1, 3))


def test_barriere_fenetre_vide_erreur():
    with pytest.raises(ErreurConfig, match="Aucun fixing"):
        calcul_barriere([(date(2026, 1, 1), 1.1)], date(2027, 1, 1), date(2027, 2, 1))


def test_spot_bce_a_la_date_de_valorisation(fixings, marche_brut):
    assert fixing_a_date(fixings, date(2026, 11, 6)) == pytest.approx(1.1165)
    assert marche_brut["spot"]["valeur"] == pytest.approx(fixing_a_date(fixings, date(2026, 11, 6)))


def test_fixing_absent_erreur(fixings):
    with pytest.raises(ErreurConfig, match="Pas de fixing"):
        fixing_a_date(fixings, date(2026, 11, 7))  # samedi


def test_fixings_ligne_invalide_erreur(tmp_path):
    f = tmp_path / "fx.csv"
    f.write_text("date,eurusd\n2026-05-06,1.17\n2026-05-07,abc\n", encoding="utf-8")
    with pytest.raises(ErreurConfig, match="ligne 3"):
        lire_fixings_bce(f)


def test_fixings_date_en_double_erreur(tmp_path):
    f = tmp_path / "fx.csv"
    f.write_text("date,eurusd\n2026-05-06,1.17\n2026-05-06,1.18\n", encoding="utf-8")
    with pytest.raises(ErreurConfig, match="double"):
        lire_fixings_bce(f)


def test_copie_data_identique_au_csv_historique():
    """data/ contient une copie du CSV racine utilisé par pricing_fx.py (référence)."""
    original = (RACINE / "eurusd_bce_2026-05-06_2026-11-06.csv").read_bytes()
    assert (RACINE / "data" / "eurusd_bce_2026-05-06_2026-11-06.csv").read_bytes() == original


# --- Config ----------------------------------------------------------------------------


def test_config_reelle(config_brute):
    config = charger_config(CHEMIN_CONFIG)
    assert config.date_valorisation == date(2026, 11, 6)
    assert config.nominal_eur == 50_000_000
    assert config.coupon_par_periode == pytest.approx(0.07)
    assert config.chemin_fixings.exists() and config.chemin_marche.exists()
    noms = {h.nom for h in config.hypotheses}
    assert {"coupon_par_periode", "date_depart_coupons", "bf25_manquant", "vols_au_dela_dernier_pilier"} <= noms
    assert all(h.justification for h in config.hypotheses)


def test_echeancier_14_dates_semestrielles():
    ech = echeancier_coupons(charger_config(CHEMIN_CONFIG))
    assert len(ech) == 14
    assert ech[0].date == date(2027, 5, 6) and ech[-1].date == date(2033, 11, 6)
    assert ech[1].t_annees == pytest.approx(1.0)  # 365 jours jusqu'au 06/11/2027
    assert all(b.t_annees > a.t_annees for a, b in zip(ech, ech[1:]))
    assert [c.numero for c in ech] == list(range(1, 15))


def test_hypothese_sans_justification_refusee(config_brute):
    config_brute["note"]["coupon_par_periode"]["justification"] = "  "
    with pytest.raises(ErreurConfig, match="sans justification"):
        config_depuis_dict(config_brute, RACINE)


def test_hypothese_marche_sans_drapeau_refusee(config_brute):
    config_brute["hypotheses_marche"]["bf25_manquant"]["hypothese"] = False
    with pytest.raises(ErreurConfig, match='"hypothese": true'):
        config_depuis_dict(config_brute, RACINE)


def test_cle_hypothese_inconnue_refusee(config_brute):
    config_brute["hypotheses_marche"]["bf_manquant"] = {"valeur_pct": 0.2}
    with pytest.raises(ErreurConfig, match="inconnue"):
        config_depuis_dict(config_brute, RACINE)


def test_champ_obligatoire_absent_nomme(config_brute):
    del config_brute["note"]["nominal_eur"]
    with pytest.raises(ErreurConfig, match=r"config\.note\.nominal_eur"):
        config_depuis_dict(config_brute, RACINE)


def test_fenetre_barriere_apres_valo_refusee(config_brute):
    config_brute["barriere"]["fin_observation"] = "2026-11-20"
    with pytest.raises(ErreurConfig, match="K ne serait pas"):
        config_depuis_dict(config_brute, RACINE)


# --- Données de marché -----------------------------------------------------------------


def sans_courbes(marche_brut: dict[str, Any]) -> dict[str, Any]:
    for dev in ("EUR", "USD"):
        marche_brut["courbes"][dev]["points"] = []
    return marche_brut


def test_marche_reel_se_charge(config_brute, marche_brut):
    marche = valider_marche(marche_brut, config_depuis_dict(config_brute, RACINE))
    assert marche.spot == pytest.approx(1.1165)
    assert [p.pilier for p in marche.piliers] == ["1W", "1M", "3M", "6M", "9M", "1Y"]
    assert all(p.bf25_pct == pytest.approx(0.20) and p.hypothese for p in marche.piliers)
    assert marche.piliers[-1].atm_pct == pytest.approx(6.76)
    assert marche.piliers[-1].rr25_pct == pytest.approx(-0.84)
    assert marche.extrapolation_vol == "vol_constante"
    # Courbes : données sourcées, pas des hypothèses.
    assert not marche.courbe_eur.hypothese and not marche.courbe_usd.hypothese
    assert marche.courbe_eur.type_taux == "swap" and marche.courbe_eur.base_calcul == "ACT/360"
    assert [p.pilier for p in marche.courbe_usd.points] == ["1M", "3M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y"]
    assert marche.points_de_terme is None
    assert (marche.k_controle, marche.date_pic_controle) == (1.177, date(2026, 5, 7))
    utilisees = " ".join(marche.hypotheses_utilisees)
    assert "bf25_manquant" in utilisees and "2Y, 3Y, 5Y, 7Y" in utilisees
    assert "courbe" not in utilisees
    assert any("Date de cotation" in a for a in marche.avertissements)
    assert any("1.0992" in a for a in marche.avertissements)


def test_contexte_reel_complet():
    ctx = charger_contexte(CHEMIN_CONFIG)
    assert ctx.barriere.niveau == pytest.approx(1.1770)
    assert ctx.marche.k_controle == pytest.approx(ctx.barriere.niveau)


def test_courbes_identiques_au_csv_ois(marche_brut):
    """Le fichier de marché reprend exactement data/courbes_ois_2026-11-06.csv."""
    with (RACINE / "data" / "courbes_ois_2026-11-06.csv").open(encoding="utf-8-sig", newline="") as f:
        lignes = list(csv.DictReader(f))
    for dev, col in (("EUR", "estr_swap_pct"), ("USD", "sofr_swap_pct")):
        attendu = {l["maturite"]: float(l[col]) for l in lignes if l[col].strip()}
        lu = {p["pilier"]: p["taux_pct"] for p in marche_brut["courbes"][dev]["points"]}
        assert lu == attendu


def test_marche_sans_hypotheses_liste_tout_ce_qui_manque(config_brute, marche_brut):
    config_brute["hypotheses_marche"] = {}
    with pytest.raises(DonneesManquantes) as e:
        valider_marche(sans_courbes(marche_brut), config_depuis_dict(config_brute, RACINE))
    m = e.value.manquants
    assert "Courbe EUR" in m[0] and "Courbe USD" in m[1]
    assert "Courbe EUR (€STR)" in str(e.value) and "hypotheses_marche" in str(e.value)
    for p in ("1W", "1M", "3M", "6M", "9M", "1Y"):
        assert f"Butterfly 25Δ {p}" in m
    for p in ("2Y", "3Y", "5Y", "7Y"):
        assert f"Vol ATM {p}" in m
    assert len(m) == 2 + 6 + 4


def test_hypotheses_de_courbe_comblent_un_manque(config_brute, marche_brut):
    marche = valider_marche(sans_courbes(marche_brut), config_depuis_dict(avec_courbes_test(config_brute), RACINE))
    assert marche.courbe_eur.hypothese and marche.courbe_usd.hypothese
    assert {"courbe_EUR", "courbe_USD"} <= set(marche.hypotheses_utilisees)


def test_marche_fichier_prioritaire_sur_hypothese(config_brute, marche_brut):
    marche = valider_marche(marche_brut, config_depuis_dict(avec_courbes_test(config_brute), RACINE))
    assert not marche.courbe_eur.hypothese and marche.courbe_eur.indice == "€STR OIS"
    assert "courbe_EUR" not in marche.hypotheses_utilisees


def test_date_de_courbe_differente_refusee(config_brute, marche_brut):
    marche_brut["courbes"]["USD"]["date"] = "2026-11-17"
    with pytest.raises(ErreurConfig, match=r"courbes\.USD\.date"):
        valider_marche(marche_brut, config_depuis_dict(config_brute, RACINE))


def test_k_du_fichier_incoherent_refuse(config_brute, marche_brut, fixings):
    marche_brut["barriere_K"]["valeur"] = 1.1765
    marche = valider_marche(marche_brut, config_depuis_dict(config_brute, RACINE))
    k = calcul_barriere(fixings, date(2026, 5, 6), date(2026, 11, 6))
    with pytest.raises(ErreurConfig, match="K du fichier"):
        verifier_coherence(marche, k, fixings, date(2026, 11, 6))


def test_spot_du_fichier_incoherent_refuse(config_brute, marche_brut, fixings):
    marche_brut["spot"]["valeur"] = 1.0992
    marche = valider_marche(marche_brut, config_depuis_dict(config_brute, RACINE))
    k = calcul_barriere(fixings, date(2026, 5, 6), date(2026, 11, 6))
    with pytest.raises(ErreurConfig, match="fixing BCE"):
        verifier_coherence(marche, k, fixings, date(2026, 11, 6))


def test_points_de_terme_remplacent_courbe_usd_pas_eur(config_brute, marche_brut):
    sans_courbes(marche_brut)
    marche_brut["points_de_terme"] = {
        "points": [{"maturite_annees": 1.0, "points_pips": 150.0}],
        "source": "valeurs de test",
    }
    with pytest.raises(DonneesManquantes) as e:
        valider_marche(marche_brut, config_depuis_dict(config_brute, RACINE))
    assert len(e.value.manquants) == 1 and "Courbe EUR" in e.value.manquants[0]


def test_trou_au_milieu_non_couvert_par_extrapolation(config_brute, marche_brut):
    marche_brut["volatilites"]["piliers"][3]["atm_pct"] = None  # 6M absent, 1Y présent
    with pytest.raises(DonneesManquantes) as e:
        valider_marche(marche_brut, config_depuis_dict(config_brute, RACINE))
    assert e.value.manquants == ("Vol ATM 6M",)


def test_date_de_marche_differente_refusee(config_brute, marche_brut):
    marche_brut["date_marche"] = "2026-11-17"
    with pytest.raises(ErreurConfig, match="une seule date de valorisation"):
        valider_marche(marche_brut, config_depuis_dict(config_brute, RACINE))


def test_maturites_non_croissantes_refusees(config_brute, marche_brut):
    p = marche_brut["volatilites"]["piliers"]
    p[0], p[1] = p[1], p[0]
    with pytest.raises(ErreurConfig, match="croissantes"):
        valider_marche(marche_brut, config_depuis_dict(config_brute, RACINE))


# --- Outils de dates -------------------------------------------------------------------


def test_ajouter_mois_fin_de_mois():
    assert ajouter_mois(date(2026, 8, 31), 6) == date(2027, 2, 28)
    assert ajouter_mois(date(2026, 11, 6), 14) == date(2028, 1, 6)


def test_fraction_annee():
    assert fraction_annee(date(2026, 1, 1), date(2026, 7, 1), "ACT/360") == pytest.approx(181 / 360)
    with pytest.raises(ErreurConfig):
        fraction_annee(date(2026, 1, 1), date(2027, 1, 1), "30/360")


def test_fichiers_json_valides():
    for f in (CHEMIN_CONFIG, RACINE / "data" / "marche_2026-11-06.json"):
        json.loads(f.read_text(encoding="utf-8"))
