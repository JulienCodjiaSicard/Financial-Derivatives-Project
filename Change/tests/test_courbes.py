"""Tests de l'étape 2 : bootstrap des courbes EUR / USD et forward EUR/USD."""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pytest

from fx.config import (
    Courbe,
    ErreurConfig,
    PointCourbe,
    PointsDeTerme,
    charger_contexte,
    echeancier_coupons,
    fraction_annee,
)
from fx.courbes import (
    CourbeActualisation,
    CourbesFX,
    bootstrap_courbe,
    construire_courbes,
    courbe_usd_depuis_points_de_terme,
    date_echeance,
    ecart_taux_implicite,
    lire_tenor,
    taux_swap_au_pair,
)

RACINE = Path(__file__).resolve().parent.parent
DV = date(2026, 11, 6)


@pytest.fixture(scope="module")
def contexte():
    return charger_contexte(RACINE / "config" / "valo_2026-11-06.json")


@pytest.fixture(scope="module")
def courbes(contexte) -> CourbesFX:
    return construire_courbes(contexte.marche, contexte.config)


def courbe_plate(devise: str, taux_pct: float, type_taux: str = "swap") -> Courbe:
    piliers = ["1M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y"]
    return Courbe(
        devise=devise,
        indice="TEST",
        type_taux=type_taux,
        base_calcul="ACT/360",
        points=tuple(PointCourbe(0.0 + i + 1, taux_pct, p) for i, p in enumerate(piliers)),
        source="valeurs de test",
        hypothese=True,
    )


# --- Ténors ----------------------------------------------------------------------------


def test_tenors():
    assert lire_tenor("10y") == (10, "Y")
    assert date_echeance(DV, "1W") == date(2026, 11, 13)
    assert date_echeance(DV, "3M") == date(2027, 2, 6)
    assert date_echeance(DV, "7Y") == date(2033, 11, 6)
    with pytest.raises(ErreurConfig):
        lire_tenor("1A")


# --- Propriétés de base ----------------------------------------------------------------


def test_df_zero_vaut_un(courbes):
    assert courbes.eur.df(0.0) == 1.0 and courbes.usd.df(0.0) == 1.0
    assert courbes.forward(0.0) == pytest.approx(courbes.spot, abs=1e-15)


def test_df_decroissants(courbes):
    for c in (courbes.eur, courbes.usd):
        assert all(b < a for a, b in zip(c.facteurs, c.facteurs[1:]))


def test_pas_d_extrapolation_au_dela_du_dernier_noeud(courbes):
    with pytest.raises(ValueError, match="au-delà"):
        courbes.eur.df(courbes.eur.t_max + 0.01)


def test_couvre_les_14_dates_de_coupon(contexte, courbes):
    assert echeancier_coupons(contexte.config)[-1].t_annees <= courbes.t_max


# --- Bootstrap -------------------------------------------------------------------------


def test_paiement_unique_jusqu_a_un_an(contexte, courbes):
    """1Y €STR : DF = 1 / (1 + S · α), α en ACT/360."""
    alpha = fraction_annee(DV, date(2027, 11, 6), "ACT/360")
    assert courbes.eur.df(1.0) == pytest.approx(1 / (1 + 0.0283 * alpha), rel=1e-14)


@pytest.mark.parametrize("devise", ["EUR", "USD"])
def test_tous_les_swaps_cotes_reprices_au_pair(contexte, courbes, devise):
    marche = contexte.marche
    source = marche.courbe_eur if devise == "EUR" else marche.courbe_usd
    courbe = courbes.eur if devise == "EUR" else courbes.usd
    for p in source.points:
        s = taux_swap_au_pair(courbe, DV, p.pilier, "ACT/360", "ACT/365F")
        assert s == pytest.approx(p.valeur / 100, abs=1e-12), p.pilier


def test_piliers_annuels_interpoles(contexte, courbes):
    assert courbes.eur.piliers_interpoles == ("4Y", "6Y", "8Y", "9Y")
    assert courbes.usd.piliers_interpoles == ("4Y", "6Y", "8Y", "9Y")
    # 4Y EUR = moyenne de 3Y (2,96) et 5Y (2,98) ; 8Y = 3,02 + (3,11 − 3,02) / 3
    for pilier, attendu in (("4Y", 0.0297), ("8Y", 0.0305)):
        s = taux_swap_au_pair(courbes.eur, DV, pilier, "ACT/360", "ACT/365F")
        assert s == pytest.approx(attendu, abs=1e-12)


def test_usd_6m_non_cote_interpole_entre_3m_et_1y(courbes):
    """Pas de nœud SOFR à 6M : DF log-linéaire entre les nœuds 3M et 1Y."""
    t6 = fraction_annee(DV, date(2027, 5, 6), "ACT/365F")
    assert all(abs(t - t6) > 1e-9 for t in courbes.usd.temps)
    t3, t12 = fraction_annee(DV, date(2027, 2, 6)), 1.0
    w = (t6 - t3) / (t12 - t3)
    attendu = math.exp((1 - w) * math.log(courbes.usd.df(t3)) + w * math.log(courbes.usd.df(t12)))
    assert courbes.usd.df(t6) == pytest.approx(attendu, rel=1e-14)


def test_courbe_plate_donne_taux_zero_quasi_constant():
    c = bootstrap_courbe(courbe_plate("EUR", 3.0), DV, "ACT/365F")
    zeros = [c.taux_zero(t) for t in (0.5, 1, 2, 5, 7, 10)]
    # Taux de swap 3 % ACT/360 annuel → taux zéro continu proche de 3 % × 365/360.
    assert max(zeros) - min(zeros) < 3e-4
    assert zeros[2] == pytest.approx(math.log(1 + 0.03 * 365 / 360), abs=2e-4)


def test_courbe_zero_lue_directement():
    c = bootstrap_courbe(courbe_plate("EUR", 2.0, "zero"), DV, "ACT/365F")
    t = fraction_annee(DV, date(2028, 11, 6))
    tau = fraction_annee(DV, date(2028, 11, 6), "ACT/360")
    assert c.df(t) == pytest.approx(math.exp(-0.02 * tau), rel=1e-14)


def test_courbe_invalide_refusee():
    with pytest.raises(ValueError):
        CourbeActualisation("EUR", (0.0, 1.0), (1.0, -0.5), (), "test", True)
    with pytest.raises(ValueError):
        CourbeActualisation("EUR", (0.1, 1.0), (1.0, 0.9), (), "test", True)


# --- Forward ---------------------------------------------------------------------------


def test_forward_parite_des_taux(courbes):
    for t in (0.25, 0.4959, 1.0, 3.0, 7.0055):
        f = courbes.forward(t)
        assert f * courbes.usd.df(t) == pytest.approx(courbes.spot * courbes.eur.df(t), rel=1e-14)
        assert f == pytest.approx(courbes.spot * math.exp(courbes.ecart_taux(t) * t), rel=1e-12)
        assert ecart_taux_implicite(courbes.spot, f, t) == pytest.approx(courbes.ecart_taux(t), rel=1e-12)


def test_forward_reel_croissant_car_sofr_au_dessus_de_estr(courbes):
    ts = [i / 20 for i in range(1, 141)]
    fwd = [courbes.forward(t) for t in ts]
    assert all(b > a for a, b in zip(fwd, fwd[1:]))
    assert all(courbes.ecart_taux(t) > 0 for t in ts)


@pytest.mark.parametrize("r_eur, r_usd, croissant", [(2.0, 4.0, True), (4.0, 2.0, False)])
def test_sens_du_forward_selon_ecart_de_taux(r_eur, r_usd, croissant):
    c = CourbesFX(
        spot=1.10,
        eur=bootstrap_courbe(courbe_plate("EUR", r_eur), DV, "ACT/365F"),
        usd=bootstrap_courbe(courbe_plate("USD", r_usd), DV, "ACT/365F"),
    )
    fwd = [c.forward(t) for t in (0.5, 1, 2, 5, 7)]
    assert all((b > a) == croissant for a, b in zip(fwd, fwd[1:]))


def test_taux_egaux_forward_egal_spot():
    c = CourbesFX(
        spot=1.10,
        eur=bootstrap_courbe(courbe_plate("EUR", 3.0), DV, "ACT/365F"),
        usd=bootstrap_courbe(courbe_plate("USD", 3.0), DV, "ACT/365F"),
    )
    assert c.forward(7.0) == pytest.approx(1.10, rel=1e-14)


def test_points_de_terme_redonnent_la_courbe_usd(courbes):
    """Points de terme tirés des courbes réelles → DF USD implicites identiques aux nœuds."""
    piliers = ["1Y", "2Y", "5Y", "7Y"]
    pts = []
    for p in piliers:
        t = fraction_annee(DV, date_echeance(DV, p))
        pts.append(PointCourbe(t, (courbes.forward(t) - courbes.spot) * 10_000, p))
    terme = PointsDeTerme(points=tuple(pts), source="test", hypothese=True)
    usd = courbe_usd_depuis_points_de_terme(courbes.eur, courbes.spot, terme, DV, "ACT/365F")
    for p in pts:
        t = fraction_annee(DV, date_echeance(DV, p.pilier))
        assert usd.df(t) == pytest.approx(courbes.usd.df(t), rel=1e-12)
    assert usd.hypothese


def test_interpolation_inconnue_refusee():
    from fx.config import config_depuis_dict, lire_json

    brut = lire_json(RACINE / "config" / "valo_2026-11-06.json")
    brut["conventions"]["interpolation_courbes"]["facteurs_actualisation"] = "spline"
    with pytest.raises(ErreurConfig, match="facteurs_actualisation"):
        config_depuis_dict(brut, RACINE)
