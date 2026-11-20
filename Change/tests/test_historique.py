"""Tests de l'étape 8 : analyse historique de la barrière."""

from __future__ import annotations

import csv
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from fx.config import ErreurConfig, charger_contexte, lire_fixings_bce
from fx.courbes import construire_courbes
from fx.digitales import valoriser_coupons
from fx.historique import (
    analyser,
    ecrire_csv,
    lire_historique_sdmx,
    rendements_glissants,
    statistique_horizon,
    tracer,
    tronquer,
    vol_realisee,
)
from fx.smile import construire_surface

RACINE = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def contexte():
    return charger_contexte(RACINE / "config" / "valo_2026-11-06.json")


@pytest.fixture(scope="module")
def historique(contexte):
    return lire_historique_sdmx(contexte.config.chemin_historique)


@pytest.fixture(scope="module")
def analyse(contexte, historique):
    cfg = contexte.config
    c = construire_courbes(contexte.marche, cfg)
    s = construire_surface(contexte.marche, c, cfg)
    res = valoriser_coupons(cfg, c, s, contexte.barriere)
    return analyser(historique, cfg.date_valorisation, c.spot, contexte.barriere.niveau, res,
                    cfg.sous_periode_annees, cfg.vol_realisee_jours, cfg.jours_ouvres_par_an,
                    {p.pilier: p.atm_pct / 100 for p in contexte.marche.piliers})


# --- Données ---------------------------------------------------------------------------


def test_historique_bce_lu(historique):
    assert historique[0] == (date(1999, 1, 4), 1.1789)
    assert len(historique) > 7000
    assert all(b[0] > a[0] for a, b in zip(historique, historique[1:]))


def test_historique_coherent_avec_la_fenetre_de_la_barriere(historique):
    """Les 133 fixings du CSV de la barrière sont identiques dans l'historique long."""
    court = lire_fixings_bce(RACINE / "data" / "eurusd_bce_2026-05-06_2026-11-06.csv")
    long = dict(historique)
    assert all(long[d] == pytest.approx(x, abs=1e-12) for d, x in court)


def test_jours_feries_ignores_et_serie_controlee(tmp_path):
    f = tmp_path / "h.csv"
    f.write_text(
        "KEY,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"
        "EXR.D.USD.EUR.SP00.A,2020-12-24,1.2,A\n"
        "EXR.D.USD.EUR.SP00.A,2020-12-25,,H\n"
        "EXR.D.USD.EUR.SP00.A,2020-12-28,1.21,A\n",
        encoding="utf-8",
    )
    assert lire_historique_sdmx(f) == ((date(2020, 12, 24), 1.2), (date(2020, 12, 28), 1.21))
    f.write_text("KEY,TIME_PERIOD,OBS_VALUE\nEXR.D.GBP.EUR.SP00.A,2020-12-24,0.9\n", encoding="utf-8")
    with pytest.raises(ErreurConfig, match="série"):
        lire_historique_sdmx(f)


def test_pas_de_donnees_apres_la_date_de_valorisation(analyse, contexte):
    assert analyse.fin == contexte.config.date_valorisation
    assert all(d <= date(2026, 11, 6) for d, _ in tronquer([(date(2026, 11, 6), 1.0), (date(2026, 11, 9), 1.1)], date(2026, 11, 6)))


# --- Fenêtres glissantes ---------------------------------------------------------------


def serie_jouets():
    """Fixings ouvrés de 2020 à 2022 sur une tendance de +10 % par an (rendement connu)."""
    d, out = date(2020, 1, 1), []
    while d <= date(2022, 12, 31):
        if d.weekday() < 5:
            out.append((d, 1.0 * math.exp(math.log(1.1) * (d - date(2020, 1, 1)).days / 365)))
        d += timedelta(days=1)
    return tuple(out)


def test_rendements_glissants_fixing_de_fin_le_plus_proche_avant():
    fx = serie_jouets()
    r = rendements_glissants(fx, 12, date(2022, 12, 31))
    d0, d1, x = r[0]
    assert d0 == date(2020, 1, 1) and d1 == date(2021, 1, 1)  # échéance un vendredi : fixing du jour
    # Départ le vendredi 03/01/2020 : échéance le dimanche 03/01/2021 → fixing du vendredi 01/01/2021
    depart = {a: b for a, b, _ in r}
    assert depart[date(2020, 1, 3)] == date(2021, 1, 1)
    assert all(e <= date(2022, 12, 31) for _, e, _ in r)
    assert x == pytest.approx(math.exp(math.log(1.1) * (d1 - d0).days / 365) - 1, rel=1e-12)


def test_frequence_hausse_tendance_connue():
    fx = serie_jouets()
    # Hausse d'environ 10 % sur 12 mois à chaque fenêtre : toutes au-dessus de 9 %, aucune au-dessus de 11 %
    assert statistique_horizon(fx, 12, 0.09, date(2022, 12, 31)).frequence_hausse == 1.0
    assert statistique_horizon(fx, 12, 0.11, date(2022, 12, 31)).frequence_hausse == 0.0
    st = statistique_horizon(fx, 12, 0.10, date(2022, 12, 31))
    assert st.nb_independantes == 2 and st.p_empirique_coupon == pytest.approx(1 - st.frequence_hausse)


def test_vol_realisee_serie_connue():
    rng = np.random.default_rng(1)
    rend = rng.normal(0, 0.06 / math.sqrt(252), 5000)
    x = np.exp(np.cumsum(np.concatenate([[0.0], rend])))
    fx = tuple((date(2000, 1, 1) + timedelta(days=i), float(v)) for i, v in enumerate(x))
    assert vol_realisee(fx, 5000, 252) == pytest.approx(0.06, rel=0.03)
    attendu = float(np.std(rend[-252:], ddof=1) * math.sqrt(252))
    assert vol_realisee(fx, 252, 252) == pytest.approx(attendu, rel=1e-12)


# --- Analyse réelle --------------------------------------------------------------------


def test_analyse_reelle(analyse):
    assert analyse.seuil == pytest.approx(1.1770 / 1.1165 - 1, rel=1e-12)
    assert [l.mois for l in analyse.lignes] == list(range(6, 85, 6))
    assert analyse.debut_sous_periode == date(2016, 11, 6)
    for l in analyse.lignes:
        assert 0 < l.complet.p_empirique_coupon < 1
        assert l.complet.nb_fenetres > l.sous_periode.nb_fenetres
    # Vol implicite au-dessus de la vol réalisée récente (prime de volatilité)
    for p in ("1M", "3M", "6M", "1Y"):
        assert analyse.vols_implicites[p] > analyse.vols_realisees[p]


def test_sorties(analyse, tmp_path):
    c1, c2 = ecrire_csv(analyse, tmp_path)
    with c1.open(encoding="utf-8-sig") as f:
        l1 = list(csv.reader(f, delimiter=";"))
    assert len(l1) == 15 and l1[1][1] == "6"
    with c2.open(encoding="utf-8-sig") as f:
        l2 = list(csv.reader(f, delimiter=";"))
    assert [r[0] for r in l2[1:5]] == ["1M", "3M", "6M", "1Y"]
    svg = tmp_path / "h.svg"
    tracer(analyse, svg)
    assert svg.read_text(encoding="utf-8").lstrip().startswith("<?xml")
