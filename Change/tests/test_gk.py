"""Tests de l'étape 3 : Garman-Kohlhagen et conventions EUR/USD."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.stats import norm

from fx.gk import (
    convention_delta,
    d1_d2,
    delta,
    digitale_actif,
    digitale_cash,
    forward,
    prix_vanille,
    probabilite_eur_sous_barriere,
    strike_atm_dns,
    strike_depuis_delta,
    strikes_piliers,
    vega,
)

# Cas de référence du CLAUDE.md : S = 1,1165, K = 1,1770, σ = 6,76 %, taux EUR = USD = 3 %, T = 1.
S, K, T, VOL = 1.1165, 1.1770, 1.0, 0.0676
DF3 = math.exp(-0.03)
# Cas avec écart de taux (ordres de grandeur des courbes du 06/11/2026 à 7 ans).
DF_USD7, DF_EUR7 = 0.73688, 0.80927
CAS = [
    (S, K, 1.0, VOL, DF3, DF3),
    (S, K, 7.0055, VOL, DF_USD7, DF_EUR7),
    (S, 1.05, 0.5, 0.10, 0.98, 0.99),
]


# --- Formule du coupon -----------------------------------------------------------------


def test_cas_de_reference_claude_md():
    """77,24 % à 1 an en formule fermée (77,26 % au Monte Carlo du camarade)."""
    p = probabilite_eur_sous_barriere(S, K, T, VOL, DF3, DF3)
    assert float(p) == pytest.approx(0.7724, abs=5e-5)


def test_formule_explicite_de_d1():
    d1, d2 = d1_d2(S, K, T, VOL, DF3, DF3)
    attendu = (math.log(S / K) + 0.5 * VOL**2 * T) / (VOL * math.sqrt(T))
    assert float(d1) == pytest.approx(attendu, rel=1e-14)
    assert float(d1 - d2) == pytest.approx(VOL * math.sqrt(T), rel=1e-14)


@pytest.mark.parametrize("s, k, t, v, dfd, dff", CAS)
def test_digitale_eur_vaut_df_eur_fois_n_moins_d1(s, k, t, v, dfd, dff):
    d1, _ = d1_d2(s, k, t, v, dfd, dff)
    assert float(digitale_actif("put", s, k, t, v, dfd, dff)) == pytest.approx(dff * norm.cdf(-float(d1)), rel=1e-13)


# --- Parités ---------------------------------------------------------------------------


@pytest.mark.parametrize("s, k, t, v, dfd, dff", CAS)
def test_parite_call_put(s, k, t, v, dfd, dff):
    c = prix_vanille("call", s, k, t, v, dfd, dff)
    p = prix_vanille("put", s, k, t, v, dfd, dff)
    assert float(c - p) == pytest.approx(s * dff - k * dfd, abs=1e-14)


@pytest.mark.parametrize("s, k, t, v, dfd, dff", CAS)
def test_parites_des_digitales(s, k, t, v, dfd, dff):
    assert float(digitale_cash("call", s, k, t, v, dfd, dff) + digitale_cash("put", s, k, t, v, dfd, dff)) == pytest.approx(dfd, abs=1e-15)
    assert float(digitale_actif("call", s, k, t, v, dfd, dff) + digitale_actif("put", s, k, t, v, dfd, dff)) == pytest.approx(dff, abs=1e-15)
    # Call vanille = S · AoN call (en USD) − K · CoN call
    c = prix_vanille("call", s, k, t, v, dfd, dff)
    reconstitue = s * digitale_actif("call", s, k, t, v, dfd, dff) - k * digitale_cash("call", s, k, t, v, dfd, dff)
    assert float(c) == pytest.approx(float(reconstitue), abs=1e-14)


@pytest.mark.parametrize("s, k, t, v, dfd, dff", CAS)
def test_digitale_cash_egale_moins_derivee_du_call_en_strike(s, k, t, v, dfd, dff):
    h = k * 1e-5
    deriv = (prix_vanille("call", s, k + h, t, v, dfd, dff) - prix_vanille("call", s, k - h, t, v, dfd, dff)) / (2 * h)
    assert float(-deriv) == pytest.approx(float(digitale_cash("call", s, k, t, v, dfd, dff)), abs=1e-8)


# --- Monte Carlo -----------------------------------------------------------------------


@pytest.mark.parametrize("s, k, t, v, dfd, dff", CAS)
def test_monte_carlo_a_0_1_pct(s, k, t, v, dfd, dff):
    """Vérification à 0,1 point près, sous les deux mesures.

    - mesure USD : X_T = F · exp(−σ²T/2 + σ√T Z) ; le coupon (1 EUR si X_T < K) vaut en USD
      DF_USD · E[X_T 1{X_T < K}], soit en EUR cette quantité divisée par S ;
    - mesure EUR : X_T = F · exp(+σ²T/2 + σ√T Z) ; P(X_T < K) = N(−d1).
    """
    rng = np.random.default_rng(20261106)
    z = rng.standard_normal(1_000_000)
    z = np.concatenate([z, -z])  # variables antithétiques
    f = float(forward(s, dfd, dff))
    sig_rt = v * math.sqrt(t)

    x_usd = f * np.exp(-0.5 * sig_rt**2 + sig_rt * z)
    aon_eur_mc = dfd * np.mean(x_usd * (x_usd < k)) / s
    con_mc = dfd * np.mean(x_usd < k)
    x_eur = f * np.exp(0.5 * sig_rt**2 + sig_rt * z)
    proba_eur_mc = np.mean(x_eur < k)

    assert aon_eur_mc == pytest.approx(float(digitale_actif("put", s, k, t, v, dfd, dff)), abs=1e-3)
    assert con_mc == pytest.approx(float(digitale_cash("put", s, k, t, v, dfd, dff)), abs=1e-3)
    assert proba_eur_mc == pytest.approx(float(probabilite_eur_sous_barriere(s, k, t, v, dfd, dff)), abs=1e-3)
    # Prix vanille : même tolérance relative au nominal
    put_mc = dfd * np.mean(np.maximum(k - x_usd, 0.0))
    assert put_mc == pytest.approx(float(prix_vanille("put", s, k, t, v, dfd, dff)), abs=1e-3 * s)


# --- Grecques --------------------------------------------------------------------------


@pytest.mark.parametrize("s, k, t, v, dfd, dff", CAS)
def test_vega_derivee_numerique(s, k, t, v, dfd, dff):
    h = 1e-5
    num = (prix_vanille("call", s, k, t, v + h, dfd, dff) - prix_vanille("call", s, k, t, v - h, dfd, dff)) / (2 * h)
    assert float(vega(s, k, t, v, dfd, dff)) == pytest.approx(float(num), rel=1e-7)


@pytest.mark.parametrize("type_option", ["call", "put"])
@pytest.mark.parametrize("s, k, t, v, dfd, dff", CAS)
def test_delta_spot_egale_derivee_en_spot(type_option, s, k, t, v, dfd, dff):
    h = s * 1e-6
    num = (prix_vanille(type_option, s + h, k, t, v, dfd, dff) - prix_vanille(type_option, s - h, k, t, v, dfd, dff)) / (2 * h)
    assert float(delta(type_option, "spot", s, k, t, v, dfd, dff)) == pytest.approx(float(num), rel=1e-7)
    assert float(delta(type_option, "forward", s, k, t, v, dfd, dff)) == pytest.approx(float(num) / dff, rel=1e-7)


# --- Conventions EUR/USD ---------------------------------------------------------------


def test_convention_delta_spot_jusqu_a_un_an_inclus():
    assert convention_delta(1.0, 1.0) == "spot"
    assert convention_delta(0.0192, 1.0) == "spot"
    assert convention_delta(1.0027, 1.0) == "forward"


@pytest.mark.parametrize("s, k, t, v, dfd, dff", CAS)
@pytest.mark.parametrize("conv", ["spot", "forward"])
def test_atm_dns_delta_neutre(s, k, t, v, dfd, dff, conv):
    k_atm = strike_atm_dns(s, t, v, dfd, dff)
    total = delta("call", conv, s, k_atm, t, v, dfd, dff) + delta("put", conv, s, k_atm, t, v, dfd, dff)
    assert float(total) == pytest.approx(0.0, abs=1e-14)
    assert float(k_atm) > float(forward(s, dfd, dff))  # DNS légèrement au-dessus du forward


@pytest.mark.parametrize("s, k, t, v, dfd, dff", CAS)
@pytest.mark.parametrize("conv", ["spot", "forward"])
@pytest.mark.parametrize("type_option, cible", [("call", 0.25), ("put", -0.25), ("call", 0.10), ("put", -0.10)])
def test_inversion_delta_strike_aller_retour(s, k, t, v, dfd, dff, conv, type_option, cible):
    kk = strike_depuis_delta(cible, type_option, conv, s, t, v, dfd, dff)
    assert float(delta(type_option, conv, s, kk, t, v, dfd, dff)) == pytest.approx(cible, abs=1e-13)


def test_inversion_delta_impossible_refusee():
    # Delta spot d'un call plafonné à DF_EUR < 1 : 0,995 n'est pas atteignable si DF_EUR = 0,99.
    with pytest.raises(ValueError, match="atteignable"):
        strike_depuis_delta(0.995, "call", "spot", S, 1.0, VOL, 0.98, 0.99)


def test_strikes_piliers_ordonnes_et_conventions():
    # 1 an : delta spot ; 2 ans : delta forward
    for t, dfd, dff in ((1.0, 0.95869, 0.97211), (2.0027, 0.91734, 0.94303)):
        kp, ka, kc = strikes_piliers(S, t, dfd, dff, 0.0676 + 0.002 + 0.0042, 0.0676, 0.0676 + 0.002 - 0.0042, 1.0)
        assert kp < ka < kc
        conv = convention_delta(t, 1.0)
        assert float(delta("put", conv, S, kp, t, 0.0676 + 0.0062, dfd, dff)) == pytest.approx(-0.25, abs=1e-13)
        assert float(delta("call", conv, S, kc, t, 0.0676 - 0.0022, dfd, dff)) == pytest.approx(0.25, abs=1e-13)


def test_fonctions_vectorisees():
    ks = np.array([1.05, 1.10, 1.1770, 1.25])
    p = probabilite_eur_sous_barriere(S, ks, T, VOL, DF3, DF3)
    assert p.shape == (4,) and np.all(np.diff(p) > 0)


@pytest.mark.parametrize("arg", ["spot", "t", "vol"])
def test_entrees_invalides(arg):
    params = dict(spot=S, strike=K, t=T, vol=VOL, df_dom=DF3, df_for=DF3)
    params[arg] = 0.0
    with pytest.raises(ValueError):
        prix_vanille("call", **params)
