"""Corrélation taux EUR / change, pour le moteur Monte Carlo commun.

Le sujet (section 4.3, étape 5 de l'ordre de calibration) simule X = 1/FX (EUR pour 1 USD) :
    ln X(t+Δ) = ln X(t) + (r_EUR − r_USD − σ²/2) Δ + σ √Δ Z_X,   Z_X = ρ Z_r + √(1 − ρ²) Z′,
où Z_r est le choc du taux court EUR (Hull-White 1 facteur) et ρ est « estimé sur l'historique ».

Estimateur : corrélation de Pearson entre les variations du taux EUR (Δr) et celles de
ln X = −ln FX (FX = fixing BCE, USD pour 1 EUR), sur les dates où les deux séries sont publiées,
avec des variations sur ``pas`` dates communes consécutives (1 = journalier, 5 = hebdomadaire,
sans chevauchement). Intervalle de confiance à 95 % par la transformation de Fisher.

Pourquoi un taux de swap court plutôt que l'€STR : en Hull-White 1 facteur, tous les taux de la
courbe bougent avec le même brownien Z_r, donc la variation d'un taux de swap est proportionnelle
à Z_r et corr(Δswap, Δln X) estime corr(Z_r, Z_X). L'€STR ne bouge vraiment qu'aux décisions de
la BCE : ses variations quotidiennes sont du bruit de fixing, sa corrélation est donnée pour comparaison.

Convention de signe : ρ porte sur X = EUR pour 1 USD (notation du sujet). Pour un moteur qui
simule FX = USD pour 1 EUR, prendre −ρ.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from fx.config import ConfigCorrelation, ErreurConfig, SerieTaux

# Tolérance sur le début d'une série : au-delà, la fenêtre demandée n'est pas couverte.
TOLERANCE_DEBUT_JOURS = 7
QUANTILE_95 = 1.959963984540054


# ---------------------------------------------------------------------------
# Lecture des séries de taux (en %)
# ---------------------------------------------------------------------------


def lire_estr_bce(chemin: Path) -> tuple[tuple[date, float], ...]:
    """CSV du portail BCE pour l'€STR : colonnes DATE (AAAA-MM-JJ), libellé, taux en %."""
    if not chemin.exists():
        raise ErreurConfig(f"Série de taux introuvable : {chemin}")
    obs: dict[date, float] = {}
    with chemin.open(encoding="utf-8-sig", newline="") as f:
        lignes = list(csv.reader(f))
    if not lignes or not lignes[0] or lignes[0][0] != "DATE" or len(lignes[0]) < 3:
        raise ErreurConfig(f"{chemin.name} : en-tête \"DATE\", \"TIME PERIOD\", <taux> attendu (export BCE de l'€STR)")
    for i, r in enumerate(lignes[1:], start=2):
        if len(r) < 3 or not r[2].strip():
            continue
        try:
            d, x = date.fromisoformat(r[0]), float(r[2])
        except ValueError:
            raise ErreurConfig(f"{chemin.name} ligne {i} : date ou taux illisible ({r[:3]})") from None
        if d in obs:
            raise ErreurConfig(f"{chemin.name} ligne {i} : date {d} en double")
        obs[d] = x
    return tuple(sorted(obs.items()))


def lire_taux_investing(chemin: Path) -> tuple[tuple[date, float], ...]:
    """CSV historique au format Investing.com en français : "Date" (JJ/MM/AAAA), "Dernier" (virgule décimale)."""
    if not chemin.exists():
        raise ErreurConfig(f"Série de taux introuvable : {chemin}")
    obs: dict[date, float] = {}
    with chemin.open(encoding="utf-8-sig", newline="") as f:
        lecteur = csv.DictReader(f)
        if lecteur.fieldnames is None or not {"Date", "Dernier"} <= set(lecteur.fieldnames):
            raise ErreurConfig(f"{chemin.name} : colonnes \"Date\" et \"Dernier\" attendues (format Investing.com)")
        for i, r in enumerate(lecteur, start=2):
            try:
                d = datetime.strptime(r["Date"].strip(), "%d/%m/%Y").date()
                x = float(r["Dernier"].strip().replace(",", "."))
            except ValueError:
                raise ErreurConfig(f"{chemin.name} ligne {i} : date ou taux illisible ({r['Date']!r}, {r['Dernier']!r})") from None
            if d in obs:
                raise ErreurConfig(f"{chemin.name} ligne {i} : date {d} en double")
            obs[d] = x
    return tuple(sorted(obs.items()))


LECTEURS = {"bce_estr": lire_estr_bce, "investing": lire_taux_investing}


def lire_serie(serie: SerieTaux) -> tuple[tuple[date, float], ...]:
    return LECTEURS[serie.format](serie.fichier)


# ---------------------------------------------------------------------------
# Estimation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EstimationCorrelation:
    """ρ = corr(Δ taux EUR, Δ ln X), X = EUR pour 1 USD, sur une fenêtre et un pas donnés."""

    serie: str
    libelle: str
    fenetre_annees: int
    pas_jours: int
    debut: date
    fin: date
    nb_variations: int
    rho: float
    ic95_bas: float
    ic95_haut: float
    part_variations_nulles: float  # part des variations du taux exactement nulles


def soustraire_annees(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year - n)
    except ValueError:  # 29 février
        return d.replace(year=d.year - n, day=28)


def estimer_correlation(
    fixings: Sequence[tuple[date, float]],
    taux: Sequence[tuple[date, float]],
    debut: date,
    fin: date,
    pas: int,
) -> tuple[int, float, float, float, float, date, date]:
    """(n, ρ, IC bas, IC haut, part de Δr nulles, première date, dernière date) sur [debut, fin]."""
    fx, tx = dict(fixings), dict(taux)
    dates = sorted(d for d in tx if d in fx and debut <= d <= fin)[::pas]
    if len(dates) < 10:
        raise ErreurConfig(f"Corrélation : {len(dates)} dates communes seulement entre {debut} et {fin}")
    dr = np.diff([tx[d] for d in dates])
    dx = -np.diff([math.log(fx[d]) for d in dates])  # ln X = −ln FX
    if np.std(dr) == 0 or np.std(dx) == 0:
        raise ErreurConfig(f"Corrélation : série constante entre {debut} et {fin}")
    rho = float(np.clip(np.corrcoef(dr, dx)[0, 1], -1.0, 1.0))
    n = len(dr)
    if abs(rho) > 1 - 1e-12:  # corrélation parfaite : intervalle réduit à un point
        bas = haut = rho
    else:
        z, se = math.atanh(rho), 1 / math.sqrt(n - 3)
        bas, haut = math.tanh(z - QUANTILE_95 * se), math.tanh(z + QUANTILE_95 * se)
    return n, rho, bas, haut, float(np.mean(dr == 0)), dates[0], dates[-1]


@dataclass(frozen=True)
class ResultatCorrelation:
    estimations: tuple[EstimationCorrelation, ...]
    retenue: EstimationCorrelation
    fenetres_non_couvertes: tuple[str, ...]  # "série : N ans (données depuis le ...)"
    justification: str


def calculer_correlations(
    config: ConfigCorrelation,
    fixings: Sequence[tuple[date, float]],
    date_valo: date,
    series: Mapping[str, Sequence[tuple[date, float]]] | None = None,
) -> ResultatCorrelation:
    """Toutes les combinaisons série × fenêtre × pas couvertes par les données, et l'estimation retenue.

    ``series`` permet de passer des séries déjà lues (tests) ; sinon elles sont lues dans les fichiers.
    """
    fixings = tuple((d, x) for d, x in fixings if d <= date_valo)
    estimations: list[EstimationCorrelation] = []
    non_couvertes: list[str] = []
    for nom, s in config.series.items():
        taux = tuple((d, x) for d, x in (series[nom] if series is not None else lire_serie(s)) if d <= date_valo)
        if not taux:
            raise ErreurConfig(f"Corrélation : série {nom} vide avant le {date_valo}")
        for an in config.fenetres_annees:
            debut = soustraire_annees(date_valo, an)
            if (taux[0][0] - debut).days > TOLERANCE_DEBUT_JOURS or (fixings[0][0] - debut).days > TOLERANCE_DEBUT_JOURS:
                non_couvertes.append(f"{s.libelle} : {an} an{'s' if an > 1 else ''} (données depuis le {taux[0][0].isoformat()})")
                continue
            for pas in config.pas_jours:
                n, rho, bas, haut, nulles, d0, d1 = estimer_correlation(fixings, taux, debut, date_valo, pas)
                estimations.append(EstimationCorrelation(
                    serie=nom, libelle=s.libelle, fenetre_annees=an, pas_jours=pas, debut=d0, fin=d1,
                    nb_variations=n, rho=rho, ic95_bas=bas, ic95_haut=haut, part_variations_nulles=nulles,
                ))
    r = config.retenue
    retenue = next(
        (e for e in estimations if (e.serie, e.fenetre_annees, e.pas_jours) == (r.serie, r.fenetre_annees, r.pas_jours)),
        None,
    )
    if retenue is None:
        raise ErreurConfig(
            f"Corrélation retenue ({r.serie}, {r.fenetre_annees} an(s), pas {r.pas_jours}) non calculable : "
            "fenêtre non couverte par les données"
        )
    return ResultatCorrelation(tuple(estimations), retenue, tuple(non_couvertes), r.justification)


def ecrire_csv(resultat: ResultatCorrelation, chemin: Path) -> None:
    """resultats_fx/correlation_taux_change.csv : une ligne par estimation."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["serie", "libelle", "fenetre_annees", "pas_jours", "debut", "fin", "nb_variations",
                    "rho_dr_dlnX", "ic95_bas", "ic95_haut", "part_variations_taux_nulles", "retenue"])
        for e in resultat.estimations:
            w.writerow([e.serie, e.libelle, e.fenetre_annees, e.pas_jours, e.debut.isoformat(), e.fin.isoformat(),
                        e.nb_variations, f"{e.rho:.4f}", f"{e.ic95_bas:.4f}", f"{e.ic95_haut:.4f}",
                        f"{e.part_variations_nulles:.3f}", "oui" if e is resultat.retenue else ""])
