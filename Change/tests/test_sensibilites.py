"""Tests de l'étape 6 : sensibilités par bump-and-reprice."""

from __future__ import annotations

import csv
import dataclasses
import math
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import norm

from fx.config import ChocsSensibilite, charger_contexte, echeancier_coupons
from fx.courbes import construire_courbes
from fx.digitales import valoriser_coupons
from fx.gk import d1_d2
from fx.sensibilites import (
    NOMS,
    calculer_sensibilites,
    choquer_courbes,
    choquer_surface,
    ecrire_csv,
    scenario_bf,
    valeurs_par_date,
)
from fx.smile import VolsDelta, construire_surface

RACINE = Path(__file__).resolve().parent.parent
SIGMA = 0.0676


@pytest.fixture(scope="module")
def contexte():
    return charger_contexte(RACINE / "config" / "valo_2026-11-06.json")


@pytest.fixture(scope="module")
def courbes(contexte):
    return construire_courbes(contexte.marche, contexte.config)


@pytest.fixture(scope="module")
def surface(contexte, courbes):
    return construire_surface(contexte.marche, courbes, contexte.config)


@pytest.fixture(scope="module")
def sens(contexte, surface):
    return calculer_sensibilites(contexte.config, surface, contexte.barriere.niveau)


@pytest.fixture(scope="module")
def plat(surface):
    """Surface à smile plat (σ = 6,76 % partout) : les grecques ont une forme analytique."""
    return dataclasses.replace(surface, vols=tuple(VolsDelta(SIGMA, SIGMA, SIGMA) for _ in surface.vols))


def petits_chocs(config):
    return dataclasses.replace(
        config, chocs=ChocsSensibilite(spot_rel=1e-4, vol_atm=1e-4, rr25=1e-4, bf25=1e-4, ecart_taux=1e-5, barriere_k=1e-5)
    )


def analytique(config, courbes, k):
    """Dérivées de V_i = M · DF_EUR · N(−d1) pour un smile plat."""
    m = config.nominal_eur * config.coupon_par_periode
    s = courbes.spot
    res = {n: [] for n in ("dS", "dsigma", "decart", "dK")}
    for c in echeancier_coupons(config):
        t = c.t_annees
        dff, dfd = courbes.eur.df(t), courbes.usd.df(t)
        d1, d2 = (float(x) for x in d1_d2(s, k, t, SIGMA, dfd, dff))
        phi, srt = norm.pdf(d1), SIGMA * math.sqrt(t)
        res["dS"].append(m * dff * -phi / (s * srt))
        res["dsigma"].append(m * dff * phi * d2 / SIGMA)
        res["decart"].append(m * dff * -phi * t / srt)
        res["dK"].append(m * dff * phi / (k * srt))
    return {n: np.array(v) for n, v in res.items()}


# --- Contrôles analytiques (smile plat, petits chocs) ----------------------------------


def test_grecques_premier_ordre_analytiques(contexte, courbes, plat):
    config = petits_chocs(contexte.config)
    k = contexte.barriere.niveau
    se = calculer_sensibilites(config, plat, k)
    a = analytique(config, courbes, k)
    c = config.chocs
    attendus = {
        "delta": a["dS"] * courbes.spot * c.spot_rel,
        "vega": a["dsigma"] * c.vol_atm,  # change de signe d'une date à l'autre : tolérance absolue
        "ecart_taux": a["decart"] * c.ecart_taux,
        "barriere_k": a["dK"] * c.barriere_k,
    }
    for nom, att in attendus.items():
        # Erreur de la différence centrée O(h²) : ~1e-6 en relatif avec ces chocs
        assert se.par_nom[nom] == pytest.approx(att, rel=1e-5, abs=1e-5 * np.max(np.abs(att))), nom


def test_vanna_egale_variation_du_vega_analytique(contexte, courbes, plat):
    config = petits_chocs(contexte.config)
    k, h = contexte.barriere.niveau, config.chocs.spot_rel
    se = calculer_sensibilites(config, plat, k)
    vega_haut = analytique(config, choquer_courbes(courbes, spot_rel=h), k)["dsigma"]
    vega_bas = analytique(config, choquer_courbes(courbes, spot_rel=-h), k)["dsigma"]
    attendu = (vega_haut - vega_bas) / 2 * config.chocs.vol_atm
    assert se.par_nom["vanna"] == pytest.approx(attendu, rel=1e-4)


def test_smile_plat_rr_et_bf_seuls_ne_changent_que_la_forme(contexte, plat):
    """Avec un smile plat, choquer RR et BF modifie la valeur (forme du smile), choquer rien ne la change pas."""
    k = contexte.barriere.niveau
    v0 = valeurs_par_date(contexte.config, plat, k)
    assert valeurs_par_date(contexte.config, choquer_surface(plat), k) == pytest.approx(v0, rel=1e-15)


# --- Chocs ------------------------------------------------------------------------------


def test_choc_ecart_de_taux_parallele_exact(courbes):
    choquee = choquer_courbes(courbes, ecart_taux=0.001)
    for t in (0.3, 1.0, 2.5, 7.0055):
        assert choquee.usd.taux_zero(t) - courbes.usd.taux_zero(t) == pytest.approx(0.001, abs=1e-13)
        assert choquee.eur.df(t) == courbes.eur.df(t)
        assert choquee.forward(t) == pytest.approx(courbes.forward(t) * math.exp(0.001 * t), rel=1e-13)


def test_choc_spot_garde_les_courbes(courbes):
    choquee = choquer_courbes(courbes, spot_rel=0.01)
    assert choquee.spot == pytest.approx(courbes.spot * 1.01)
    assert choquee.forward(3.0) == pytest.approx(courbes.forward(3.0) * 1.01, rel=1e-14)


def test_choc_de_vols_en_coordonnees_de_cotation(surface):
    s = choquer_surface(surface, d_atm=0.01, d_rr=0.005, d_bf=0.001)
    for v0, v1 in zip(surface.vols, s.vols):
        assert v1.vol_atm - v0.vol_atm == pytest.approx(0.01)
        assert (v1.vol_25c - v1.vol_25p) - (v0.vol_25c - v0.vol_25p) == pytest.approx(0.005)  # RR
        bf = lambda v: (v.vol_25c + v.vol_25p) / 2 - v.vol_atm  # noqa: E731
        assert bf(v1) - bf(v0) == pytest.approx(0.001)


# --- Données réelles --------------------------------------------------------------------


def test_base_egale_valeurs_de_l_etape_5(contexte, courbes, surface, sens):
    r = valoriser_coupons(contexte.config, courbes, surface, contexte.barriere)
    assert sens.base == pytest.approx(np.array([x.valeur_smile for x in r]), rel=1e-15)


def test_signes_attendus(sens):
    assert sens.total("delta") < 0  # EUR/USD monte : la condition X < K est moins probable
    assert sens.total("ecart_taux") < 0  # forward plus haut
    assert sens.total("barriere_k") > 0  # barrière plus haute
    assert sens.total("rr25") > 0  # skew moins négatif : pente σ'(K) moins négative
    assert np.all(sens.par_nom["barriere_k"] > 0) and np.all(sens.par_nom["delta"] < 0)


def test_vega_change_de_signe_avec_la_probabilite(contexte, courbes, surface, sens):
    """Digitale dans la monnaie (P > 50 %) : vega < 0 ; hors de la monnaie : vega > 0."""
    r = valoriser_coupons(contexte.config, courbes, surface, contexte.barriere)
    for x, vg in zip(r, sens.par_nom["vega"]):
        if x.p_smile > 0.55:
            assert vg < 0
        elif x.p_smile < 0.45:
            assert vg > 0


def test_scenario_bf_coherent_avec_la_sensibilite(contexte, courbes, sens):
    k = contexte.barriere.niveau
    bas, haut = contexte.config.hypotheses_marche.bf25_plage_pct
    v_bas = scenario_bf(contexte.config, contexte.marche, courbes, k, bas)
    v_haut = scenario_bf(contexte.config, contexte.marche, courbes, k, haut)
    # Écart de 0,1 point entre les deux scénarios = choc BF de +0,1 point (tous les BF sont supposés)
    assert float(np.sum(v_haut - v_bas)) == pytest.approx(sens.total("bf25"), rel=1e-2)
    assert scenario_bf(contexte.config, contexte.marche, courbes, k, 0.20) == pytest.approx(sens.base, rel=1e-14)


def test_csv(contexte, sens, tmp_path):
    chemin = tmp_path / "sensibilites_fx.csv"
    ecrire_csv(contexte.config, sens, chemin)
    with chemin.open(encoding="utf-8-sig") as f:
        lignes = list(csv.reader(f, delimiter=";"))
    assert len(lignes) == 16 and lignes[0][3] == "delta_spot_+1pct_EUR"
    assert lignes[0][-1] == "barriere_K_+0,01_EUR"
    total = dict(zip(lignes[0], lignes[-1]))
    assert float(total["delta_spot_+1pct_EUR"]) == pytest.approx(sens.total("delta"), abs=0.01)
    assert set(sens.par_nom) == set(NOMS)
