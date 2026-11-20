"""Les 14 coupons digitaux EUR/USD : probabilité P(X_T < K) sous la mesure EUR et valeur.

Le coupon de la date T_i paie N · c (en EUR) si X_{T_i} < K (condition de change seule).
Sa valeur actualisée vaut N · c · DF_EUR(T_i) · P_EUR(X_{T_i} < K).

Trois versions côte à côte :
1. vol ATM plate : P = N(−d1(σ_ATM(T))) ;
2. σ(K) injectée dans N(−d1) : P = N(−d1(σ(K))). Fausse en présence de smile, gardée
   seulement pour montrer l'erreur : elle ignore le terme de pente du smile ;
3. smile par réplication (la bonne) : en USD, l'asset-or-nothing put vaut
       AoN(K) = K · ∂P/∂K − P(K),
   avec P le put vanille pricé à σ(K) (la dérivée totale inclut donc vega · σ'(K)),
   dérivée par différences centrées de pas relatif ~1e-4. Puis
       P_EUR(X_T < K) = AoN / (S · DF_EUR(T)).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
from numpy.typing import NDArray

from fx.config import Barriere, ConfigValo, echeancier_coupons
from fx.courbes import CourbesFX
from fx.gk import prix_vanille, probabilite_eur_sous_barriere
from fx.smile import SurfaceVol

PAS_RELATIF_DEFAUT = 1e-4

FonctionVol = Callable[[NDArray[np.float64]], NDArray[np.float64]]


def proba_replication(
    spot: float,
    strike: float,
    t: float,
    df_dom: float,
    df_for: float,
    vol_fn: FonctionVol,
    pas_relatif: float = PAS_RELATIF_DEFAUT,
) -> float:
    """P_EUR(X_T < K) par réplication : [K · ∂P/∂K − P(K)] / (S · DF_EUR), P pricé à σ(K).

    Dérivée par différences centrées (pas h = K · pas_relatif), améliorée par une
    extrapolation de Richardson : D = [4 D(h/2) − D(h)] / 3, d'erreur O(h⁴) au lieu de
    O(h²) (sans elle, l'erreur est de l'ordre de 1e-7 sur la probabilité avec h ~ 1e-4).
    """

    def put(k: float) -> float:
        return float(prix_vanille("put", spot, k, t, vol_fn(np.asarray(k)), df_dom, df_for))

    def centree(h: float) -> float:
        return (put(strike + h) - put(strike - h)) / (2 * h)

    h = strike * pas_relatif
    derivee = (4 * centree(h / 2) - centree(h)) / 3
    aon_usd = strike * derivee - put(strike)
    return aon_usd / (spot * df_for)


@dataclass(frozen=True)
class ResultatCoupon:
    """Résultats pour une date de coupon (probabilités en décimal, valeurs en EUR)."""

    numero: int
    date: date
    t: float
    forward: float
    df_eur: float
    vol_atm: float
    vol_k: float
    vol_extrapolee: bool
    p_plate: float
    p_vol_k: float
    p_smile: float
    montant_coupon_eur: float

    @property
    def valeur_plate(self) -> float:
        return self.montant_coupon_eur * self.df_eur * self.p_plate

    @property
    def valeur_vol_k(self) -> float:
        return self.montant_coupon_eur * self.df_eur * self.p_vol_k

    @property
    def valeur_smile(self) -> float:
        return self.montant_coupon_eur * self.df_eur * self.p_smile


def valoriser_coupon(
    numero: int,
    date_coupon: date,
    t: float,
    courbes: CourbesFX,
    surface: SurfaceVol,
    k: float,
    montant_coupon_eur: float,
    pas_relatif: float = PAS_RELATIF_DEFAUT,
) -> ResultatCoupon:
    """Les trois versions de la probabilité pour une date de coupon."""
    s, dfd, dff = courbes.spot, courbes.usd.df(t), courbes.eur.df(t)
    smile = surface.smile(t)
    vol_atm = smile.vols.vol_atm
    vol_k = float(smile.vol(k, surface.methode, surface.ordre_vv))
    p_smile = proba_replication(s, k, t, dfd, dff, lambda x: smile.vol(x, surface.methode, surface.ordre_vv), pas_relatif)
    if not 0.0 <= p_smile <= 1.0:
        raise ValueError(f"Probabilité par réplication hors de [0, 1] au coupon {numero} : {p_smile}")
    return ResultatCoupon(
        numero=numero,
        date=date_coupon,
        t=t,
        forward=courbes.forward(t),
        df_eur=dff,
        vol_atm=vol_atm,
        vol_k=vol_k,
        vol_extrapolee=surface.est_extrapole(t),
        p_plate=float(probabilite_eur_sous_barriere(s, k, t, vol_atm, dfd, dff)),
        p_vol_k=float(probabilite_eur_sous_barriere(s, k, t, vol_k, dfd, dff)),
        p_smile=p_smile,
        montant_coupon_eur=montant_coupon_eur,
    )


def valoriser_coupons(
    config: ConfigValo, courbes: CourbesFX, surface: SurfaceVol, barriere: Barriere
) -> tuple[ResultatCoupon, ...]:
    """Les 14 coupons FX de la note."""
    montant = config.nominal_eur * config.coupon_par_periode
    return tuple(
        valoriser_coupon(c.numero, c.date, c.t_annees, courbes, surface, barriere.niveau, montant)
        for c in echeancier_coupons(config)
    )


# ---------------------------------------------------------------------------
# Sorties
# ---------------------------------------------------------------------------

COLONNES_CSV = (
    "numero", "date", "T_annees", "forward", "DF_EUR", "vol_ATM_pct", "vol_K_pct", "vol_extrapolee",
    "P_plate_pct", "P_volK_injectee_pct", "P_smile_pct",
    "valeur_plate_EUR", "valeur_volK_injectee_EUR", "valeur_smile_EUR",
)


def ecrire_csv(resultats: Sequence[ResultatCoupon], chemin: Path) -> None:
    """resultats_fx/coupons_fx.csv : une ligne par date, plus une ligne de total des valeurs."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(COLONNES_CSV)
        for r in resultats:
            w.writerow([
                r.numero, r.date.isoformat(), f"{r.t:.6f}", f"{r.forward:.6f}", f"{r.df_eur:.6f}",
                f"{100 * r.vol_atm:.4f}", f"{100 * r.vol_k:.4f}", "oui" if r.vol_extrapolee else "non",
                f"{100 * r.p_plate:.4f}", f"{100 * r.p_vol_k:.4f}", f"{100 * r.p_smile:.4f}",
                f"{r.valeur_plate:.2f}", f"{r.valeur_vol_k:.2f}", f"{r.valeur_smile:.2f}",
            ])
        w.writerow([
            "total", "", "", "", "", "", "", "", "", "", "",
            f"{sum(r.valeur_plate for r in resultats):.2f}",
            f"{sum(r.valeur_vol_k for r in resultats):.2f}",
            f"{sum(r.valeur_smile for r in resultats):.2f}",
        ])


# Palette de référence (3 premières couleurs catégorielles, validées toutes paires).
_SURFACE, _TEXTE, _TEXTE_2, _GRILLE = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
_SERIES = ("#2a78d6", "#eb6834", "#1baf7a")


def tracer_probabilites(resultats: Sequence[ResultatCoupon], k: float, chemin: Path, t_dernier_pilier: float) -> None:
    """Graphique SVG de P(X_T < K) en fonction de T pour les trois versions."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ts = [r.t for r in resultats]
    series = (
        ("Vol ATM plate", [100 * r.p_plate for r in resultats]),
        ("σ(K) injectée dans N(−d1)", [100 * r.p_vol_k for r in resultats]),
        ("Smile, réplication", [100 * r.p_smile for r in resultats]),
    )
    fig, ax = plt.subplots(figsize=(9, 5.2), dpi=100)
    fig.patch.set_facecolor(_SURFACE)
    ax.set_facecolor(_SURFACE)
    t_max = max(ts) + 1.1  # marge à droite pour les étiquettes directes
    ax.axvspan(t_dernier_pilier, max(ts) + 0.25, color=_GRILLE, alpha=0.45, lw=0, zorder=0)
    ax.text(t_dernier_pilier + 0.08, 97, "vols extrapolées (hypothèse)", color=_TEXTE_2, fontsize=9, va="top")
    for (nom, ys), couleur in zip(series, _SERIES):
        ax.plot(ts, ys, color=couleur, lw=2, marker="o", ms=4.5, mec=_SURFACE, mew=1.2, label=nom, zorder=3)
    # Étiquettes directes à droite du dernier point, espacées pour éviter les chevauchements.
    derniers = sorted(((ys[-1], nom) for nom, ys in series), reverse=True)
    pos = [derniers[0][0]]
    for y, _ in derniers[1:]:
        pos.append(min(y, pos[-1] - 3.2))
    noms_courts = {series[0][0]: "plate", series[1][0]: "σ(K)", series[2][0]: "smile"}
    for (y, nom), yp in zip(derniers, pos):
        ax.text(ts[-1] + 0.15, yp, f"{y:.1f} % {noms_courts[nom]}".replace(".", ","), color=_TEXTE, fontsize=9, va="center")
    ax.set_xlim(0, t_max)
    ax.set_ylim(0, 100)
    ax.set_xticks(range(0, 8))
    ax.set_xlabel("Maturité du coupon (années)", color=_TEXTE_2)
    ax.set_ylabel("P(EUR/USD < K) sous la mesure EUR (%)", color=_TEXTE_2)
    ax.set_title(
        f"Probabilité de paiement de la condition de change, K = {k:.4f}".replace(".", ","),
        color=_TEXTE, loc="left", fontsize=12,
    )
    ax.grid(axis="y", color=_GRILLE, lw=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(_GRILLE)
    ax.tick_params(colors=_TEXTE_2)
    leg = ax.legend(loc="lower left", frameon=False, fontsize=9)
    for txt in leg.get_texts():
        txt.set_color(_TEXTE)
    fig.tight_layout()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(chemin, format="svg", facecolor=_SURFACE)
    plt.close(fig)
