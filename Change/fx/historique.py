"""Analyse historique de la barrière sur les fixings BCE longs (série EXR.D.USD.EUR.SP00.A).

Question : historiquement, à quelle fréquence l'EUR/USD a-t-il monté de plus que l'écart
actuel entre spot et barrière (K / S − 1, environ 5,4 %) sur 6 mois, 1 an... ?
La probabilité "empirique" du coupon FX à l'horizon h est la fréquence des fenêtres où la
hausse reste sous ce seuil, à comparer à la probabilité risque-neutre de l'étape 5.

Méthode :
- seuls les fixings jusqu'à la date de valorisation sont utilisés (pas de biais d'anticipation) ;
- fenêtres glissantes quotidiennes : chaque fixing est un point de départ ; le fixing de fin
  est le dernier fixing publié à la date de départ + h mois, ou avant ;
- les fenêtres se chevauchent : le nombre de fenêtres indépendantes (durée / h) donne
  l'ordre de grandeur de l'information réelle, très faible au-delà de quelques années ;
- vol réalisée : écart-type des rendements logarithmiques quotidiens sur les N derniers
  fixings, annualisé par la racine du nombre de jours ouvrés par an.

Différence de fond : la fréquence historique est mesurée sous la probabilité réelle (avec la
tendance passée de l'EUR/USD), la probabilité risque-neutre intègre le forward (écart de
taux) et le smile. L'écart entre les deux n'est pas une erreur de prix.
"""

from __future__ import annotations

import csv
import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from fx.config import ErreurConfig, ajouter_mois

CLE_SERIE = "EXR.D.USD.EUR.SP00.A"


def lire_historique_sdmx(chemin: Path, cle: str = CLE_SERIE) -> tuple[tuple[date, float], ...]:
    """Lit le CSV SDMX du portail BCE (colonnes KEY, TIME_PERIOD, OBS_VALUE).

    Les observations vides (jours fériés, statut H) sont ignorées ; toute autre anomalie est une erreur.
    """
    if not chemin.exists():
        raise ErreurConfig(
            f"Historique BCE introuvable : {chemin}. À télécharger sur "
            f"https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?format=csvdata"
        )
    obs: dict[date, float] = {}
    with chemin.open(encoding="utf-8-sig", newline="") as f:
        lecteur = csv.DictReader(f)
        if lecteur.fieldnames is None or not {"KEY", "TIME_PERIOD", "OBS_VALUE"} <= set(lecteur.fieldnames):
            raise ErreurConfig(f"{chemin.name} : colonnes KEY, TIME_PERIOD, OBS_VALUE attendues (format SDMX CSV)")
        for ligne, row in enumerate(lecteur, start=2):
            if row["KEY"] != cle:
                raise ErreurConfig(f"{chemin.name} ligne {ligne} : série {row['KEY']!r} au lieu de {cle!r}")
            valeur = row["OBS_VALUE"].strip()
            if not valeur:
                continue
            d = date.fromisoformat(row["TIME_PERIOD"])
            x = float(valeur)
            if not math.isfinite(x) or x <= 0:
                raise ErreurConfig(f"{chemin.name} ligne {ligne} : fixing invalide {valeur!r}")
            if d in obs:
                raise ErreurConfig(f"{chemin.name} ligne {ligne} : date {d} en double")
            obs[d] = x
    return tuple(sorted(obs.items()))


def tronquer(fixings: Sequence[tuple[date, float]], jusqu_a: date) -> tuple[tuple[date, float], ...]:
    """Fixings publiés jusqu'à la date incluse."""
    return tuple((d, x) for d, x in fixings if d <= jusqu_a)


def rendements_glissants(
    fixings: Sequence[tuple[date, float]], mois: int, fin: date, debut: date | None = None
) -> tuple[tuple[date, date, float], ...]:
    """(départ, fin effective, rendement simple) pour chaque départ ≥ debut dont l'échéance ≤ fin."""
    dates = [d for d, _ in fixings]
    valeurs = [x for _, x in fixings]
    res = []
    for i, (d0, x0) in enumerate(fixings):
        if debut is not None and d0 < debut:
            continue
        echeance = ajouter_mois(d0, mois)
        if echeance > fin:
            break
        j = bisect_right(dates, echeance) - 1  # dernier fixing ≤ échéance
        if j <= i:
            continue
        res.append((d0, dates[j], valeurs[j] / x0 - 1.0))
    return tuple(res)


@dataclass(frozen=True)
class StatHorizon:
    """Statistiques empiriques pour un horizon donné."""

    mois: int
    seuil: float
    nb_fenetres: int
    nb_independantes: int
    frequence_hausse: float  # part des fenêtres où la hausse ≥ seuil (coupon perdu)
    rendement_median: float

    @property
    def p_empirique_coupon(self) -> float:
        return 1.0 - self.frequence_hausse


def statistique_horizon(
    fixings: Sequence[tuple[date, float]], mois: int, seuil: float, fin: date, debut: date | None = None
) -> StatHorizon:
    """Fréquence des hausses ≥ seuil sur h mois, en fenêtres glissantes."""
    r = rendements_glissants(fixings, mois, fin, debut)
    if not r:
        raise ValueError(f"Aucune fenêtre complète de {mois} mois avant le {fin}")
    rend = np.array([x for _, _, x in r])
    duree_mois = (fin.year - r[0][0].year) * 12 + (fin.month - r[0][0].month)
    return StatHorizon(
        mois=mois,
        seuil=seuil,
        nb_fenetres=len(r),
        nb_independantes=max(duree_mois // mois, 1),
        frequence_hausse=float(np.mean(rend >= seuil)),
        rendement_median=float(np.median(rend)),
    )


def vol_realisee(fixings: Sequence[tuple[date, float]], nb_jours: int, jours_par_an: int) -> float:
    """Vol réalisée annualisée sur les nb_jours derniers rendements logarithmiques."""
    if len(fixings) < nb_jours + 1:
        raise ValueError(f"Pas assez de fixings pour une vol sur {nb_jours} jours")
    x = np.log(np.array([v for _, v in fixings[-(nb_jours + 1):]]))
    return float(np.std(np.diff(x), ddof=1) * math.sqrt(jours_par_an))


def vols_realisees(
    fixings: Sequence[tuple[date, float]], fenetres: Mapping[str, int], jours_par_an: int
) -> dict[str, float]:
    return {p: vol_realisee(fixings, n, jours_par_an) for p, n in fenetres.items()}


# ---------------------------------------------------------------------------
# Analyse complète et sorties
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LigneHorizon:
    """Comparaison, à un horizon de coupon, des probabilités empiriques et risque-neutres."""

    numero: int
    mois: int
    complet: StatHorizon
    sous_periode: StatHorizon
    p_risque_neutre_smile: float
    p_risque_neutre_plate: float


@dataclass(frozen=True)
class AnalyseHistorique:
    debut: date
    fin: date
    debut_sous_periode: date
    nb_fixings: int
    seuil: float
    lignes: tuple[LigneHorizon, ...]
    vols_realisees: dict[str, float]
    vols_implicites: dict[str, float]
    vol_realisee_periode_complete: float


def analyser(
    fixings_longs: Sequence[tuple[date, float]],
    date_valo: date,
    spot: float,
    k: float,
    resultats_coupons: Sequence,  # ResultatCoupon de l'étape 5
    sous_periode_annees: int,
    fenetres_vol: Mapping[str, int],
    jours_par_an: int,
    vols_implicites: Mapping[str, float],
) -> AnalyseHistorique:
    """Analyse à chaque horizon de coupon (6, 12, ..., 84 mois), période complète et sous-période."""
    h = tronquer(fixings_longs, date_valo)
    seuil = k / spot - 1.0
    debut_sp = ajouter_mois(date_valo, -12 * sous_periode_annees)
    lignes = []
    for r in resultats_coupons:
        mois = round(12 * (r.date.year - date_valo.year) + (r.date.month - date_valo.month))
        lignes.append(
            LigneHorizon(
                numero=r.numero,
                mois=mois,
                complet=statistique_horizon(h, mois, seuil, date_valo),
                sous_periode=statistique_horizon(h, mois, seuil, date_valo, debut_sp),
                p_risque_neutre_smile=r.p_smile,
                p_risque_neutre_plate=r.p_plate,
            )
        )
    return AnalyseHistorique(
        debut=h[0][0],
        fin=h[-1][0],
        debut_sous_periode=debut_sp,
        nb_fixings=len(h),
        seuil=seuil,
        lignes=tuple(lignes),
        vols_realisees=vols_realisees(h, fenetres_vol, jours_par_an),
        vols_implicites=dict(vols_implicites),
        vol_realisee_periode_complete=vol_realisee(h, len(h) - 1, jours_par_an),
    )


def ecrire_csv(analyse: AnalyseHistorique, dossier: Path) -> tuple[Path, Path]:
    """historique_barriere_fx.csv et vol_historique_vs_implicite_fx.csv."""
    dossier.mkdir(parents=True, exist_ok=True)
    c1 = dossier / "historique_barriere_fx.csv"
    sp = f"{analyse.debut_sous_periode.year}"
    with c1.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow([
            "numero", "horizon_mois", "seuil_hausse_pct",
            f"nb_fenetres_{analyse.debut.year}", f"nb_independantes_{analyse.debut.year}",
            f"freq_hausse_sup_seuil_pct_{analyse.debut.year}", f"P_empirique_coupon_pct_{analyse.debut.year}",
            f"nb_fenetres_depuis_{sp}", f"nb_independantes_depuis_{sp}", f"P_empirique_coupon_pct_depuis_{sp}",
            "rendement_median_pct", "P_risque_neutre_smile_pct", "P_risque_neutre_plate_pct",
        ])
        for l in analyse.lignes:
            a, b = l.complet, l.sous_periode
            w.writerow([
                l.numero, l.mois, f"{100 * analyse.seuil:.3f}",
                a.nb_fenetres, a.nb_independantes, f"{100 * a.frequence_hausse:.2f}", f"{100 * a.p_empirique_coupon:.2f}",
                b.nb_fenetres, b.nb_independantes, f"{100 * b.p_empirique_coupon:.2f}",
                f"{100 * a.rendement_median:.2f}", f"{100 * l.p_risque_neutre_smile:.2f}", f"{100 * l.p_risque_neutre_plate:.2f}",
            ])
    c2 = dossier / "vol_historique_vs_implicite_fx.csv"
    with c2.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["pilier", "vol_realisee_pct", "vol_implicite_ATM_pct", "ecart_implicite_moins_realisee_pts"])
        for p, v in analyse.vols_realisees.items():
            imp = analyse.vols_implicites.get(p)
            w.writerow([p, f"{100 * v:.3f}", "" if imp is None else f"{100 * imp:.3f}",
                        "" if imp is None else f"{100 * (imp - v):.3f}"])
        w.writerow([f"{analyse.debut.year}-{analyse.fin.year}", f"{100 * analyse.vol_realisee_periode_complete:.3f}", "", ""])
    return c1, c2


_SURFACE, _TEXTE, _TEXTE_2, _GRILLE = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
_SERIES = ("#2a78d6", "#eb6834", "#1baf7a")


def tracer(analyse: AnalyseHistorique, chemin: Path) -> None:
    """P empirique (période complète, sous-période) et P risque-neutre (smile) par horizon."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ans = [l.mois / 12 for l in analyse.lignes]
    sp = analyse.debut_sous_periode.year
    series = (
        (f"Historique {analyse.debut.year}–{analyse.fin.year}", [100 * l.complet.p_empirique_coupon for l in analyse.lignes], "-"),
        (f"Historique depuis {sp}", [100 * l.sous_periode.p_empirique_coupon for l in analyse.lignes], "--"),
        ("Risque-neutre (smile, étape 5)", [100 * l.p_risque_neutre_smile for l in analyse.lignes], "-"),
    )
    courts = ("hist. complet", f"depuis {sp}", "risque-neutre")
    fig, ax = plt.subplots(figsize=(9, 5.2), dpi=100)
    fig.patch.set_facecolor(_SURFACE)
    ax.set_facecolor(_SURFACE)
    for (nom, ys, style), couleur in zip(series, _SERIES):
        ax.plot(ans, ys, color=couleur, lw=2, ls=style, marker="o", ms=4.5, mec=_SURFACE, mew=1.2, label=nom, zorder=3)
    derniers = sorted(((s[1][-1], court) for s, court in zip(series, courts)), reverse=True)
    pos = [derniers[0][0]]
    for y, _ in derniers[1:]:
        pos.append(min(y, pos[-1] - 3.2))
    for (y, court), yp in zip(derniers, pos):
        ax.text(ans[-1] + 0.15, yp, f"{y:.0f} % {court}", color=_TEXTE, fontsize=9, va="center")
    ax.set_xlim(0, ans[-1] + 1.6)
    ax.set_ylim(0, 100)
    ax.set_xticks(range(0, 8))
    ax.set_xlabel("Horizon du coupon (années)", color=_TEXTE_2)
    ax.set_ylabel("P(hausse de l'EUR/USD < seuil) (%)", color=_TEXTE_2)
    ax.set_title(
        f"Coupon FX : fréquence historique et probabilité risque-neutre (seuil +{100 * analyse.seuil:.1f} %)".replace(".", ","),
        color=_TEXTE, loc="left", fontsize=12,
    )
    peu = [l for l in analyse.lignes if l.complet.nb_independantes < 10]
    if peu:
        ax.text(
            0.0, -0.17,
            f"Fenêtres glissantes quotidiennes, très chevauchantes : moins de 10 périodes indépendantes dès "
            f"{peu[0].mois / 12:g} ans ; sur la sous-période, {peu[-1].sous_periode.nb_independantes} seulement "
            f"à {peu[-1].mois / 12:g} ans.",
            transform=ax.transAxes, color=_TEXTE_2, fontsize=8,
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
