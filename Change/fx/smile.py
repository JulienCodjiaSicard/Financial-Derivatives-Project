"""Smile de volatilité EUR/USD : piliers par maturité, interpolation en strike et en temps.

Piliers (vols en décimal) :
    σ_25P = ATM + BF − RR/2,   σ_25C = ATM + BF + RR/2   (RR = σ_25C − σ_25P).
Approximation à signaler dans les limites : le BF coté par le marché est un "market
strangle" (un seul strangle pricé à la vol ATM + BF), on le traite ici comme un "smile
strangle". L'écart entre les deux est faible quand le RR est petit (|RR| < 1 point ici).

Strikes des piliers : fx.gk.strikes_piliers (delta spot jusqu'à 1 an, delta forward
au-delà, non ajusté de la prime ; ATM delta-neutre).

Interpolation en strike :
- Vanna-Volga, approximation de Castagna-Mercurio (Risk, 2007). Ordre 1 : interpolation
  de Lagrange des trois vols en ln K. Ordre 2 :
      σ(K) = σ_ATM + (2 σ_ATM D1 + D2) / (σ_ATM + sqrt(σ_ATM² + d1 d2 (2 σ_ATM D1 + D2)))
  (forme algébriquement identique à celle de l'article, mais stable quand d1 d2 → 0).
  Si la racine est négative, l'ordre 2 est instable et une erreur est levée.
- SABR (Hagan et al., 2002), β fixé, α, ρ, ν calibrés sur les trois piliers.

Interpolation en temps : pour chacun des trois piliers de delta (25P, ATM, 25C), la
variance totale σ² T est interpolée linéairement en T ("à delta constant"). Avant le
premier pilier : vol constante. Au-delà du dernier pilier coté : vol constante,
seulement si l'hypothèse 'vols_au_dela_dernier_pilier' est déclarée dans la config.

Delta tenu constant : jusqu'au seuil (1 an), delta spot 0,25. Au-delà, delta forward.
Un pilier coté en delta spot (≤ 1 an) correspond au delta forward 0,25 / DF_EUR(T_i) ;
c'est ce delta forward qui est tenu constant après lui (interpolé linéairement en T
jusqu'au pilier suivant s'il existe). Sans cela, réutiliser les vols 1Y (cotées en delta
spot) avec un delta forward 0,25 décale les strikes des ailes au passage de 1 an et crée
un petit arbitrage calendaire dans les ailes.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from functools import cached_property, lru_cache

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.optimize import least_squares

from fx.config import ConfigValo, MarcheFX
from fx.courbes import CourbesFX
from fx.gk import convention_delta, prix_vanille, strikes_piliers

_EPS_T = 1e-12


class ErreurSmile(ValueError):
    """Smile incalculable : instabilité numérique ou arbitrage dans les cotations."""


# ---------------------------------------------------------------------------
# Piliers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VolsDelta:
    """Vols (décimal) des trois piliers : 25Δ put, ATM, 25Δ call."""

    vol_25p: float
    vol_atm: float
    vol_25c: float

    def comme_tuple(self) -> tuple[float, float, float]:
        return self.vol_25p, self.vol_atm, self.vol_25c


def vols_depuis_cotations(atm_pct: float, rr25_pct: float, bf25_pct: float) -> VolsDelta:
    """σ_25P = ATM + BF − RR/2 ; σ_25C = ATM + BF + RR/2 (entrées en points de vol)."""
    atm, rr, bf = atm_pct / 100, rr25_pct / 100, bf25_pct / 100
    v = VolsDelta(vol_25p=atm + bf - rr / 2, vol_atm=atm, vol_25c=atm + bf + rr / 2)
    if min(v.comme_tuple()) <= 0:
        raise ErreurSmile(f"Vol de pilier négative ou nulle : {v}")
    return v


# ---------------------------------------------------------------------------
# SABR
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParametresSABR:
    alpha: float
    beta: float
    rho: float
    nu: float
    residu_max: float  # écart maximal aux trois vols de pilier après calibration


def vol_sabr(strike: ArrayLike, f: float, t: float, alpha: float, beta: float, rho: float, nu: float) -> NDArray[np.float64]:
    """Vol lognormale implicite SABR, formule de Hagan et al. (2002)."""
    k = np.asarray(strike, dtype=float)
    lfk = np.log(f / k)
    fkb = (f * k) ** ((1 - beta) / 2)
    z = nu / alpha * fkb * lfk
    # z / x(z), avec son développement limité près de z = 0 (ATM)
    petit = np.abs(z) < 1e-7
    z_sur = np.where(petit, 1.0, z)
    x = np.log((np.sqrt(1 - 2 * rho * z_sur + z_sur**2) + z_sur - rho) / (1 - rho))
    z_sur_x = np.where(petit, 1.0 - 0.5 * rho * z, z_sur / x)
    un_b = 1 - beta
    denom = fkb * (1 + un_b**2 / 24 * lfk**2 + un_b**4 / 1920 * lfk**4)
    correction = 1 + (un_b**2 / 24 * alpha**2 / fkb**2 + rho * beta * nu * alpha / (4 * fkb) + (2 - 3 * rho**2) / 24 * nu**2) * t
    return alpha / denom * z_sur_x * correction


def calibrer_sabr(strikes: tuple[float, float, float], vols: tuple[float, float, float], f: float, t: float, beta: float) -> ParametresSABR:
    """Calibre α, ρ, ν (β fixé) sur trois couples (strike, vol)."""
    k = np.asarray(strikes)
    v = np.asarray(vols)

    def residus(x: NDArray[np.float64]) -> NDArray[np.float64]:
        return vol_sabr(k, f, t, x[0], beta, x[1], x[2]) - v

    x0 = np.array([vols[1] * f ** (1 - beta), -0.1, 0.5])
    sol = least_squares(
        residus, x0, bounds=([1e-8, -0.9999, 1e-8], [10.0, 0.9999, 20.0]), xtol=1e-15, ftol=1e-15, gtol=1e-15
    )
    return ParametresSABR(
        alpha=float(sol.x[0]), beta=beta, rho=float(sol.x[1]), nu=float(sol.x[2]),
        residu_max=float(np.max(np.abs(residus(sol.x)))),
    )


# ---------------------------------------------------------------------------
# Smile à une maturité
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SmileMaturite:
    """Smile à la maturité t : trois piliers (strike, vol) et interpolation en strike."""

    t: float
    spot: float
    df_dom: float  # DF_USD(t)
    df_for: float  # DF_EUR(t)
    vols: VolsDelta
    strikes: tuple[float, float, float]
    convention: str
    sabr_beta: float = 1.0

    @property
    def forward(self) -> float:
        return self.spot * self.df_for / self.df_dom

    def _poids(self, strike: ArrayLike) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
        """Poids de Lagrange en ln K (somme = 1), qui valent 1 au pilier correspondant."""
        k = np.asarray(strike, dtype=float)
        k1, k2, k3 = self.strikes
        y1 = np.log(k2 / k) * np.log(k3 / k) / (math.log(k2 / k1) * math.log(k3 / k1))
        y2 = np.log(k / k1) * np.log(k3 / k) / (math.log(k2 / k1) * math.log(k3 / k2))
        y3 = np.log(k / k1) * np.log(k / k2) / (math.log(k3 / k1) * math.log(k3 / k2))
        return y1, y2, y3

    def _d1d2(self, strike: ArrayLike) -> NDArray[np.float64]:
        """d1 · d2 calculés à la vol ATM (notation de Castagna-Mercurio)."""
        s2rt = self.vols.vol_atm * math.sqrt(self.t)
        d1 = (np.log(self.forward / np.asarray(strike, dtype=float)) + 0.5 * s2rt**2) / s2rt
        return d1 * (d1 - s2rt)

    def vol_vv1(self, strike: ArrayLike) -> NDArray[np.float64]:
        """Vanna-Volga, ordre 1."""
        y1, y2, y3 = self._poids(strike)
        s1, s2, s3 = self.vols.comme_tuple()
        return y1 * s1 + y2 * s2 + y3 * s3

    def vol_vv2(self, strike: ArrayLike) -> NDArray[np.float64]:
        """Vanna-Volga, ordre 2 ; ErreurSmile si la racine devient négative (instable)."""
        y1, _, y3 = self._poids(strike)
        s1, s2, s3 = self.vols.comme_tuple()
        k1, _, k3 = self.strikes
        d_1 = self.vol_vv1(strike) - s2
        d_2 = y1 * self._d1d2(k1) * (s1 - s2) ** 2 + y3 * self._d1d2(k3) * (s3 - s2) ** 2
        num = 2 * s2 * d_1 + d_2
        radicande = s2**2 + self._d1d2(strike) * num
        if np.any(radicande < 0):
            raise ErreurSmile(f"Vanna-Volga ordre 2 instable à T = {self.t:.4f} (racine négative) : utiliser l'ordre 1")
        vol = s2 + num / (s2 + np.sqrt(radicande))
        if np.any(vol <= 0):
            raise ErreurSmile(f"Vanna-Volga ordre 2 : vol négative à T = {self.t:.4f}")
        return vol

    @cached_property
    def sabr(self) -> ParametresSABR:
        return calibrer_sabr(self.strikes, self.vols.comme_tuple(), self.forward, self.t, self.sabr_beta)

    def vol_sabr(self, strike: ArrayLike) -> NDArray[np.float64]:
        p = self.sabr
        return vol_sabr(strike, self.forward, self.t, p.alpha, p.beta, p.rho, p.nu)

    def vol(self, strike: ArrayLike, methode: str = "vanna_volga", ordre: int = 2) -> NDArray[np.float64]:
        """Vol implicite au strike selon la méthode choisie."""
        if methode == "sabr":
            return self.vol_sabr(strike)
        if methode != "vanna_volga":
            raise ValueError(f"méthode de smile inconnue : {methode!r}")
        if ordre == 1:
            return self.vol_vv1(strike)
        if ordre == 2:
            return self.vol_vv2(strike)
        raise ValueError(f"ordre Vanna-Volga inconnu : {ordre!r}")


def construire_smile(
    t: float,
    spot: float,
    df_dom: float,
    df_for: float,
    vols: VolsDelta,
    seuil_delta_spot: float,
    sabr_beta: float = 1.0,
    delta_pilier: float = 0.25,
    convention: str | None = None,
) -> SmileMaturite:
    """Calcule les strikes des trois piliers puis le smile à la maturité t."""
    conv = convention or convention_delta(t, seuil_delta_spot)
    strikes = strikes_piliers(spot, t, df_dom, df_for, *vols.comme_tuple(), seuil_delta_spot, delta_pilier, conv)
    if not strikes[0] < strikes[1] < strikes[2]:
        raise ErreurSmile(f"Strikes de piliers non ordonnés à T = {t:.4f} : {strikes}")
    return SmileMaturite(
        t=t, spot=spot, df_dom=df_dom, df_for=df_for, vols=vols, strikes=strikes,
        convention=conv, sabr_beta=sabr_beta,
    )


def densite_implicite(smile: SmileMaturite, strike: ArrayLike, methode: str = "vanna_volga", ordre: int = 2, pas_relatif: float = 1e-3) -> NDArray[np.float64]:
    """Densité risque-neutre USD de X_T : ∂²C/∂K² / DF_USD, par différences centrées.

    Négative = arbitrage papillon.
    """
    k = np.asarray(strike, dtype=float)
    h = k * pas_relatif

    def c(x: NDArray[np.float64]) -> NDArray[np.float64]:
        return prix_vanille("call", smile.spot, x, smile.t, smile.vol(x, methode, ordre), smile.df_dom, smile.df_for)

    return (c(k + h) - 2 * c(k) + c(k - h)) / (h * h) / smile.df_dom


# ---------------------------------------------------------------------------
# Surface : interpolation en temps
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SurfaceVol:
    """Piliers cotés par maturité et règles d'interpolation en temps et en strike."""

    courbes: CourbesFX
    piliers: tuple[str, ...]
    temps: tuple[float, ...]
    vols: tuple[VolsDelta, ...]
    extrapolation: str | None
    seuil_delta_spot: float
    methode: str
    ordre_vv: int
    sabr_beta: float

    def __post_init__(self) -> None:
        if not self.temps or len(self.temps) != len(self.vols):
            raise ErreurSmile("Surface : au moins un pilier, et autant de vols que de maturités")
        if any(b <= a for a, b in zip(self.temps, self.temps[1:])):
            raise ErreurSmile("Surface : maturités strictement croissantes attendues")
        # Pas d'arbitrage calendaire dans les cotations : variance totale croissante à delta constant.
        for i, nom in enumerate(("25P", "ATM", "25C")):
            w = [v.comme_tuple()[i] ** 2 * t for v, t in zip(self.vols, self.temps)]
            for (p0, w0), (p1, w1) in zip(zip(self.piliers, w), zip(self.piliers[1:], w[1:])):
                if w1 <= w0:
                    raise ErreurSmile(f"Arbitrage calendaire coté : variance totale {nom} décroissante entre {p0} et {p1}")

    @property
    def t_dernier_pilier(self) -> float:
        return self.temps[-1]

    def est_extrapole(self, t: float) -> bool:
        return t > self.temps[-1] + _EPS_T

    def vols_delta(self, t: float) -> VolsDelta:
        """Vols des trois piliers de delta à la maturité t (variance totale linéaire en t)."""
        if t <= 0:
            raise ValueError("t doit être strictement positif")
        if t <= self.temps[0]:
            return self.vols[0]
        if self.est_extrapole(t):
            if self.extrapolation != "vol_constante":
                raise ErreurSmile(
                    f"T = {t:.4f} au-delà du dernier pilier coté ({self.piliers[-1]}) sans hypothèse "
                    "d'extrapolation dans la config"
                )
            return self.vols[-1]
        i = min(bisect_right(self.temps, t) - 1, len(self.temps) - 2)
        t0, t1 = self.temps[i], self.temps[i + 1]
        w = (t - t0) / (t1 - t0)
        res = []
        for a, b in zip(self.vols[i].comme_tuple(), self.vols[i + 1].comme_tuple()):
            var = (1 - w) * a * a * t0 + w * b * b * t1
            res.append(math.sqrt(var / t))
        return VolsDelta(*res)

    def _delta_forward_pilier(self, i: int) -> float:
        """Delta forward équivalent au 25Δ du pilier i (0,25 / DF_EUR si coté en delta spot)."""
        t = self.temps[i]
        if convention_delta(t, self.seuil_delta_spot) == "spot":
            return 0.25 / self.courbes.eur.df(t)
        return 0.25

    def convention_et_delta(self, t: float) -> tuple[str, float]:
        """Convention et niveau du delta des ailes tenus constants à la maturité t."""
        if convention_delta(t, self.seuil_delta_spot) == "spot":
            return "spot", 0.25
        i = bisect_right(self.temps, t) - 1  # dernier pilier ≤ t (existe : t > seuil ≥ ... )
        if i < 0:
            return "forward", 0.25
        if i >= len(self.temps) - 1:
            return "forward", self._delta_forward_pilier(len(self.temps) - 1)
        t0, t1 = self.temps[i], self.temps[i + 1]
        w = (t - t0) / (t1 - t0)
        return "forward", (1 - w) * self._delta_forward_pilier(i) + w * self._delta_forward_pilier(i + 1)

    @lru_cache(maxsize=4096)  # noqa: B019 : surface immuable ; garde la calibration SABR par maturité
    def smile(self, t: float) -> SmileMaturite:
        c = self.courbes
        conv, d = self.convention_et_delta(t)
        return construire_smile(
            t, c.spot, c.usd.df(t), c.eur.df(t), self.vols_delta(t), self.seuil_delta_spot, self.sabr_beta, d, conv
        )

    def vol(self, t: float, strike: ArrayLike, methode: str | None = None, ordre: int | None = None) -> NDArray[np.float64]:
        """Vol implicite σ(t, K) ; méthode et ordre de la config par défaut."""
        return self.smile(t).vol(strike, methode or self.methode, ordre or self.ordre_vv)


def construire_surface(marche: MarcheFX, courbes: CourbesFX, config: ConfigValo) -> SurfaceVol:
    """Surface à partir des piliers validés (BF éventuellement supposé) et de la config."""
    return SurfaceVol(
        courbes=courbes,
        piliers=tuple(p.pilier for p in marche.piliers),
        temps=tuple(p.maturite_annees for p in marche.piliers),
        vols=tuple(vols_depuis_cotations(p.atm_pct, p.rr25_pct, p.bf25_pct) for p in marche.piliers),
        extrapolation=marche.extrapolation_vol,
        seuil_delta_spot=config.delta_spot_jusqu_a_annees,
        methode=config.methode_smile,
        ordre_vv=config.ordre_vanna_volga,
        sabr_beta=config.sabr_beta,
    )
