"""Chargement et validation de la config de valorisation et des données de marché EUR/USD.

Règle du projet : aucune donnée de marché n'est inventée. Une donnée absente du fichier
de marché n'est acceptée que si la config fournit une hypothèse explicite
(``"hypothese": true`` et une ``"justification"`` non vide). Sinon le chargement échoue
avec la liste complète de ce qui manque (exception ``DonneesManquantes``).

Les chemins relatifs de la config sont résolus depuis la racine du projet, c'est-à-dire
le dossier parent du dossier ``config/``.

Organisation :
- fonctions pures ``config_depuis_dict``, ``valider_marche``, ``calcul_barriere``... qui
  travaillent sur des objets Python déjà lus (testables sans fichiers) ;
- fonctions ``charger_*`` / ``lire_*`` qui lisent les fichiers puis appellent les premières.
"""

from __future__ import annotations

import calendar
import csv
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

# Écart toléré entre le spot du fichier de marché et le fixing BCE du même jour.
TOLERANCE_SPOT = 1e-6
# Écart relatif au-delà duquel on signale que les vols ont été cotées sur un autre spot.
ECART_SPOT_AVERTISSEMENT = 0.005

BASES_CALCUL = ("ACT/365F", "ACT/360")
TYPES_TAUX = ("zero", "swap", "df")
INTERPOLATIONS_DF = ("log_lineaire",)
INTERPOLATIONS_SWAPS = ("lineaire",)
METHODES_SMILE = ("vanna_volga", "sabr")
METHODES_EXTRAPOLATION_VOL = ("vol_constante",)
CLES_HYPOTHESES_MARCHE = (
    "bf25_manquant",
    "vols_au_dela_dernier_pilier",
    "courbe_EUR",
    "courbe_USD",
    "points_de_terme",
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ErreurConfig(ValueError):
    """Fichier de config ou de données mal formé ou incohérent."""


class DonneesManquantes(ValueError):
    """Données de marché absentes et non couvertes par une hypothèse explicite."""

    def __init__(self, manquants: Sequence[str]) -> None:
        self.manquants: tuple[str, ...] = tuple(manquants)
        lignes = "\n".join(f"  - {m}" for m in self.manquants)
        super().__init__(
            f"{len(self.manquants)} donnée(s) de marché manquante(s). Complète le fichier "
            "de marché, ou ajoute une hypothèse explicite dans la config (section "
            "'hypotheses_marche', avec \"hypothese\": true et une justification) :\n"
            + lignes
        )


# ---------------------------------------------------------------------------
# Structures de données
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Hypothese:
    """Hypothèse explicite déclarée dans la config (reprise dans A_LIRE à l'étape 9)."""

    nom: str
    valeur: Any
    justification: str


@dataclass(frozen=True)
class PointCourbe:
    """Point d'une courbe : maturité en années et valeur (taux en %, points de terme en pips,
    ou facteur d'actualisation pour une courbe de type "df").

    ``pilier`` garde le libellé coté (1M, 2Y...) : l'étape 2 en tire la date exacte d'échéance.
    """

    maturite_annees: float
    valeur: float
    pilier: str | None = None


@dataclass(frozen=True)
class Courbe:
    """Courbe de taux d'une devise : taux zéro ou taux de swap (en %), ou facteurs
    d'actualisation (type "df") lus dans un fichier produit par le sous-groupe taux."""

    devise: str
    indice: str
    type_taux: str
    base_calcul: str
    points: tuple[PointCourbe, ...]
    source: str
    hypothese: bool
    convention: str | None = None  # ex. fréquence de la jambe fixe, en clair
    fichier: Path | None = None  # fichier source d'une courbe de type "df"


@dataclass(frozen=True)
class PointsDeTerme:
    """Points de terme EUR/USD en pips : F(T) = S + points / 10 000."""

    points: tuple[PointCourbe, ...]
    source: str
    hypothese: bool


@dataclass(frozen=True)
class HypothesesMarche:
    """Hypothèses de marché autorisées par la config, déjà validées."""

    bf25_pct: float | None
    bf25_plage_pct: tuple[float, float] | None
    extrapolation_vol: str | None
    courbe_eur: Courbe | None
    courbe_usd: Courbe | None
    points_de_terme: PointsDeTerme | None
    liste: tuple[Hypothese, ...]


@dataclass(frozen=True)
class ChocsSensibilite:
    """Tailles des chocs (en décimal ; le spot en relatif, K en absolu)."""

    spot_rel: float
    vol_atm: float
    rr25: float
    bf25: float
    ecart_taux: float
    barriere_k: float


@dataclass(frozen=True)
class ConfigValo:
    """Paramètres de valorisation : dates, chemins, termes de la note, conventions."""

    racine: Path
    date_valorisation: date
    chemin_fixings: Path
    chemin_marche: Path
    chemin_historique: Path
    dossier_resultats: Path
    debut_barriere: date
    fin_barriere: date
    source_barriere: str
    nominal_eur: float
    maturite_annees: int
    frequence_par_an: int
    coupon_par_periode: float
    date_depart_coupons: date
    base_calcul: str
    delta_ajuste_prime: bool
    delta_spot_jusqu_a_annees: float
    convention_atm: str
    interpolation_df: str
    interpolation_swaps: str
    methode_smile: str
    ordre_vanna_volga: int
    sabr_beta: float
    chocs: ChocsSensibilite
    sigma_eff_max_annees: float
    tolerance_controle_moteur: float  # en décimal (0,01 = 1 point de probabilité)
    sous_periode_annees: int
    vol_realisee_jours: Mapping[str, int]
    jours_ouvres_par_an: int
    hypotheses_marche: HypothesesMarche
    hypotheses: tuple[Hypothese, ...]


@dataclass(frozen=True)
class PilierVol:
    """Pilier de volatilité (en points de vol, %) : ATM, risk reversal et butterfly 25 delta."""

    pilier: str
    maturite_annees: float
    atm_pct: float
    rr25_pct: float
    bf25_pct: float
    source: str
    hypothese: bool
    bf25_suppose: bool = False  # BF25 repris de l'hypothèse 'bf25_manquant' de la config


@dataclass(frozen=True)
class MarcheFX:
    """Données de marché EUR/USD validées à la date de valorisation."""

    date_marche: date
    spot: float
    source_spot: str
    courbe_eur: Courbe
    courbe_usd: Courbe | None
    points_de_terme: PointsDeTerme | None
    piliers: tuple[PilierVol, ...]
    extrapolation_vol: str | None
    source_vols: str
    date_cotation_vols: date | None
    spot_reference_vols: float | None
    avertissements: tuple[str, ...]
    hypotheses_utilisees: tuple[str, ...]
    # K et date du pic recopiés dans le fichier de marché, recoupés avec le calcul sur les fixings.
    k_controle: float | None = None
    date_pic_controle: date | None = None


@dataclass(frozen=True)
class Barriere:
    """Barrière K = maximum des fixings sur la fenêtre d'observation."""

    niveau: float
    date_pic: date
    nb_observations: int
    debut: date
    fin: date


@dataclass(frozen=True)
class DateCoupon:
    """Date de coupon : numéro (1 à 14), date calendaire, maturité en années."""

    numero: int
    date: date
    t_annees: float


@dataclass(frozen=True)
class ContexteValo:
    """Tout ce qu'il faut pour pricer : config, marché, fixings et barrière."""

    config: ConfigValo
    marche: MarcheFX
    fixings: tuple[tuple[date, float], ...]
    barriere: Barriere


# ---------------------------------------------------------------------------
# Petites fonctions de lecture et de validation
# ---------------------------------------------------------------------------


def _champ(d: Any, cle: str, chemin: str) -> Any:
    """Renvoie d[cle] ou lève une erreur qui nomme le champ absent."""
    if not isinstance(d, Mapping):
        raise ErreurConfig(f"{chemin} : objet JSON attendu, reçu {type(d).__name__}")
    if cle not in d:
        raise ErreurConfig(f"Champ obligatoire absent : {chemin}.{cle}")
    return d[cle]


def _date(x: Any, chemin: str) -> date:
    try:
        return date.fromisoformat(x)
    except (TypeError, ValueError):
        raise ErreurConfig(f"{chemin} : date AAAA-MM-JJ attendue, reçu {x!r}") from None


def _nombre(x: Any, chemin: str, *, positif: bool = False) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ErreurConfig(f"{chemin} : nombre fini attendu, reçu {x!r}")
    if positif and x <= 0:
        raise ErreurConfig(f"{chemin} : nombre strictement positif attendu, reçu {x!r}")
    return float(x)


def _nombre_ou_none(x: Any, chemin: str, *, positif: bool = False) -> float | None:
    return None if x is None else _nombre(x, chemin, positif=positif)


def _entier_positif(x: Any, chemin: str) -> int:
    if isinstance(x, bool) or not isinstance(x, int) or x <= 0:
        raise ErreurConfig(f"{chemin} : entier strictement positif attendu, reçu {x!r}")
    return x


def _texte(x: Any, chemin: str) -> str:
    if not isinstance(x, str) or not x.strip():
        raise ErreurConfig(f"{chemin} : texte non vide attendu, reçu {x!r}")
    return x.strip()


def _booleen(x: Any, chemin: str) -> bool:
    if not isinstance(x, bool):
        raise ErreurConfig(f"{chemin} : true ou false attendu, reçu {x!r}")
    return x


def _hypothese(nom: str, d: Any, chemin: str, valeur: Any) -> Hypothese:
    """Vérifie qu'un bloc d'hypothèse porte "hypothese": true et une justification."""
    if not isinstance(d, Mapping) or d.get("hypothese") is not True:
        raise ErreurConfig(f"{chemin} : une hypothèse doit porter \"hypothese\": true")
    justification = d.get("justification")
    if not isinstance(justification, str) or not justification.strip():
        raise ErreurConfig(f"{chemin} : hypothèse sans justification")
    return Hypothese(nom=nom, valeur=valeur, justification=justification.strip())


def _valeur_ou_hypothese(x: Any, chemin: str, nom: str) -> tuple[Any, Hypothese | None]:
    """Accepte une valeur brute, ou {"valeur": ..., "hypothese": true, "justification": ...}."""
    if not isinstance(x, Mapping):
        return x, None
    valeur = _champ(x, "valeur", chemin)
    if x.get("hypothese") is True:
        return valeur, _hypothese(nom, x, chemin, valeur)
    return valeur, None


def fraction_annee(debut: date, fin: date, base: str = "ACT/365F") -> float:
    """Fraction d'année entre deux dates selon la base de calcul."""
    jours = (fin - debut).days
    if base == "ACT/365F":
        return jours / 365.0
    if base == "ACT/360":
        return jours / 360.0
    raise ErreurConfig(f"Base de calcul non gérée : {base!r} (attendu : {', '.join(BASES_CALCUL)})")


def ajouter_mois(d: date, n: int) -> date:
    """Ajoute n mois à une date ; le jour est ramené au dernier jour du mois si besoin."""
    mois_total = d.month - 1 + n
    annee, mois = d.year + mois_total // 12, mois_total % 12 + 1
    return date(annee, mois, min(d.day, calendar.monthrange(annee, mois)[1]))


# ---------------------------------------------------------------------------
# Courbes et points de terme
# ---------------------------------------------------------------------------


def _points(liste: Any, cle_valeur: str, chemin: str) -> tuple[PointCourbe, ...]:
    """Lit une liste de points {maturite_annees, <cle_valeur>} à maturités croissantes."""
    if not isinstance(liste, list):
        raise ErreurConfig(f"{chemin} : liste attendue")
    points = tuple(
        PointCourbe(
            maturite_annees=_nombre(
                _champ(p, "maturite_annees", f"{chemin}[{i}]"),
                f"{chemin}[{i}].maturite_annees",
                positif=True,
            ),
            valeur=_nombre(_champ(p, cle_valeur, f"{chemin}[{i}]"), f"{chemin}[{i}].{cle_valeur}"),
            pilier=None if p.get("pilier") is None else _texte(p["pilier"], f"{chemin}[{i}].pilier"),
        )
        for i, p in enumerate(liste)
    )
    maturites = [p.maturite_annees for p in points]
    if any(b <= a for a, b in zip(maturites, maturites[1:])):
        raise ErreurConfig(f"{chemin} : maturités strictement croissantes attendues")
    return points


def lire_facteurs_fichier(chemin_fichier: Path, colonne_temps: str, colonne_df: str) -> tuple[PointCourbe, ...]:
    """Lit un CSV de facteurs d'actualisation (t en années, DF) ; le nœud t = 0 est ignoré.

    Format attendu : celui des sorties du sous-groupe taux (output/curve_eur.csv,
    output/curve_usd.csv) : une ligne par maturité, colonnes nommées dans la config.
    """
    if not chemin_fichier.exists():
        raise ErreurConfig(f"Fichier de courbe introuvable : {chemin_fichier}")
    points: list[PointCourbe] = []
    with chemin_fichier.open(encoding="utf-8-sig", newline="") as f:
        lecteur = csv.DictReader(f)
        if lecteur.fieldnames is None or not {colonne_temps, colonne_df} <= set(lecteur.fieldnames):
            raise ErreurConfig(
                f"{chemin_fichier.name} : colonnes {colonne_temps!r} et {colonne_df!r} attendues, "
                f"trouvées {lecteur.fieldnames}"
            )
        for ligne, row in enumerate(lecteur, start=2):
            try:
                t, df = float(row[colonne_temps]), float(row[colonne_df])
            except (TypeError, ValueError):
                raise ErreurConfig(f"{chemin_fichier.name} ligne {ligne} : valeur illisible") from None
            if not (math.isfinite(t) and math.isfinite(df)) or t < 0 or not 0 < df <= 1.5:
                raise ErreurConfig(f"{chemin_fichier.name} ligne {ligne} : t = {t!r}, DF = {df!r} invalides")
            if t > 0:
                points.append(PointCourbe(maturite_annees=t, valeur=df))
    if not points:
        raise ErreurConfig(f"{chemin_fichier.name} : aucun facteur d'actualisation pour t > 0")
    maturites = [p.maturite_annees for p in points]
    if any(b <= a for a, b in zip(maturites, maturites[1:])):
        raise ErreurConfig(f"{chemin_fichier.name} : maturités strictement croissantes attendues")
    return tuple(points)


def lire_courbe(
    d: Any,
    devise: str,
    chemin: str,
    *,
    hypothese: bool,
    date_valo: date | None = None,
    racine: Path | None = None,
) -> Courbe | None:
    """Lit une courbe de taux ; renvoie None si elle n'a aucun point.

    Deux formes :
    - "points" : liste de taux (zéro ou swap, en %) ;
    - "fichier" (type_taux "df") : CSV de facteurs d'actualisation, chemin relatif à la
      racine du projet, colonnes "colonne_temps" (défaut "t") et "colonne_df".
    Si la courbe porte un champ "date", il doit être égal à la date de valorisation.
    """
    fichier: Path | None = None
    if isinstance(d, Mapping) and d.get("fichier") is not None:
        if _champ(d, "type_taux", chemin) != "df":
            raise ErreurConfig(f"{chemin} : une courbe lue dans un fichier doit avoir \"type_taux\": \"df\"")
        fichier = Path(_texte(d["fichier"], f"{chemin}.fichier"))
        if not fichier.is_absolute():
            if racine is None:
                raise ErreurConfig(f"{chemin}.fichier : chemin relatif sans racine de projet")
            fichier = racine / fichier
        points = lire_facteurs_fichier(
            fichier,
            _texte(d.get("colonne_temps", "t"), f"{chemin}.colonne_temps"),
            _texte(_champ(d, "colonne_df", chemin), f"{chemin}.colonne_df"),
        )
    else:
        points = _points(_champ(d, "points", chemin), "taux_pct", f"{chemin}.points")
        if not points:
            return None
        if isinstance(d, Mapping) and d.get("type_taux") == "df":
            raise ErreurConfig(f"{chemin} : type_taux \"df\" uniquement avec un champ \"fichier\"")
    if date_valo is not None and d.get("date") is not None:
        date_courbe = _date(d["date"], f"{chemin}.date")
        if date_courbe != date_valo:
            raise ErreurConfig(
                f"{chemin}.date ({date_courbe}) différente de la date de valorisation ({date_valo})"
            )
    convention = d.get("convention")
    type_taux = _texte(_champ(d, "type_taux", chemin), f"{chemin}.type_taux")
    if type_taux not in TYPES_TAUX:
        raise ErreurConfig(f"{chemin}.type_taux : {type_taux!r} (attendu : {', '.join(TYPES_TAUX)})")
    base = _texte(_champ(d, "base_calcul", chemin), f"{chemin}.base_calcul")
    if base not in BASES_CALCUL:
        raise ErreurConfig(f"{chemin}.base_calcul : {base!r} (attendu : {', '.join(BASES_CALCUL)})")
    return Courbe(
        devise=devise,
        indice=_texte(_champ(d, "indice", chemin), f"{chemin}.indice"),
        type_taux=type_taux,
        base_calcul=base,
        points=points,
        source=_texte(_champ(d, "source", chemin), f"{chemin}.source"),
        hypothese=hypothese,
        convention=None if convention is None else _texte(convention, f"{chemin}.convention"),
        fichier=fichier,
    )


def lire_points_de_terme(d: Any, chemin: str, *, hypothese: bool) -> PointsDeTerme | None:
    """Lit des points de terme EUR/USD ; renvoie None s'il n'y a aucun point."""
    points = _points(_champ(d, "points", chemin), "points_pips", f"{chemin}.points")
    if not points:
        return None
    return PointsDeTerme(
        points=points,
        source=_texte(_champ(d, "source", chemin), f"{chemin}.source"),
        hypothese=hypothese,
    )


# ---------------------------------------------------------------------------
# Config de valorisation
# ---------------------------------------------------------------------------


def _hypotheses_marche(brut: Any) -> HypothesesMarche:
    """Valide la section 'hypotheses_marche' de la config (toutes les entrées sont optionnelles)."""
    chemin = "config.hypotheses_marche"
    if not isinstance(brut, Mapping):
        raise ErreurConfig(f"{chemin} : objet JSON attendu")
    inconnues = sorted(set(brut) - set(CLES_HYPOTHESES_MARCHE))
    if inconnues:
        raise ErreurConfig(
            f"{chemin} : clé(s) inconnue(s) {inconnues} (autorisées : {', '.join(CLES_HYPOTHESES_MARCHE)})"
        )
    liste: list[Hypothese] = []

    bf25: float | None = None
    bf25_plage: tuple[float, float] | None = None
    if "bf25_manquant" in brut:
        c = f"{chemin}.bf25_manquant"
        d = brut["bf25_manquant"]
        bf25 = _nombre(_champ(d, "valeur_pct", c), f"{c}.valeur_pct")
        if d.get("plage_sensibilite_pct") is not None:
            p = d["plage_sensibilite_pct"]
            if not isinstance(p, list) or len(p) != 2:
                raise ErreurConfig(f"{c}.plage_sensibilite_pct : liste [bas, haut] attendue")
            bas, haut = _nombre(p[0], f"{c}.plage_sensibilite_pct[0]"), _nombre(p[1], f"{c}.plage_sensibilite_pct[1]")
            if not bas <= bf25 <= haut:
                raise ErreurConfig(f"{c}.plage_sensibilite_pct : la plage doit contenir valeur_pct ({bf25})")
            bf25_plage = (bas, haut)
        liste.append(_hypothese("bf25_manquant", d, c, bf25))

    extrapolation: str | None = None
    if "vols_au_dela_dernier_pilier" in brut:
        c = f"{chemin}.vols_au_dela_dernier_pilier"
        d = brut["vols_au_dela_dernier_pilier"]
        extrapolation = _texte(_champ(d, "methode", c), f"{c}.methode")
        if extrapolation not in METHODES_EXTRAPOLATION_VOL:
            raise ErreurConfig(f"{c}.methode : {extrapolation!r} (attendu : {', '.join(METHODES_EXTRAPOLATION_VOL)})")
        liste.append(_hypothese("vols_au_dela_dernier_pilier", d, c, extrapolation))

    courbes: dict[str, Courbe | None] = {"EUR": None, "USD": None}
    for devise in courbes:
        cle = f"courbe_{devise}"
        if cle in brut:
            c = f"{chemin}.{cle}"
            courbe = lire_courbe(brut[cle], devise, c, hypothese=True)
            if courbe is None:
                raise ErreurConfig(f"{c} : hypothèse de courbe sans aucun point")
            liste.append(_hypothese(cle, brut[cle], c, courbe.points))
            courbes[devise] = courbe

    terme: PointsDeTerme | None = None
    if "points_de_terme" in brut:
        c = f"{chemin}.points_de_terme"
        terme = lire_points_de_terme(brut["points_de_terme"], c, hypothese=True)
        if terme is None:
            raise ErreurConfig(f"{c} : hypothèse de points de terme sans aucun point")
        liste.append(_hypothese("points_de_terme", brut["points_de_terme"], c, terme.points))

    return HypothesesMarche(
        bf25_pct=bf25,
        bf25_plage_pct=bf25_plage,
        extrapolation_vol=extrapolation,
        courbe_eur=courbes["EUR"],
        courbe_usd=courbes["USD"],
        points_de_terme=terme,
        liste=tuple(liste),
    )


def config_depuis_dict(brut: Mapping[str, Any], racine: Path) -> ConfigValo:
    """Construit et valide la config à partir du JSON déjà lu (fonction pure)."""
    hypotheses: list[Hypothese] = []
    date_valo = _date(_champ(brut, "date_valorisation", "config"), "config.date_valorisation")

    chemins = _champ(brut, "chemins", "config")

    def chemin(cle: str) -> Path:
        p = Path(_texte(_champ(chemins, cle, "config.chemins"), f"config.chemins.{cle}"))
        return p if p.is_absolute() else racine / p

    b = _champ(brut, "barriere", "config")
    debut = _date(_champ(b, "debut_observation", "config.barriere"), "config.barriere.debut_observation")
    fin = _date(_champ(b, "fin_observation", "config.barriere"), "config.barriere.fin_observation")
    if fin < debut:
        raise ErreurConfig("config.barriere : fin_observation antérieure à debut_observation")
    if fin > date_valo:
        raise ErreurConfig(
            "config.barriere : la fenêtre d'observation se termine après la date de "
            "valorisation, K ne serait pas encore connu"
        )

    n = _champ(brut, "note", "config")
    coupon_brut, h = _valeur_ou_hypothese(
        _champ(n, "coupon_par_periode", "config.note"), "config.note.coupon_par_periode", "coupon_par_periode"
    )
    coupon = _nombre(coupon_brut, "config.note.coupon_par_periode", positif=True)
    if h:
        hypotheses.append(h)
    depart_brut, h = _valeur_ou_hypothese(
        _champ(n, "date_depart_coupons", "config.note"), "config.note.date_depart_coupons", "date_depart_coupons"
    )
    depart = _date(depart_brut, "config.note.date_depart_coupons")
    if h:
        hypotheses.append(h)

    c = _champ(brut, "conventions", "config")
    base = _texte(_champ(c, "base_calcul", "config.conventions"), "config.conventions.base_calcul")
    if base not in BASES_CALCUL:
        raise ErreurConfig(f"config.conventions.base_calcul : {base!r} (attendu : {', '.join(BASES_CALCUL)})")
    atm = _texte(_champ(c, "atm", "config.conventions"), "config.conventions.atm")
    if atm != "DNS":
        raise ErreurConfig(f"config.conventions.atm : seule la convention 'DNS' est gérée, reçu {atm!r}")
    ci = "config.conventions.interpolation_courbes"
    interp = _champ(c, "interpolation_courbes", "config.conventions")
    interp_df = _texte(_champ(interp, "facteurs_actualisation", ci), f"{ci}.facteurs_actualisation")
    if interp_df not in INTERPOLATIONS_DF:
        raise ErreurConfig(f"{ci}.facteurs_actualisation : {interp_df!r} (attendu : {', '.join(INTERPOLATIONS_DF)})")
    interp_swaps = _texte(_champ(interp, "taux_swap_manquants", ci), f"{ci}.taux_swap_manquants")
    if interp_swaps not in INTERPOLATIONS_SWAPS:
        raise ErreurConfig(f"{ci}.taux_swap_manquants : {interp_swaps!r} (attendu : {', '.join(INTERPOLATIONS_SWAPS)})")

    sm = _champ(brut, "smile", "config")
    methode_smile = _texte(_champ(sm, "methode", "config.smile"), "config.smile.methode")
    if methode_smile not in METHODES_SMILE:
        raise ErreurConfig(f"config.smile.methode : {methode_smile!r} (attendu : {', '.join(METHODES_SMILE)})")
    ordre_vv = _champ(sm, "ordre_vanna_volga", "config.smile")
    if ordre_vv not in (1, 2) or isinstance(ordre_vv, bool):
        raise ErreurConfig(f"config.smile.ordre_vanna_volga : 1 ou 2 attendu, reçu {ordre_vv!r}")
    sabr_beta = _nombre(_champ(sm, "sabr_beta", "config.smile"), "config.smile.sabr_beta")
    if not 0.0 <= sabr_beta <= 1.0:
        raise ErreurConfig(f"config.smile.sabr_beta : valeur entre 0 et 1 attendue, reçu {sabr_beta!r}")

    se = _champ(brut, "sensibilites", "config")

    def choc(cle: str) -> float:
        return _nombre(_champ(se, cle, "config.sensibilites"), f"config.sensibilites.{cle}", positif=True)

    chocs = ChocsSensibilite(
        spot_rel=choc("spot_pct") / 100,
        vol_atm=choc("vol_atm_pts") / 100,
        rr25=choc("rr25_pts") / 100,
        bf25=choc("bf25_pts") / 100,
        ecart_taux=choc("ecart_taux_bp") / 10_000,
        barriere_k=choc("barriere_k"),
    )

    hi = _champ(brut, "historique", "config")
    ch = "config.historique"
    vol_jours_brut = _champ(hi, "vol_realisee_jours", ch)
    if not isinstance(vol_jours_brut, Mapping) or not vol_jours_brut:
        raise ErreurConfig(f"{ch}.vol_realisee_jours : objet {{pilier: nombre de jours}} non vide attendu")
    vol_jours = {
        _texte(p, f"{ch}.vol_realisee_jours"): _entier_positif(n, f"{ch}.vol_realisee_jours.{p}")
        for p, n in vol_jours_brut.items()
    }

    hm = _hypotheses_marche(brut.get("hypotheses_marche", {}))
    hypotheses.extend(hm.liste)

    return ConfigValo(
        racine=racine,
        date_valorisation=date_valo,
        chemin_fixings=chemin("fixings_bce"),
        chemin_marche=chemin("marche"),
        chemin_historique=chemin("historique_bce"),
        dossier_resultats=chemin("resultats"),
        debut_barriere=debut,
        fin_barriere=fin,
        source_barriere=_texte(_champ(b, "source", "config.barriere"), "config.barriere.source"),
        nominal_eur=_nombre(_champ(n, "nominal_eur", "config.note"), "config.note.nominal_eur", positif=True),
        maturite_annees=_entier_positif(_champ(n, "maturite_annees", "config.note"), "config.note.maturite_annees"),
        frequence_par_an=_entier_positif(
            _champ(n, "frequence_coupons_par_an", "config.note"), "config.note.frequence_coupons_par_an"
        ),
        coupon_par_periode=coupon,
        date_depart_coupons=depart,
        base_calcul=base,
        delta_ajuste_prime=_booleen(
            _champ(c, "delta_ajuste_prime", "config.conventions"), "config.conventions.delta_ajuste_prime"
        ),
        delta_spot_jusqu_a_annees=_nombre(
            _champ(c, "delta_spot_jusqu_a_annees", "config.conventions"),
            "config.conventions.delta_spot_jusqu_a_annees",
            positif=True,
        ),
        convention_atm=atm,
        interpolation_df=interp_df,
        interpolation_swaps=interp_swaps,
        methode_smile=methode_smile,
        ordre_vanna_volga=ordre_vv,
        sabr_beta=sabr_beta,
        chocs=chocs,
        sigma_eff_max_annees=_nombre(
            _champ(_champ(brut, "export_mc", "config"), "sigma_eff_maturite_max_annees", "config.export_mc"),
            "config.export_mc.sigma_eff_maturite_max_annees", positif=True,
        ),
        tolerance_controle_moteur=_nombre(
            _champ(_champ(brut, "export_mc", "config"), "tolerance_controle_moteur_points", "config.export_mc"),
            "config.export_mc.tolerance_controle_moteur_points", positif=True,
        ) / 100,
        sous_periode_annees=_entier_positif(_champ(hi, "sous_periode_annees", ch), f"{ch}.sous_periode_annees"),
        vol_realisee_jours=vol_jours,
        jours_ouvres_par_an=_entier_positif(_champ(hi, "jours_ouvres_par_an", ch), f"{ch}.jours_ouvres_par_an"),
        hypotheses_marche=hm,
        hypotheses=tuple(hypotheses),
    )


def lire_json(chemin: Path) -> Any:
    """Lit un fichier JSON en UTF-8, avec un message clair si le fichier est absent ou invalide."""
    if not chemin.exists():
        raise ErreurConfig(f"Fichier introuvable : {chemin}")
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ErreurConfig(f"{chemin} : JSON invalide ({e})") from None


def charger_config(chemin_config: Path) -> ConfigValo:
    """Lit config/<fichier>.json ; les chemins relatifs partent de la racine du projet."""
    chemin_config = Path(chemin_config).resolve()
    return config_depuis_dict(lire_json(chemin_config), racine=chemin_config.parent.parent)


def echeancier_coupons(config: ConfigValo) -> tuple[DateCoupon, ...]:
    """Dates de coupon : tous les 12/frequence mois depuis la date de départ, jusqu'à maturité."""
    pas_mois = 12 // config.frequence_par_an
    if pas_mois * config.frequence_par_an != 12:
        raise ErreurConfig("config.note.frequence_coupons_par_an doit diviser 12")
    nb = config.maturite_annees * config.frequence_par_an
    dates = [ajouter_mois(config.date_depart_coupons, pas_mois * i) for i in range(1, nb + 1)]
    return tuple(
        DateCoupon(numero=i, date=d, t_annees=fraction_annee(config.date_valorisation, d, config.base_calcul))
        for i, d in enumerate(dates, start=1)
    )


# ---------------------------------------------------------------------------
# Données de marché
# ---------------------------------------------------------------------------


def valider_marche(brut: Mapping[str, Any], config: ConfigValo) -> MarcheFX:
    """Valide le fichier de marché ; complète avec les hypothèses de la config si autorisé.

    Règles :
    - une courbe EUR est obligatoire (actualisation des coupons payés en EUR) ;
    - il faut en plus une courbe USD, ou à défaut des points de terme EUR/USD ;
    - chaque pilier coté doit avoir ATM et RR25 ; un BF25 absent est remplacé par
      l'hypothèse 'bf25_manquant' si elle existe ;
    - un pilier sans ATM n'est accepté que s'il est au-delà du dernier pilier coté et que
      l'hypothèse 'vols_au_dela_dernier_pilier' existe (il est alors retiré de la liste,
      l'extrapolation est faite par smile.py) ; un trou au milieu n'est jamais accepté.
    Toutes les données manquantes sont listées ensemble dans une seule exception.
    """
    hm = config.hypotheses_marche
    manquants: list[str] = []
    avertissements: list[str] = []
    utilisees: list[str] = []

    date_marche = _date(_champ(brut, "date_marche", "marche"), "marche.date_marche")
    if date_marche != config.date_valorisation:
        raise ErreurConfig(
            f"marche.date_marche ({date_marche}) différente de la date de valorisation "
            f"({config.date_valorisation}) : une seule date de valorisation est autorisée"
        )

    # Spot
    s = _champ(brut, "spot", "marche")
    spot = _nombre_ou_none(_champ(s, "valeur", "marche.spot"), "marche.spot.valeur", positif=True)
    source_spot = ""
    if spot is None:
        manquants.append("Spot EUR/USD à la date de valorisation")
    else:
        date_spot = _date(_champ(s, "date", "marche.spot"), "marche.spot.date")
        if date_spot != config.date_valorisation:
            raise ErreurConfig(
                f"marche.spot.date ({date_spot}) différente de la date de valorisation ({config.date_valorisation})"
            )
        source_spot = _texte(_champ(s, "source", "marche.spot"), "marche.spot.source")

    # Courbes : le fichier de marché a priorité, la config ne sert qu'à combler un manque.
    courbes = _champ(brut, "courbes", "marche")
    dv = config.date_valorisation
    courbe_eur = lire_courbe(
        _champ(courbes, "EUR", "marche.courbes"), "EUR", "marche.courbes.EUR",
        hypothese=False, date_valo=dv, racine=config.racine,
    )
    courbe_usd = lire_courbe(
        _champ(courbes, "USD", "marche.courbes"), "USD", "marche.courbes.USD",
        hypothese=False, date_valo=dv, racine=config.racine,
    )
    terme = lire_points_de_terme(
        brut.get("points_de_terme", {"points": []}), "marche.points_de_terme", hypothese=False
    )
    if courbe_eur is None and hm.courbe_eur is not None:
        courbe_eur = hm.courbe_eur
        utilisees.append("courbe_EUR")
    if courbe_usd is None and hm.courbe_usd is not None:
        courbe_usd = hm.courbe_usd
        utilisees.append("courbe_USD")
    if courbe_usd is None and terme is None and hm.points_de_terme is not None:
        terme = hm.points_de_terme
        utilisees.append("points_de_terme")
    if courbe_eur is None:
        manquants.append(
            "Courbe EUR (€STR) : aucun point (taux zéro ou taux de swap) ; indispensable "
            "pour actualiser les coupons payés en EUR"
        )
    if courbe_usd is None and terme is None:
        manquants.append("Courbe USD (SOFR) : aucun point (taux zéro ou taux de swap), ou à défaut points de terme EUR/USD")

    # Volatilités
    v = _champ(brut, "volatilites", "marche")
    source_vols = _texte(_champ(v, "source", "marche.volatilites"), "marche.volatilites.source")
    date_cot_brut = v.get("date_cotation")
    date_cotation = None if date_cot_brut is None else _date(date_cot_brut, "marche.volatilites.date_cotation")
    spot_ref = _nombre_ou_none(v.get("spot_reference"), "marche.volatilites.spot_reference", positif=True)
    if date_cotation is None:
        avertissements.append(f"Date de cotation des vols ({source_vols}) inconnue : à confirmer.")
    elif date_cotation != config.date_valorisation:
        avertissements.append(
            f"Vols {source_vols} cotées le {date_cotation}, pas à la date de valorisation {config.date_valorisation}."
        )
    if spot is not None and spot_ref is not None and abs(spot_ref / spot - 1) > ECART_SPOT_AVERTISSEMENT:
        avertissements.append(
            f"Spot de référence des vols ({spot_ref:.4f}) différent du spot de valorisation "
            f"({spot:.4f}), écart de {100 * (spot_ref / spot - 1):+.2f} %."
        )

    bruts = _champ(v, "piliers", "marche.volatilites")
    if not isinstance(bruts, list) or not bruts:
        raise ErreurConfig("marche.volatilites.piliers : liste non vide attendue")
    lus = []
    for i, p in enumerate(bruts):
        c = f"marche.volatilites.piliers[{i}]"
        lus.append(
            (
                _texte(_champ(p, "pilier", c), f"{c}.pilier"),
                _nombre(_champ(p, "maturite_annees", c), f"{c}.maturite_annees", positif=True),
                _nombre_ou_none(_champ(p, "atm_pct", c), f"{c}.atm_pct", positif=True),
                _nombre_ou_none(_champ(p, "rr25_pct", c), f"{c}.rr25_pct"),
                _nombre_ou_none(_champ(p, "bf25_pct", c), f"{c}.bf25_pct"),
                p.get("source"),
                p.get("hypothese") is True,
            )
        )
    maturites = [t for _, t, *_ in lus]
    if any(b <= a for a, b in zip(maturites, maturites[1:])):
        raise ErreurConfig("marche.volatilites.piliers : maturités strictement croissantes attendues")
    cotes = [t for _, t, atm, *_ in lus if atm is not None]
    dernier_cote = max(cotes) if cotes else 0.0

    piliers: list[PilierVol] = []
    bf_supposes: list[str] = []
    extrapoles: list[str] = []
    for nom, t, atm, rr, bf, source, hyp in lus:
        if atm is None:
            if t > dernier_cote and hm.extrapolation_vol is not None:
                extrapoles.append(nom)
            else:
                manquants.append(f"Vol ATM {nom}")
            continue
        if rr is None:
            manquants.append(f"Risk reversal 25Δ {nom}")
        bf_suppose = False
        if bf is None:
            if hm.bf25_pct is not None:
                bf, hyp, bf_suppose = hm.bf25_pct, True, True
                source = f"{source or source_vols} ; BF25 : hypothèse config"
                bf_supposes.append(nom)
            else:
                manquants.append(f"Butterfly 25Δ {nom}")
        if rr is not None and bf is not None:
            piliers.append(
                PilierVol(
                    pilier=nom,
                    maturite_annees=t,
                    atm_pct=atm,
                    rr25_pct=rr,
                    bf25_pct=bf,
                    source=source or source_vols,
                    hypothese=hyp,
                    bf25_suppose=bf_suppose,
                )
            )
    if bf_supposes:
        utilisees.append(f"bf25_manquant ({hm.bf25_pct:.2f} pt) sur {', '.join(bf_supposes)}")
    if extrapoles:
        utilisees.append(f"vols_au_dela_dernier_pilier ({hm.extrapolation_vol}) pour {', '.join(extrapoles)}")

    # Contrôle facultatif : K recopié dans le fichier de marché (recoupé dans charger_contexte).
    k_controle: float | None = None
    date_pic_controle: date | None = None
    if brut.get("barriere_K") is not None:
        bk = brut["barriere_K"]
        k_controle = _nombre(_champ(bk, "valeur", "marche.barriere_K"), "marche.barriere_K.valeur", positif=True)
        date_pic_controle = _date(_champ(bk, "date_pic", "marche.barriere_K"), "marche.barriere_K.date_pic")

    if manquants:
        raise DonneesManquantes(manquants)
    assert spot is not None and courbe_eur is not None  # garanti par l'absence de manquants
    return MarcheFX(
        date_marche=date_marche,
        spot=spot,
        source_spot=source_spot,
        courbe_eur=courbe_eur,
        courbe_usd=courbe_usd,
        points_de_terme=terme,
        piliers=tuple(piliers),
        extrapolation_vol=hm.extrapolation_vol if extrapoles else None,
        source_vols=source_vols,
        date_cotation_vols=date_cotation,
        spot_reference_vols=spot_ref,
        avertissements=tuple(avertissements),
        hypotheses_utilisees=tuple(utilisees),
        k_controle=k_controle,
        date_pic_controle=date_pic_controle,
    )


def charger_marche(config: ConfigValo) -> MarcheFX:
    """Lit et valide le fichier de marché désigné par la config."""
    return valider_marche(lire_json(config.chemin_marche), config)


# ---------------------------------------------------------------------------
# Fixings BCE et barrière
# ---------------------------------------------------------------------------


def lire_fixings_bce(chemin: Path) -> tuple[tuple[date, float], ...]:
    """Lit le CSV BCE (colonnes date, eurusd) ; toute ligne invalide est une erreur."""
    if not chemin.exists():
        raise ErreurConfig(f"Fichier de fixings introuvable : {chemin}")
    fixings: dict[date, float] = {}
    with chemin.open(encoding="utf-8-sig", newline="") as f:
        lecteur = csv.DictReader(f)
        if lecteur.fieldnames is None or not {"date", "eurusd"} <= set(lecteur.fieldnames):
            raise ErreurConfig(f"{chemin} : colonnes 'date' et 'eurusd' attendues")
        for ligne, row in enumerate(lecteur, start=2):
            d = _date(row["date"], f"{chemin.name} ligne {ligne}")
            try:
                x = float(row["eurusd"])
            except (TypeError, ValueError):
                raise ErreurConfig(f"{chemin.name} ligne {ligne} : fixing illisible {row['eurusd']!r}") from None
            if not math.isfinite(x) or x <= 0:
                raise ErreurConfig(f"{chemin.name} ligne {ligne} : fixing invalide {x!r}")
            if d in fixings:
                raise ErreurConfig(f"{chemin.name} ligne {ligne} : date {d} en double")
            fixings[d] = x
    return tuple(sorted(fixings.items()))


def calcul_barriere(fixings: Sequence[tuple[date, float]], debut: date, fin: date) -> Barriere:
    """K = plus haut fixing sur [debut, fin] (bornes incluses) ; en cas d'égalité, la première date."""
    fenetre = sorted((d, x) for d, x in fixings if debut <= d <= fin)
    if not fenetre:
        raise ErreurConfig(f"Aucun fixing entre le {debut} et le {fin} : barrière incalculable")
    date_pic, niveau = fenetre[0]
    for d, x in fenetre[1:]:
        if x > niveau:
            date_pic, niveau = d, x
    return Barriere(niveau=niveau, date_pic=date_pic, nb_observations=len(fenetre), debut=debut, fin=fin)


def fixing_a_date(fixings: Sequence[tuple[date, float]], d: date) -> float:
    """Fixing BCE publié à la date d (pas de report d'une date antérieure)."""
    for jour, x in fixings:
        if jour == d:
            return x
    raise ErreurConfig(f"Pas de fixing BCE le {d}")


def verifier_coherence(marche: MarcheFX, barriere: Barriere, fixings: Sequence[tuple[date, float]], date_valo: date) -> None:
    """Recoupe le fichier de marché avec les fixings : spot (si source BCE) et K recopié."""
    if "BCE" in marche.source_spot:
        bce = fixing_a_date(fixings, date_valo)
        if abs(bce - marche.spot) > TOLERANCE_SPOT:
            raise ErreurConfig(f"Spot de marché ({marche.spot}) différent du fixing BCE du {date_valo} ({bce})")
    if marche.k_controle is not None and abs(marche.k_controle - barriere.niveau) > TOLERANCE_SPOT:
        raise ErreurConfig(
            f"K du fichier de marché ({marche.k_controle}) différent de K recalculé sur les fixings ({barriere.niveau})"
        )
    if marche.date_pic_controle is not None and marche.date_pic_controle != barriere.date_pic:
        raise ErreurConfig(
            f"Date du pic du fichier de marché ({marche.date_pic_controle}) différente de celle "
            f"recalculée sur les fixings ({barriere.date_pic})"
        )


def charger_contexte(chemin_config: Path) -> ContexteValo:
    """Charge config, fixings, barrière et marché, puis recoupe marché et fixings."""
    config = charger_config(chemin_config)
    fixings = lire_fixings_bce(config.chemin_fixings)
    barriere = calcul_barriere(fixings, config.debut_barriere, config.fin_barriere)
    marche = charger_marche(config)
    verifier_coherence(marche, barriere, fixings, config.date_valorisation)
    return ContexteValo(config=config, marche=marche, fixings=fixings, barriere=barriere)
