"""Courbes d'actualisation EUR et USD, forward EUR/USD.

Conventions (voir aussi config.conventions.interpolation_courbes) :
- temps t en années selon config.base_calcul (ACT/365F) depuis la date de valorisation ;
- échéance d'un pilier = date de valorisation + ténor. Simplification : pas de décalage
  spot T+2 ni d'ajustement jours ouvrés ;
- swap OIS au pair : paiement unique jusqu'à 1 an, paiements annuels au-delà ; fraction
  d'intérêt de la jambe fixe selon la base de la courbe (ACT/360). Avec une courbe unique
  (actualisation = projection, sans spread), la jambe variable OIS vaut 1 − DF(T_n), donc
  S · Σ α_i · DF(t_i) = 1 − DF(T_n) ;
- taux de swap aux dates annuelles intermédiaires non cotées (4Y, 6Y...) : interpolation
  linéaire en maturité entre les piliers cotés voisins ;
- DF entre les nœuds : interpolation log-linéaire en t (forwards constants par morceaux) ;
  aucune extrapolation au-delà du dernier nœud ;
- courbe de type "zero" : taux zéro en composition continue sur la base de la courbe ;
- courbe de type "df" : facteurs d'actualisation repris tels quels du sous-groupe taux
  (output/curve_eur.csv et curve_usd.csv, grille mensuelle). Leurs maturités sont des
  années ; elles sont utilisées directement comme temps t, sans conversion de dates.
  L'interpolation log-linéaire entre leurs nœuds redonne exactement leur P_at (même
  interpolation, nœuds alignés sur leur courbe) ;
- X = USD pour 1 EUR : F(T) = S · DF_EUR(T) / DF_USD(T)  (parité des taux d'intérêt).
"""

from __future__ import annotations

import math
import re
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

from fx.config import (
    ConfigValo,
    Courbe,
    ErreurConfig,
    MarcheFX,
    PointsDeTerme,
    ajouter_mois,
    fraction_annee,
)

_TENOR = re.compile(r"^(\d+)([DWMY])$")
_TOL_T = 1e-12


# ---------------------------------------------------------------------------
# Ténors
# ---------------------------------------------------------------------------


def lire_tenor(pilier: str) -> tuple[int, str]:
    """'3M' -> (3, 'M'). Unités acceptées : D, W, M, Y."""
    m = _TENOR.match(pilier.strip().upper())
    if m is None or int(m.group(1)) <= 0:
        raise ErreurConfig(f"Ténor illisible : {pilier!r} (attendu par ex. 1W, 3M, 2Y)")
    return int(m.group(1)), m.group(2)


def mois_du_tenor(pilier: str) -> int | None:
    """Nombre de mois d'un ténor en M ou Y ; None pour un ténor en jours ou semaines."""
    n, unite = lire_tenor(pilier)
    return n if unite == "M" else 12 * n if unite == "Y" else None


def date_echeance(date_valo: date, pilier: str) -> date:
    """Date de valorisation + ténor (calendrier civil, sans ajustement jours ouvrés)."""
    n, unite = lire_tenor(pilier)
    if unite == "D":
        return date_valo + timedelta(days=n)
    if unite == "W":
        return date_valo + timedelta(weeks=n)
    return ajouter_mois(date_valo, n if unite == "M" else 12 * n)


# ---------------------------------------------------------------------------
# Courbe d'actualisation
# ---------------------------------------------------------------------------


def df_log_lineaire(temps: Sequence[float], facteurs: Sequence[float], t: float) -> float:
    """DF(t) par interpolation linéaire de ln DF entre les nœuds ; erreur hors [0, dernier nœud]."""
    if t < -_TOL_T:
        raise ValueError(f"Temps négatif : {t}")
    if t > temps[-1] + _TOL_T:
        raise ValueError(f"t = {t:.4f} au-delà du dernier nœud de la courbe ({temps[-1]:.4f}) : pas d'extrapolation")
    t = min(max(t, 0.0), temps[-1])
    i = bisect_right(temps, t) - 1
    if i >= len(temps) - 1:
        return facteurs[-1]
    t0, t1 = temps[i], temps[i + 1]
    w = (t - t0) / (t1 - t0)
    return math.exp((1 - w) * math.log(facteurs[i]) + w * math.log(facteurs[i + 1]))


@dataclass(frozen=True)
class CourbeActualisation:
    """Facteurs d'actualisation aux nœuds (t = 0 inclus, DF(0) = 1)."""

    devise: str
    temps: tuple[float, ...]
    facteurs: tuple[float, ...]
    piliers_interpoles: tuple[str, ...]
    source: str
    hypothese: bool

    def __post_init__(self) -> None:
        if len(self.temps) != len(self.facteurs) or len(self.temps) < 2:
            raise ValueError("Courbe : au moins un nœud en plus de t = 0, et autant de DF que de temps")
        if self.temps[0] != 0.0 or self.facteurs[0] != 1.0:
            raise ValueError("Courbe : le premier nœud doit être (t = 0, DF = 1)")
        if any(b <= a for a, b in zip(self.temps, self.temps[1:])):
            raise ValueError("Courbe : temps strictement croissants attendus")
        if any(not (f > 0 and math.isfinite(f)) for f in self.facteurs):
            raise ValueError("Courbe : facteurs d'actualisation strictement positifs attendus")

    @property
    def t_max(self) -> float:
        return self.temps[-1]

    def df(self, t: float) -> float:
        """Facteur d'actualisation à t années."""
        return df_log_lineaire(self.temps, self.facteurs, t)

    def taux_zero(self, t: float) -> float:
        """Taux zéro continu (décimal) : −ln DF(t) / t ; à t = 0, limite à droite (premier segment)."""
        if t <= _TOL_T:
            return -math.log(self.facteurs[1]) / self.temps[1]
        return -math.log(self.df(t)) / t


def _taux_swap_complets(
    courbe: Courbe,
) -> tuple[list[tuple[str, float]], tuple[str, ...]]:
    """Piliers cotés + taux interpolés linéairement aux dates annuelles manquantes.

    Renvoie [(pilier, taux décimal)] et la liste des piliers interpolés.
    """
    cotes: dict[str, float] = {}
    for p in courbe.points:
        if p.pilier is None:
            raise ErreurConfig(f"Courbe {courbe.devise} de swaps : chaque point doit porter son 'pilier' (1M, 2Y...)")
        cotes[p.pilier.strip().upper()] = p.valeur / 100.0
    # Abscisse d'interpolation : maturité nominale en années.
    abscisses: list[tuple[float, float]] = []
    for pilier, taux in cotes.items():
        n, unite = lire_tenor(pilier)
        x = {"D": n / 365.0, "W": 7 * n / 365.0, "M": n / 12.0, "Y": float(n)}[unite]
        abscisses.append((x, taux))
    abscisses.sort()

    mois_cotes = {mois_du_tenor(p) for p in cotes}
    requis: set[int] = set()
    for pilier in cotes:
        mois = mois_du_tenor(pilier)
        if mois is not None and mois > 12:
            if mois % 12:
                raise ErreurConfig(
                    f"Courbe {courbe.devise} : swap {pilier} au-delà de 1 an non multiple de 12 mois, non géré"
                )
            requis.update(range(12, mois, 12))
    interpoles: list[str] = []
    resultat = list(cotes.items())
    for mois in sorted(requis - mois_cotes):
        x = mois / 12.0
        bas = max((a for a in abscisses if a[0] < x), default=None)
        haut = min((a for a in abscisses if a[0] > x), default=None)
        if bas is None or haut is None:  # impossible : x est entre 1 an et un pilier coté
            raise ErreurConfig(f"Courbe {courbe.devise} : pas de voisins pour interpoler {mois // 12}Y")
        w = (x - bas[0]) / (haut[0] - bas[0])
        pilier = f"{mois // 12}Y"
        resultat.append((pilier, (1 - w) * bas[1] + w * haut[1]))
        interpoles.append(pilier)
    return resultat, tuple(interpoles)


def bootstrap_courbe(courbe: Courbe, date_valo: date, base_temps: str) -> CourbeActualisation:
    """Construit les DF d'une devise : taux de swap OIS au pair, taux zéro, ou DF lus tels quels."""
    if courbe.type_taux == "df":
        return CourbeActualisation(
            devise=courbe.devise,
            temps=(0.0, *(p.maturite_annees for p in courbe.points)),
            facteurs=(1.0, *(p.valeur for p in courbe.points)),
            piliers_interpoles=(),
            source=courbe.source,
            hypothese=courbe.hypothese,
        )
    if courbe.type_taux == "zero":
        noeuds: list[tuple[float, float]] = []
        for p in courbe.points:
            if p.pilier is not None:
                echeance = date_echeance(date_valo, p.pilier)
                t = fraction_annee(date_valo, echeance, base_temps)
                tau = fraction_annee(date_valo, echeance, courbe.base_calcul)
            else:
                t = tau = p.maturite_annees
            noeuds.append((t, math.exp(-p.valeur / 100.0 * tau)))
        noeuds.sort()
        return CourbeActualisation(
            devise=courbe.devise,
            temps=(0.0, *(t for t, _ in noeuds)),
            facteurs=(1.0, *(f for _, f in noeuds)),
            piliers_interpoles=(),
            source=courbe.source,
            hypothese=courbe.hypothese,
        )

    swaps, interpoles = _taux_swap_complets(courbe)
    swaps.sort(key=lambda s: date_echeance(date_valo, s[0]))
    temps, facteurs = [0.0], [1.0]
    for pilier, taux in swaps:
        echeance = date_echeance(date_valo, pilier)
        mois = mois_du_tenor(pilier)
        if mois is None or mois <= 12:
            paiements = [echeance]  # paiement unique jusqu'à 1 an
        else:
            paiements = [ajouter_mois(date_valo, 12 * k) for k in range(1, mois // 12 + 1)]
        debuts = [date_valo, *paiements[:-1]]
        alphas = [fraction_annee(d0, d1, courbe.base_calcul) for d0, d1 in zip(debuts, paiements)]
        # Annuité des paiements déjà couverts par la courbe (nœuds précédents).
        annuite = sum(
            a * df_log_lineaire(temps, facteurs, fraction_annee(date_valo, d, base_temps))
            for a, d in zip(alphas[:-1], paiements[:-1])
        )
        df_n = (1.0 - taux * annuite) / (1.0 + taux * alphas[-1])
        t_n = fraction_annee(date_valo, echeance, base_temps)
        if t_n <= temps[-1]:
            raise ErreurConfig(f"Courbe {courbe.devise} : pilier {pilier} en double ou désordonné")
        if df_n <= 0:
            raise ErreurConfig(f"Courbe {courbe.devise} : DF négatif au pilier {pilier}, taux incohérents")
        temps.append(t_n)
        facteurs.append(df_n)
    return CourbeActualisation(
        devise=courbe.devise,
        temps=tuple(temps),
        facteurs=tuple(facteurs),
        piliers_interpoles=interpoles,
        source=courbe.source,
        hypothese=courbe.hypothese,
    )


def taux_swap_au_pair(
    courbe: CourbeActualisation, date_valo: date, pilier: str, base_courbe: str, base_temps: str
) -> float:
    """Taux de swap au pair recalculé sur la courbe (décimal) : sert à vérifier le bootstrap."""
    echeance = date_echeance(date_valo, pilier)
    mois = mois_du_tenor(pilier)
    if mois is None or mois <= 12:
        paiements = [echeance]
    else:
        paiements = [ajouter_mois(date_valo, 12 * k) for k in range(1, mois // 12 + 1)]
    debuts = [date_valo, *paiements[:-1]]
    annuite = sum(
        fraction_annee(d0, d1, base_courbe) * courbe.df(fraction_annee(date_valo, d1, base_temps))
        for d0, d1 in zip(debuts, paiements)
    )
    return (1.0 - courbe.df(fraction_annee(date_valo, echeance, base_temps))) / annuite


# ---------------------------------------------------------------------------
# Points de terme
# ---------------------------------------------------------------------------


def courbe_usd_depuis_points_de_terme(
    eur: CourbeActualisation,
    spot: float,
    terme: PointsDeTerme,
    date_valo: date,
    base_temps: str,
) -> CourbeActualisation:
    """DF USD implicites : F = S + pips / 10 000 et DF_USD(T) = DF_EUR(T) · S / F(T)."""
    noeuds = []
    for p in terme.points:
        t = (
            fraction_annee(date_valo, date_echeance(date_valo, p.pilier), base_temps)
            if p.pilier is not None
            else p.maturite_annees
        )
        forward = spot + p.valeur / 10_000.0
        if forward <= 0:
            raise ErreurConfig(f"Points de terme : forward négatif à t = {t:.4f}")
        noeuds.append((t, eur.df(t) * spot / forward))
    noeuds.sort()
    return CourbeActualisation(
        devise="USD",
        temps=(0.0, *(t for t, _ in noeuds)),
        facteurs=(1.0, *(f for _, f in noeuds)),
        piliers_interpoles=(),
        source=f"implicite : points de terme ({terme.source}) et courbe EUR",
        hypothese=terme.hypothese or eur.hypothese,
    )


def ecart_taux_implicite(spot: float, forward: float, t: float) -> float:
    """Écart r_USD − r_EUR (continu, décimal) implicite dans un forward : ln(F / S) / t."""
    if t <= 0:
        raise ValueError("t doit être strictement positif")
    return math.log(forward / spot) / t


# ---------------------------------------------------------------------------
# Ensemble EUR / USD et forward
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CourbesFX:
    """Spot et courbes EUR et USD à la date de valorisation."""

    spot: float
    eur: CourbeActualisation
    usd: CourbeActualisation

    @property
    def t_max(self) -> float:
        return min(self.eur.t_max, self.usd.t_max)

    def forward(self, t: float) -> float:
        """F(T) = S · DF_EUR(T) / DF_USD(T), en USD pour 1 EUR."""
        return self.spot * self.eur.df(t) / self.usd.df(t)

    def ecart_taux(self, t: float) -> float:
        """r_USD − r_EUR en taux zéro continus (décimal)."""
        return self.usd.taux_zero(t) - self.eur.taux_zero(t)


def construire_courbes(marche: MarcheFX, config: ConfigValo) -> CourbesFX:
    """Courbes EUR et USD depuis le marché validé ; USD implicite si seuls des points de terme existent."""
    dv, base = config.date_valorisation, config.base_calcul
    eur = bootstrap_courbe(marche.courbe_eur, dv, base)
    if marche.courbe_usd is not None:
        usd = bootstrap_courbe(marche.courbe_usd, dv, base)
    elif marche.points_de_terme is not None:
        usd = courbe_usd_depuis_points_de_terme(eur, marche.spot, marche.points_de_terme, dv, base)
    else:  # exclu par valider_marche
        raise ErreurConfig("Ni courbe USD ni points de terme")
    return CourbesFX(spot=marche.spot, eur=eur, usd=usd)
