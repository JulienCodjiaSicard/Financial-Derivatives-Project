"""Garman-Kohlhagen pour EUR/USD : vanilles, digitales, vega, deltas, inversion delta -> strike.

Notations : X = USD pour 1 EUR. Devise domestique = USD (devise de cotation et de la
prime), devise étrangère = EUR. Les fonctions prennent directement les facteurs
d'actualisation, ce qui évite de passer par des taux :
    df_dom = DF_USD(T), df_for = DF_EUR(T), F = S · df_for / df_dom,
    d1 = [ln(F / K) + σ² T / 2] / (σ √T), d2 = d1 − σ √T.

Unités : sauf mention contraire, les prix sont en USD pour 1 EUR de nominal ; les vols
et les maturités sont en décimal et en années.

Conventions de marché EUR/USD (prime en USD) : delta non ajusté de la prime ; delta
spot jusqu'à 1 an inclus, delta forward au-delà (seuil dans la config) ; ATM
delta-neutre (DNS) : K_ATM = F · exp(σ² T / 2).

Toutes les fonctions acceptent des scalaires ou des tableaux numpy (diffusion).
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.special import ndtr, ndtri

TypeOption = Literal["call", "put"]
ConventionDelta = Literal["spot", "forward"]

_RACINE_2PI = np.sqrt(2.0 * np.pi)


def _densite(x: NDArray[np.float64]) -> NDArray[np.float64]:
    """Densité de la loi normale centrée réduite."""
    return np.exp(-0.5 * x * x) / _RACINE_2PI


def _verifier(spot: ArrayLike, t: ArrayLike, vol: ArrayLike, df_dom: ArrayLike, df_for: ArrayLike) -> None:
    for nom, x in (("spot", spot), ("t", t), ("vol", vol), ("df_dom", df_dom), ("df_for", df_for)):
        if np.any(np.asarray(x) <= 0) or not np.all(np.isfinite(x)):
            raise ValueError(f"{nom} doit être strictement positif et fini")


def forward(spot: ArrayLike, df_dom: ArrayLike, df_for: ArrayLike) -> NDArray[np.float64]:
    """F = S · DF_EUR / DF_USD."""
    return np.asarray(spot, dtype=float) * np.asarray(df_for) / np.asarray(df_dom)


def d1_d2(
    spot: ArrayLike, strike: ArrayLike, t: ArrayLike, vol: ArrayLike, df_dom: ArrayLike, df_for: ArrayLike
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Renvoie (d1, d2)."""
    _verifier(spot, t, vol, df_dom, df_for)
    if np.any(np.asarray(strike) <= 0):
        raise ValueError("strike doit être strictement positif")
    sig_rt = np.asarray(vol) * np.sqrt(t)
    d1 = (np.log(forward(spot, df_dom, df_for) / np.asarray(strike)) + 0.5 * sig_rt**2) / sig_rt
    return d1, d1 - sig_rt


# ---------------------------------------------------------------------------
# Vanilles
# ---------------------------------------------------------------------------


def prix_vanille(
    type_option: TypeOption,
    spot: ArrayLike,
    strike: ArrayLike,
    t: ArrayLike,
    vol: ArrayLike,
    df_dom: ArrayLike,
    df_for: ArrayLike,
) -> NDArray[np.float64]:
    """Call ou put européen, en USD pour 1 EUR de nominal."""
    d1, d2 = d1_d2(spot, strike, t, vol, df_dom, df_for)
    f, k = forward(spot, df_dom, df_for), np.asarray(strike)
    if type_option == "call":
        return np.asarray(df_dom) * (f * ndtr(d1) - k * ndtr(d2))
    if type_option == "put":
        return np.asarray(df_dom) * (k * ndtr(-d2) - f * ndtr(-d1))
    raise ValueError(f"type_option inconnu : {type_option!r}")


def vega(
    spot: ArrayLike, strike: ArrayLike, t: ArrayLike, vol: ArrayLike, df_dom: ArrayLike, df_for: ArrayLike
) -> NDArray[np.float64]:
    """∂Prix/∂σ (identique pour call et put), en USD pour 1 EUR et pour 1,00 de vol (100 points)."""
    d1, _ = d1_d2(spot, strike, t, vol, df_dom, df_for)
    return np.asarray(spot) * np.asarray(df_for) * _densite(d1) * np.sqrt(t)


# ---------------------------------------------------------------------------
# Digitales
# ---------------------------------------------------------------------------


def digitale_cash(
    type_option: TypeOption,
    spot: ArrayLike,
    strike: ArrayLike,
    t: ArrayLike,
    vol: ArrayLike,
    df_dom: ArrayLike,
    df_for: ArrayLike,
) -> NDArray[np.float64]:
    """Cash-or-nothing : paie 1 USD si X_T > K (call) ou X_T < K (put). Valeur en USD : DF_USD · N(±d2)."""
    _, d2 = d1_d2(spot, strike, t, vol, df_dom, df_for)
    signe = {"call": 1.0, "put": -1.0}.get(type_option)
    if signe is None:
        raise ValueError(f"type_option inconnu : {type_option!r}")
    return np.asarray(df_dom) * ndtr(signe * d2)


def digitale_actif(
    type_option: TypeOption,
    spot: ArrayLike,
    strike: ArrayLike,
    t: ArrayLike,
    vol: ArrayLike,
    df_dom: ArrayLike,
    df_for: ArrayLike,
) -> NDArray[np.float64]:
    """Asset-or-nothing : paie 1 EUR si X_T > K (call) ou X_T < K (put). Valeur en EUR : DF_EUR · N(±d1).

    C'est le coupon de la note : sous la mesure risque-neutre EUR, P(X_T < K) = N(−d1).
    Valeur en USD : multiplier par le spot.
    """
    d1, _ = d1_d2(spot, strike, t, vol, df_dom, df_for)
    signe = {"call": 1.0, "put": -1.0}.get(type_option)
    if signe is None:
        raise ValueError(f"type_option inconnu : {type_option!r}")
    return np.asarray(df_for) * ndtr(signe * d1)


def probabilite_eur_sous_barriere(
    spot: ArrayLike, strike: ArrayLike, t: ArrayLike, vol: ArrayLike, df_dom: ArrayLike, df_for: ArrayLike
) -> NDArray[np.float64]:
    """P(X_T < K) sous la mesure risque-neutre EUR : N(−d1)."""
    d1, _ = d1_d2(spot, strike, t, vol, df_dom, df_for)
    return ndtr(-d1)


# ---------------------------------------------------------------------------
# Deltas, ATM et inversion delta -> strike
# ---------------------------------------------------------------------------


def convention_delta(t: float, seuil_spot_annees: float) -> ConventionDelta:
    """Delta spot jusqu'au seuil inclus (1 an pour EUR/USD), delta forward au-delà."""
    return "spot" if t <= seuil_spot_annees + 1e-12 else "forward"


def delta(
    type_option: TypeOption,
    convention: ConventionDelta,
    spot: ArrayLike,
    strike: ArrayLike,
    t: ArrayLike,
    vol: ArrayLike,
    df_dom: ArrayLike,
    df_for: ArrayLike,
) -> NDArray[np.float64]:
    """Delta non ajusté de la prime. Spot : ±DF_EUR · N(±d1) (= ∂Prix/∂S) ; forward : ±N(±d1)."""
    d1, _ = d1_d2(spot, strike, t, vol, df_dom, df_for)
    if type_option == "call":
        d = ndtr(d1)
    elif type_option == "put":
        d = -ndtr(-d1)
    else:
        raise ValueError(f"type_option inconnu : {type_option!r}")
    if convention == "spot":
        return np.asarray(df_for) * d
    if convention == "forward":
        return d
    raise ValueError(f"convention de delta inconnue : {convention!r}")


def strike_atm_dns(spot: ArrayLike, t: ArrayLike, vol: ArrayLike, df_dom: ArrayLike, df_for: ArrayLike) -> NDArray[np.float64]:
    """Strike ATM delta-neutre (delta call + delta put = 0, non ajusté) : K = F · exp(σ² T / 2)."""
    _verifier(spot, t, vol, df_dom, df_for)
    return forward(spot, df_dom, df_for) * np.exp(0.5 * np.asarray(vol) ** 2 * np.asarray(t))


def strike_depuis_delta(
    delta_cible: ArrayLike,
    type_option: TypeOption,
    convention: ConventionDelta,
    spot: ArrayLike,
    t: ArrayLike,
    vol: ArrayLike,
    df_dom: ArrayLike,
    df_for: ArrayLike,
) -> NDArray[np.float64]:
    """Strike dont le delta (non ajusté) vaut delta_cible, à la vol donnée (celle du pilier).

    Delta non ajusté : formule fermée. On retrouve d1 puis K = F · exp(−d1 σ √T + σ² T / 2).
    Conventions de signe : call delta > 0 (ex. 0,25), put delta < 0 (ex. −0,25).
    """
    _verifier(spot, t, vol, df_dom, df_for)
    dc = np.asarray(delta_cible, dtype=float)
    echelle = np.asarray(df_for) if convention == "spot" else 1.0
    if convention not in ("spot", "forward"):
        raise ValueError(f"convention de delta inconnue : {convention!r}")
    if type_option == "call":
        n = dc / echelle  # N(d1)
        if np.any(n <= 0) or np.any(n >= 1):
            raise ValueError("delta call hors de l'intervalle atteignable")
        d1 = ndtri(n)
    elif type_option == "put":
        n = -dc / echelle  # N(−d1)
        if np.any(n <= 0) or np.any(n >= 1):
            raise ValueError("delta put hors de l'intervalle atteignable")
        d1 = -ndtri(n)
    else:
        raise ValueError(f"type_option inconnu : {type_option!r}")
    sig_rt = np.asarray(vol) * np.sqrt(t)
    return forward(spot, df_dom, df_for) * np.exp(-d1 * sig_rt + 0.5 * sig_rt**2)


def strikes_piliers(
    spot: float,
    t: float,
    df_dom: float,
    df_for: float,
    vol_25p: float,
    vol_atm: float,
    vol_25c: float,
    seuil_delta_spot_annees: float,
    delta_pilier: float = 0.25,
    convention: ConventionDelta | None = None,
) -> tuple[float, float, float]:
    """Strikes (25Δ put, ATM DNS, 25Δ call), chacun à la vol de son pilier.

    Convention de delta selon T (seuil de la config), sauf si ``convention`` est imposée.
    """
    conv = convention or convention_delta(t, seuil_delta_spot_annees)
    k_p = float(strike_depuis_delta(-delta_pilier, "put", conv, spot, t, vol_25p, df_dom, df_for))
    k_atm = float(strike_atm_dns(spot, t, vol_atm, df_dom, df_for))
    k_c = float(strike_depuis_delta(delta_pilier, "call", conv, spot, t, vol_25c, df_dom, df_for))
    return k_p, k_atm, k_c
