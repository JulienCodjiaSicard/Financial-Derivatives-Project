"""Corrélation taux EUR / change (ρ du sujet, section 4.3) et son export vers le moteur."""

from __future__ import annotations

import copy
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from fx.config import ErreurConfig, charger_config, config_depuis_dict, lire_json
from fx.correlation import (
    calculer_correlations,
    estimer_correlation,
    lire_estr_bce,
    lire_taux_investing,
)
from fx.historique import lire_historique_sdmx

RACINE = Path(__file__).resolve().parent.parent
CONFIG = RACINE / "config" / "valo_2026-11-13.json"
VALO = date(2026, 11, 13)


def jours_ouvres(debut: date, fin: date) -> list[date]:
    d, out = debut, []
    while d <= fin:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def series_synthetiques(rho: float, n_ans: int = 2, graine: int = 1):
    """FX (USD pour 1 EUR) et taux (%) dont les chocs (Δr, Δln X = −Δln FX) ont la corrélation rho."""
    dates = jours_ouvres(date(VALO.year - n_ans, VALO.month, VALO.day), VALO)
    rng = np.random.default_rng(graine)
    z_r = rng.standard_normal(len(dates))
    z_x = rho * z_r + math.sqrt(1 - rho**2) * rng.standard_normal(len(dates))
    taux = 2.0 + 0.03 * np.cumsum(z_r)
    fx = 1.10 * np.exp(-0.004 * np.cumsum(z_x))  # ln X = −ln FX
    return tuple(zip(dates, fx)), tuple(zip(dates, taux))


# --- Estimateur ---------------------------------------------------------------------------------


@pytest.mark.parametrize("rho", [-0.6, 0.0, 0.4])
def test_retrouve_la_correlation_des_chocs(rho):
    fx, taux = series_synthetiques(rho)
    n, r, bas, haut, _, _, _ = estimer_correlation(fx, taux, date(2024, 11, 13), VALO, 1)
    assert n > 500
    assert bas < r < haut
    assert r == pytest.approx(rho, abs=0.1)


def test_convention_de_signe_x_egal_un_sur_fx():
    """Taux EUR qui monte quand l'EUR/USD baisse (dollar qui monte, X = EUR pour 1 USD monte) : ρ = +1."""
    dates = jours_ouvres(date(2025, 11, 13), VALO)
    chocs = np.random.default_rng(3).standard_normal(len(dates))
    taux = tuple(zip(dates, 2 + 0.01 * np.cumsum(chocs)))
    fx = tuple(zip(dates, 1.1 * np.exp(-0.005 * np.cumsum(chocs))))
    assert estimer_correlation(fx, taux, dates[0], VALO, 1)[1] == pytest.approx(1.0)


def test_seules_les_dates_communes_comptent():
    fx, taux = series_synthetiques(0.3, n_ans=1)
    trou = tuple((d, x) for i, (d, x) in enumerate(taux) if i % 7)  # un taux sur sept manquant
    n_complet = estimer_correlation(fx, taux, date(2025, 11, 13), VALO, 1)[0]
    n_trou = estimer_correlation(fx, trou, date(2025, 11, 13), VALO, 1)[0]
    assert n_trou < n_complet


def test_pas_hebdomadaire_sans_chevauchement():
    fx, taux = series_synthetiques(0.3, n_ans=1)
    n1 = estimer_correlation(fx, taux, date(2025, 11, 13), VALO, 1)[0]
    n5 = estimer_correlation(fx, taux, date(2025, 11, 13), VALO, 5)[0]
    assert n5 == pytest.approx(n1 / 5, abs=1)


# --- Lecture des fichiers ------------------------------------------------------------------------


def test_lecture_estr_bce(tmp_path):
    f = tmp_path / "estr.csv"
    f.write_text('"DATE","TIME PERIOD","Euro short-term rate"\n"2026-11-11","11 Nov 2026","2.441"\n'
                 '"2026-11-12","12 Nov 2026",""\n"2026-11-13","13 Nov 2026","2.443"\n', encoding="utf-8")
    assert lire_estr_bce(f) == ((date(2026, 11, 11), 2.441), (date(2026, 11, 13), 2.443))


def test_lecture_investing_virgule_decimale_et_ordre_croissant(tmp_path):
    f = tmp_path / "swap.csv"
    f.write_text('﻿"Date","Dernier","Ouv."\n"13/11/2026","2,407","2,356"\n"12/11/2026","2,359","2,355"\n',
                 encoding="utf-8")
    assert lire_taux_investing(f) == ((date(2026, 11, 12), 2.359), (date(2026, 11, 13), 2.407))


def test_lecture_format_inattendu(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ErreurConfig, match="Dernier"):
        lire_taux_investing(f)
    with pytest.raises(ErreurConfig, match="DATE"):
        lire_estr_bce(f)


# --- Config --------------------------------------------------------------------------------------


def test_section_facultative():
    assert charger_config(RACINE / "config" / "valo_2026-11-06.json").correlation is None


def test_choix_retenu_declare_comme_hypothese():
    config = charger_config(CONFIG)
    assert config.correlation.retenue.serie == "swap_eur_2y"
    assert "correlation_taux_change" in {h.nom for h in config.hypotheses}


def test_serie_retenue_inconnue_refusee():
    brut = copy.deepcopy(lire_json(CONFIG))
    brut["correlation_taux_change"]["retenue"]["serie"] = "inconnue"
    with pytest.raises(ErreurConfig, match="inconnue"):
        config_depuis_dict(brut, RACINE)


def test_choix_retenu_sans_justification_refuse():
    brut = copy.deepcopy(lire_json(CONFIG))
    del brut["correlation_taux_change"]["retenue"]["justification"]
    with pytest.raises(ErreurConfig, match="justification"):
        config_depuis_dict(brut, RACINE)


def test_fenetre_non_couverte_ecartee_et_choix_impossible_refuse():
    config = charger_config(CONFIG)
    fx, taux = series_synthetiques(0.2, n_ans=1)
    series = {nom: taux for nom in config.correlation.series}
    res = calculer_correlations(config.correlation, fx, VALO, series)
    assert {e.fenetre_annees for e in res.estimations} == {1}
    assert len(res.fenetres_non_couvertes) == len(config.correlation.series)
    brut = copy.deepcopy(lire_json(CONFIG))
    brut["correlation_taux_change"]["retenue"]["fenetre_annees"] = 2
    with pytest.raises(ErreurConfig, match="non calculable"):
        calculer_correlations(config_depuis_dict(brut, RACINE).correlation, fx, VALO, series)


# --- Données réelles -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reel():
    config = charger_config(CONFIG)
    return calculer_correlations(config.correlation, lire_historique_sdmx(config.chemin_historique), VALO)


def test_donnees_reelles_jusqu_a_la_date_de_valorisation(reel):
    assert all(e.fin <= VALO for e in reel.estimations)
    r = reel.retenue
    assert (r.serie, r.fenetre_annees, r.pas_jours) == ("swap_eur_2y", 1, 1)
    assert r.nb_variations > 240 and -1 < r.ic95_bas < r.rho < r.ic95_haut < 1


def test_estr_journalier_proche_de_zero(reel):
    """L'€STR bouge par paliers BCE : sa corrélation journalière est non significative."""
    for e in reel.estimations:
        if e.serie == "estr" and e.pas_jours == 1:
            assert e.ic95_bas < 0 < e.ic95_haut
