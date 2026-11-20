"""Vol effective σ_eff(T_i) et barrière effective K_eff(T_i).

Ce qui est livré au moteur Monte Carlo commun est la barrière effective (voir export_mc).
La vol effective est mal posée pour ce produit : quand le forward approche K, P ≈ N(−σ√T/2)
et il faut une vol extrême pour descendre nettement sous 50 % ; au-delà, deux racines,
aucune réaliste. Elle reste calculée ici pour information (plan initial, étape 7).

σ_eff(T_i) est la vol lognormale qui, avec la courbe de forward, redonne la probabilité
avec smile de l'étape 5 :  N(−d1(σ_eff)) = P_smile(T_i)  (mesure risque-neutre EUR).

Attention, N(−d1) n'est pas monotone en σ quand K < F :
    ∂N(−d1)/∂σ = φ(d1) · d2 / σ,   d2 = [ln(F/K) − σ² T / 2] / (σ √T).
- K ≥ F : P décroît de 1 à 0 quand σ va de 0 à l'infini → une seule racine ;
- K < F : P croît de 0 jusqu'à son maximum en σ_crit = sqrt(2 ln(F/K) / T), puis décroît
  → deux racines (ou aucune si P_smile dépasse le maximum).
Choix de racine : la plus proche du σ_eff de la date précédente (σ_ATM pour la première
date). Avec un smile plat, on retrouve ainsi σ_eff = σ partout ; avec un skew, σ_eff reste
continu d'une date à l'autre.

La variance totale σ_eff² · T doit croître avec T (le moteur interpole la variance totale).
Sinon : avertissement et correction minimale, w_i = w_{i−1} · (1 + 1e-9), signalée avec
l'erreur de probabilité qu'elle induit.

Alternative robuste, exportée à titre d'information : la barrière effective K_eff(T_i)
qui, à la vol ATM, redonne P_smile. N(−d1) est toujours croissant en K, donc K_eff existe
et est unique.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Sequence

from scipy.optimize import brentq
from scipy.special import ndtri

from fx.courbes import CourbesFX
from fx.digitales import ResultatCoupon
from fx.gk import probabilite_eur_sous_barriere

SIGMA_MIN, SIGMA_MAX = 1e-6, 5.0
CORRECTION_RELATIVE = 1e-9


class ErreurVolEffective(ValueError):
    """Aucune vol lognormale ne redonne la probabilité visée."""


def _p(spot: float, k: float, t: float, sigma: float, df_dom: float, df_for: float) -> float:
    return float(probabilite_eur_sous_barriere(spot, k, t, sigma, df_dom, df_for))


def racines_sigma(spot: float, k: float, t: float, df_dom: float, df_for: float, p_cible: float) -> list[float]:
    """Toutes les vols σ ∈ [SIGMA_MIN, SIGMA_MAX] telles que N(−d1(σ)) = p_cible (1 ou 2 racines)."""
    if not 0.0 < p_cible < 1.0:
        raise ErreurVolEffective(f"Probabilité cible hors de ]0, 1[ : {p_cible}")

    def f(s: float) -> float:
        return _p(spot, k, t, s, df_dom, df_for) - p_cible

    f_ = spot * df_for / df_dom
    lfk = math.log(f_ / k)
    branches = [(SIGMA_MIN, SIGMA_MAX)] if lfk <= 0 else [
        (SIGMA_MIN, math.sqrt(2 * lfk / t)),
        (math.sqrt(2 * lfk / t), SIGMA_MAX),
    ]
    racines = []
    for a, b in branches:
        fa, fb = f(a), f(b)
        if fa == 0.0:
            racines.append(a)
        elif fa * fb < 0:
            racines.append(brentq(f, a, b, xtol=1e-15, rtol=1e-15, maxiter=500))
    if not racines:
        raise ErreurVolEffective(
            f"Aucune vol lognormale ne redonne P = {p_cible:.6f} à T = {t:.4f} (F/K = {f_ / k:.4f})"
        )
    return racines


def barriere_effective(spot: float, t: float, sigma: float, df_dom: float, df_for: float, p_cible: float) -> float:
    """K_eff tel que N(−d1(K_eff, σ)) = p_cible (N(−d1) croît strictement avec K)."""
    f_ = spot * df_for / df_dom
    srt = sigma * math.sqrt(t)
    # N(−d1) = p  ⇔  d1 = −Φ⁻¹(p)  ⇔  K = F · exp(Φ⁻¹(p) · σ√T + σ² T / 2)
    return f_ * math.exp(float(ndtri(p_cible)) * srt + 0.5 * srt * srt)


@dataclass(frozen=True)
class VolEffective:
    numero: int
    date: date
    t: float
    forward: float
    df_eur: float
    df_usd: float
    vol_atm: float
    p_smile: float
    sigma_eff: float
    nb_racines: int
    corrigee: bool
    p_reproduite: float  # N(−d1(σ_eff)) ; égale à p_smile sauf correction
    k_effective_vol_atm: float

    @property
    def variance_totale(self) -> float:
        return self.sigma_eff**2 * self.t


def vols_effectives(
    resultats: Sequence[ResultatCoupon], courbes: CourbesFX, k: float
) -> tuple[tuple[VolEffective, ...], tuple[str, ...]]:
    """σ_eff aux dates de coupon, contrôle de la variance totale ; renvoie aussi les avertissements."""
    sorties: list[VolEffective] = []
    avertissements: list[str] = []
    precedent: float | None = None
    w_prec = 0.0
    for r in sorted(resultats, key=lambda x: x.t):
        s, dfd, dff = courbes.spot, courbes.usd.df(r.t), courbes.eur.df(r.t)
        racines = racines_sigma(s, k, r.t, dfd, dff, r.p_smile)
        reference = precedent if precedent is not None else r.vol_atm
        sigma = min(racines, key=lambda x: abs(x - reference))
        corrigee = False
        if sigma * sigma * r.t <= w_prec:
            sigma_corr = math.sqrt(w_prec * (1 + CORRECTION_RELATIVE) / r.t)
            ecart = _p(s, k, r.t, sigma_corr, dfd, dff) - r.p_smile
            avertissements.append(
                f"Coupon {r.numero} : variance totale σ_eff²·T décroissante ({sigma * sigma * r.t:.6f} ≤ {w_prec:.6f}) ; "
                f"σ_eff relevé de {100 * sigma:.3f} % à {100 * sigma_corr:.3f} %, erreur de probabilité {100 * ecart:+.4f} pt"
            )
            sigma, corrigee = sigma_corr, True
        sorties.append(
            VolEffective(
                numero=r.numero,
                date=r.date,
                t=r.t,
                forward=courbes.forward(r.t),
                df_eur=dff,
                df_usd=dfd,
                vol_atm=r.vol_atm,
                p_smile=r.p_smile,
                sigma_eff=sigma,
                nb_racines=len(racines),
                corrigee=corrigee,
                p_reproduite=_p(s, k, r.t, sigma, dfd, dff),
                k_effective_vol_atm=barriere_effective(s, r.t, r.vol_atm, dfd, dff, r.p_smile),
            )
        )
        precedent, w_prec = sigma, sigma * sigma * r.t
    return tuple(sorties), tuple(avertissements)
