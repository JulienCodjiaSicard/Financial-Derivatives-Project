"""Sensibilités de la valeur des 14 coupons FX (version smile) par bump-and-reprice.

Toutes les sensibilités sont en EUR, par date et au total, calculées par différences
centrées et rapportées pour le choc + (tailles dans config.sensibilites) :
- delta  = [V(S+) − V(S−)] / 2                       pour +1 % de spot ;
- gamma  = V(S+) − 2 V + V(S−)                        variation du delta pour +1 % de spot ;
- vega   = [V(σ+) − V(σ−)] / 2                        vols ATM de tous les piliers +1 point
                                                     (σ_25P et σ_25C suivent : RR et BF fixes) ;
- volga  = V(σ+) − 2 V + V(σ−)                        variation du vega pour +1 point ;
- vanna  = [V(S+σ+) − V(S+σ−) − V(S−σ+) + V(S−σ−)] / 4   variation du delta pour +1 point de vol ;
- RR, BF : RR25 (resp. BF25) de tous les piliers +0,5 (resp. +0,1) point ;
- écart de taux : taux zéro USD +10 bp en parallèle, courbe EUR inchangée (le forward
  monte, l'actualisation EUR ne bouge pas) ;
- K : barrière +0,01.

Hypothèses de choc : quand le spot bouge, les courbes (DF) sont inchangées, donc le forward
bouge en proportion, et les vols des piliers sont inchangées à delta constant (sticky delta).
"""

from __future__ import annotations

import csv
import dataclasses
import math
from dataclasses import dataclass
from pathlib import Path
import numpy as np
from numpy.typing import NDArray

from fx.config import Barriere, ConfigValo, MarcheFX, echeancier_coupons
from fx.courbes import CourbeActualisation, CourbesFX
from fx.digitales import valoriser_coupons
from fx.smile import SurfaceVol, VolsDelta, construire_surface

NOMS = ("delta", "gamma", "vega", "vanna", "volga", "rr25", "bf25", "ecart_taux", "barriere_k")


# ---------------------------------------------------------------------------
# Chocs
# ---------------------------------------------------------------------------


def choquer_courbes(courbes: CourbesFX, *, spot_rel: float = 0.0, ecart_taux: float = 0.0) -> CourbesFX:
    """Spot × (1 + spot_rel) ; DF_USD(t) × exp(−ecart_taux · t) (exact avec l'interpolation log-linéaire)."""
    usd = courbes.usd
    if ecart_taux:
        usd = dataclasses.replace(
            usd, facteurs=tuple(f * math.exp(-ecart_taux * t) for t, f in zip(usd.temps, usd.facteurs))
        )
    return CourbesFX(spot=courbes.spot * (1 + spot_rel), eur=courbes.eur, usd=usd)


def choquer_vols(v: VolsDelta, *, d_atm: float = 0.0, d_rr: float = 0.0, d_bf: float = 0.0) -> VolsDelta:
    """Choc en coordonnées de cotation : σ_25P/C = ATM + BF ∓ RR/2."""
    return VolsDelta(
        vol_25p=v.vol_25p + d_atm + d_bf - d_rr / 2,
        vol_atm=v.vol_atm + d_atm,
        vol_25c=v.vol_25c + d_atm + d_bf + d_rr / 2,
    )


def choquer_surface(
    surface: SurfaceVol,
    *,
    courbes: CourbesFX | None = None,
    d_atm: float = 0.0,
    d_rr: float = 0.0,
    d_bf: float = 0.0,
) -> SurfaceVol:
    """Nouvelle surface : vols de tous les piliers choquées, courbes éventuellement remplacées."""
    return dataclasses.replace(
        surface,
        courbes=courbes or surface.courbes,
        vols=tuple(choquer_vols(v, d_atm=d_atm, d_rr=d_rr, d_bf=d_bf) for v in surface.vols),
    )


def valeurs_par_date(config: ConfigValo, surface: SurfaceVol, k: float) -> NDArray[np.float64]:
    """Valeur actualisée (smile, EUR) de chacun des 14 coupons."""
    barriere = Barriere(niveau=k, date_pic=config.debut_barriere, nb_observations=0,
                        debut=config.debut_barriere, fin=config.fin_barriere)
    return np.array([r.valeur_smile for r in valoriser_coupons(config, surface.courbes, surface, barriere)])


# ---------------------------------------------------------------------------
# Sensibilités
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Sensibilites:
    """Valeur de base et sensibilités par date (tableaux de 14 valeurs, en EUR)."""

    base: NDArray[np.float64]
    par_nom: dict[str, NDArray[np.float64]]

    def total(self, nom: str) -> float:
        return float(np.sum(self.par_nom[nom]))


def calculer_sensibilites(config: ConfigValo, surface: SurfaceVol, k: float) -> Sensibilites:
    """Toutes les sensibilités par bump-and-reprice, chocs de la config."""
    c = config.chocs
    v0 = valeurs_par_date(config, surface, k)

    def v(*, spot: float = 0.0, atm: float = 0.0, rr: float = 0.0, bf: float = 0.0,
          ecart: float = 0.0, dk: float = 0.0) -> NDArray[np.float64]:
        courbes = choquer_courbes(surface.courbes, spot_rel=spot, ecart_taux=ecart)
        s = choquer_surface(surface, courbes=courbes, d_atm=atm, d_rr=rr, d_bf=bf)
        return valeurs_par_date(config, s, k + dk)

    s_up, s_dn = v(spot=c.spot_rel), v(spot=-c.spot_rel)
    a_up, a_dn = v(atm=c.vol_atm), v(atm=-c.vol_atm)
    croise = {
        (i, j): v(spot=i * c.spot_rel, atm=j * c.vol_atm) for i in (1, -1) for j in (1, -1)
    }
    par_nom = {
        "delta": (s_up - s_dn) / 2,
        "gamma": s_up - 2 * v0 + s_dn,
        "vega": (a_up - a_dn) / 2,
        "vanna": (croise[1, 1] - croise[1, -1] - croise[-1, 1] + croise[-1, -1]) / 4,
        "volga": a_up - 2 * v0 + a_dn,
        "rr25": (v(rr=c.rr25) - v(rr=-c.rr25)) / 2,
        "bf25": (v(bf=c.bf25) - v(bf=-c.bf25)) / 2,
        "ecart_taux": (v(ecart=c.ecart_taux) - v(ecart=-c.ecart_taux)) / 2,
        "barriere_k": (v(dk=c.barriere_k) - v(dk=-c.barriere_k)) / 2,
    }
    return Sensibilites(base=v0, par_nom=par_nom)


def scenario_bf(config: ConfigValo, marche: MarcheFX, courbes: CourbesFX, k: float, bf_pct: float) -> NDArray[np.float64]:
    """Valeurs par date si le BF supposé valait bf_pct (seuls les piliers à BF supposé changent)."""
    piliers = tuple(
        dataclasses.replace(p, bf25_pct=bf_pct) if p.bf25_suppose else p for p in marche.piliers
    )
    surface = construire_surface(dataclasses.replace(marche, piliers=piliers), courbes, config)
    return valeurs_par_date(config, surface, k)


# ---------------------------------------------------------------------------
# Sortie
# ---------------------------------------------------------------------------

def entetes(config: ConfigValo) -> dict[str, str]:
    """Libellés de colonnes avec les tailles de chocs réellement utilisées."""
    c = config.chocs

    def f(x: float) -> str:
        return f"{x:g}".replace(".", ",")

    return {
        "delta": f"delta_spot_+{f(100 * c.spot_rel)}pct_EUR",
        "gamma": f"gamma_spot_{f(100 * c.spot_rel)}pct_EUR",
        "vega": f"vega_ATM_+{f(100 * c.vol_atm)}pt_EUR",
        "vanna": f"vanna_{f(100 * c.spot_rel)}pct_{f(100 * c.vol_atm)}pt_EUR",
        "volga": f"volga_{f(100 * c.vol_atm)}pt_EUR",
        "rr25": f"RR25_+{f(100 * c.rr25)}pt_EUR",
        "bf25": f"BF25_+{f(100 * c.bf25)}pt_EUR",
        "ecart_taux": f"ecart_taux_USD-EUR_+{f(10_000 * c.ecart_taux)}bp_EUR",
        "barriere_k": f"barriere_K_+{f(c.barriere_k)}_EUR",
    }


def ecrire_csv(config: ConfigValo, sens: Sensibilites, chemin: Path) -> None:
    """resultats_fx/sensibilites_fx.csv : une ligne par date, plus le total."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    noms = entetes(config)
    dates = echeancier_coupons(config)
    with chemin.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["numero", "date", "valeur_smile_EUR", *(noms[n] for n in NOMS)])
        for i, d in enumerate(dates):
            w.writerow([d.numero, d.date.isoformat(), f"{sens.base[i]:.2f}", *(f"{sens.par_nom[n][i]:.2f}" for n in NOMS)])
        w.writerow(["total", "", f"{float(np.sum(sens.base)):.2f}", *(f"{sens.total(n):.2f}" for n in NOMS)])
