"""Tests de l'étape 4 : smile (Vanna-Volga, SABR) et interpolation en temps."""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path

import numpy as np
import pytest

from fx.config import charger_contexte, echeancier_coupons
from fx.courbes import construire_courbes
from fx.gk import convention_delta, delta
from fx.smile import (
    ErreurSmile,
    VolsDelta,
    calibrer_sabr,
    construire_smile,
    construire_surface,
    densite_implicite,
    vol_sabr,
    vols_depuis_cotations,
)

RACINE = Path(__file__).resolve().parent.parent
METHODES = [("vanna_volga", 1), ("vanna_volga", 2), ("sabr", 2)]


@pytest.fixture(scope="module")
def contexte():
    return charger_contexte(RACINE / "config" / "valo_2026-11-06.json")


@pytest.fixture(scope="module")
def courbes(contexte):
    return construire_courbes(contexte.marche, contexte.config)


@pytest.fixture(scope="module")
def surface(contexte, courbes):
    return construire_surface(contexte.marche, courbes, contexte.config)


# --- Piliers ---------------------------------------------------------------------------


def test_vols_des_piliers():
    v = vols_depuis_cotations(6.76, -0.84, 0.20)
    assert (v.vol_25p, v.vol_atm, v.vol_25c) == pytest.approx((0.0738, 0.0676, 0.0654), abs=1e-15)
    assert v.vol_25c - v.vol_25p == pytest.approx(-0.0084, abs=1e-15)  # RR
    assert (v.vol_25c + v.vol_25p) / 2 - v.vol_atm == pytest.approx(0.0020, abs=1e-15)  # BF


def test_surface_reelle(surface):
    assert surface.piliers == ("1W", "1M", "3M", "6M", "9M", "1Y")
    assert surface.extrapolation == "vol_constante"
    assert surface.methode == "vanna_volga" and surface.ordre_vv == 2


# --- Le smile repasse par les trois piliers ----------------------------------------------


@pytest.mark.parametrize("methode, ordre", METHODES)
def test_smile_repasse_par_les_piliers(surface, methode, ordre):
    for t, v in zip(surface.temps, surface.vols):
        sm = surface.smile(t)
        tol = 1e-12 if methode == "vanna_volga" else 1e-9
        assert sm.vol(np.array(sm.strikes), methode, ordre) == pytest.approx(np.array(v.comme_tuple()), abs=tol)


def test_strikes_des_piliers_aux_bons_deltas(surface):
    for t in surface.temps:
        sm = surface.smile(t)
        conv = convention_delta(t, 1.0)
        assert sm.convention == conv
        kp, ka, kc = sm.strikes
        assert float(delta("put", conv, sm.spot, kp, t, sm.vols.vol_25p, sm.df_dom, sm.df_for)) == pytest.approx(-0.25, abs=1e-12)
        assert float(delta("call", conv, sm.spot, kc, t, sm.vols.vol_25c, sm.df_dom, sm.df_for)) == pytest.approx(0.25, abs=1e-12)
        dn = delta("call", conv, sm.spot, ka, t, sm.vols.vol_atm, sm.df_dom, sm.df_for) + delta(
            "put", conv, sm.spot, ka, t, sm.vols.vol_atm, sm.df_dom, sm.df_for
        )
        assert float(dn) == pytest.approx(0.0, abs=1e-13)


def test_smile_plat_si_piliers_egaux():
    sm = construire_smile(2.0, 1.1, 0.92, 0.94, VolsDelta(0.07, 0.07, 0.07), 1.0)
    ks = np.linspace(0.9, 1.4, 11)
    for methode, ordre in METHODES:
        # SABR : ν converge vers 0 sans l'atteindre exactement (borne de l'optimiseur)
        tol = 1e-12 if methode == "vanna_volga" else 1e-7
        assert sm.vol(ks, methode, ordre) == pytest.approx(np.full(11, 0.07), abs=tol)


def test_skew_negatif_vol_decroissante_en_strike(surface):
    sm = surface.smile(1.0)
    ks = np.linspace(sm.strikes[0], sm.strikes[2], 50)
    for methode, ordre in METHODES:
        assert np.all(np.diff(sm.vol(ks, methode, ordre)) < 0)


def test_vv2_forme_stable_egale_forme_de_castagna_mercurio(surface):
    """La forme utilisée est algébriquement celle de l'article (hors zone d1·d2 ≈ 0)."""
    sm = surface.smile(0.5)
    s2 = sm.vols.vol_atm
    for k in (1.06, 1.10, 1.177, 1.20):
        y1, _, y3 = sm._poids(k)
        d_1 = float(sm.vol_vv1(k)) - s2
        k1, _, k3 = sm.strikes
        d_2 = float(y1 * sm._d1d2(k1) * (sm.vols.vol_25p - s2) ** 2 + y3 * sm._d1d2(k3) * (sm.vols.vol_25c - s2) ** 2)
        dd = float(sm._d1d2(k))
        article = s2 + (-s2 + math.sqrt(s2**2 + dd * (2 * s2 * d_1 + d_2))) / dd
        assert float(sm.vol_vv2(k)) == pytest.approx(article, rel=1e-10)


def test_vv2_instable_signale():
    sm = construire_smile(0.1, 1.1, 0.97, 0.98, vols_depuis_cotations(5.0, -10.0, 2.0), 1.0)
    with pytest.raises(ErreurSmile, match="ordre 2"):
        sm.vol_vv2(np.linspace(sm.strikes[0] * 0.5, sm.strikes[2] * 1.5, 200))


def test_sabr_calibration_exacte_et_atm(surface):
    for t in surface.temps:
        sm = surface.smile(t)
        p = sm.sabr
        assert p.residu_max < 1e-10 and -1 < p.rho < 0 and p.nu > 0  # skew négatif → ρ < 0
    # Cas ATM du développement limité z → 0
    assert float(vol_sabr(1.1, 1.1, 1.0, 0.07, 1.0, -0.3, 0.5)) == pytest.approx(
        0.07 * (1 + (-0.3 * 0.5 * 0.07 / 4 + (2 - 3 * 0.09) / 24 * 0.25)), rel=1e-14
    )


def test_sabr_beta_inferieur_a_un():
    p = calibrer_sabr((1.05, 1.10, 1.15), (0.075, 0.068, 0.066), 1.10, 1.0, 0.5)
    assert p.residu_max < 1e-10 and p.beta == 0.5


# --- Interpolation en temps ------------------------------------------------------------


def test_temps_variance_totale_lineaire_entre_piliers(surface):
    t0, t1 = surface.temps[3], surface.temps[4]  # 6M, 9M
    t = 0.6
    w = (t - t0) / (t1 - t0)
    v = surface.vols_delta(t)
    for i in range(3):
        a, b = surface.vols[3].comme_tuple()[i], surface.vols[4].comme_tuple()[i]
        assert v.comme_tuple()[i] ** 2 * t == pytest.approx((1 - w) * a * a * t0 + w * b * b * t1, rel=1e-14)


def test_temps_vols_exactes_aux_piliers_et_constantes_aux_bords(surface):
    for t, v in zip(surface.temps, surface.vols):
        assert surface.vols_delta(t) == v
    assert surface.vols_delta(0.001) == surface.vols[0]
    assert surface.vols_delta(7.0055) == surface.vols[-1]
    assert surface.est_extrapole(1.5) and not surface.est_extrapole(1.0)


def test_extrapolation_refusee_sans_hypothese(surface):
    sans = dataclasses.replace(surface, extrapolation=None)
    assert sans.vols_delta(1.0) == surface.vols[-1]
    with pytest.raises(ErreurSmile, match="sans hypothèse"):
        sans.vols_delta(1.5)


def test_arbitrage_calendaire_dans_les_cotations_refuse(surface):
    vols = list(surface.vols)
    vols[4] = VolsDelta(0.05, 0.05, 0.05)  # 9M : variance totale < 6M
    with pytest.raises(ErreurSmile, match="calendaire"):
        dataclasses.replace(surface, vols=tuple(vols))


def test_continuite_au_passage_delta_spot_delta_forward(surface):
    """Après 1 an, le delta forward tenu constant est celui du pilier 1Y : pas de saut."""
    conv, d = surface.convention_et_delta(1.5)
    assert conv == "forward" and d == pytest.approx(0.25 / surface.courbes.eur.df(1.0), rel=1e-14)
    avant, apres = surface.smile(1.0), surface.smile(1.0 + 1e-7)
    assert apres.strikes == pytest.approx(avant.strikes, abs=1e-6)


@pytest.mark.parametrize("methode, ordre", [("vanna_volga", 2), ("sabr", 2)])
def test_pas_d_arbitrage_calendaire_variance_totale_croissante(surface, courbes, methode, ordre):
    """À log-moneyness forward fixée k = ln(K / F(T)), σ²(T, K) · T croît avec T."""
    ts = np.concatenate([np.linspace(0.25, 1.2, 40), np.linspace(1.25, 7.0, 25)])
    for k in (-0.25, -0.15, -0.08, -0.03, 0.0, 0.03, 0.08, 0.15, 0.25):
        w = [float(surface.vol(t, courbes.forward(t) * math.exp(k), methode, ordre)) ** 2 * t for t in ts]
        assert np.all(np.diff(w) > 0), (methode, k)


def test_variance_totale_croissante_a_delta_constant(surface):
    ts = np.linspace(0.01, 7.0, 300)
    for i in range(3):
        w = [surface.vols_delta(t).comme_tuple()[i] ** 2 * t for t in ts]
        assert np.all(np.diff(w) > 0)


# --- Pas d'arbitrage papillon ----------------------------------------------------------


@pytest.mark.parametrize("methode, ordre", METHODES)
def test_densite_positive_autour_de_k(contexte, surface, methode, ordre):
    k = contexte.barriere.niveau
    grille = k * np.linspace(0.85, 1.15, 61)
    for c in echeancier_coupons(contexte.config):
        dens = densite_implicite(surface.smile(c.t_annees), grille, methode, ordre)
        assert np.all(dens > 0), (methode, c.numero)


def test_densite_integre_a_un(surface):
    """Contrôle de cohérence : la densité implicite à 1 an intègre à ~1 et redonne le forward."""
    sm = surface.smile(1.0)
    ks = np.linspace(0.85, 1.55, 3001)
    dens = densite_implicite(sm, ks)
    assert np.trapezoid(dens, ks) == pytest.approx(1.0, abs=2e-3)
    assert np.trapezoid(ks * dens, ks) == pytest.approx(sm.forward, abs=2e-3)
