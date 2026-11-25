"""Intégration avec le sous-groupe taux : valorisation au 13/11/2026 sur ses courbes.

Les facteurs d'actualisation EUR (swaps Euribor 6M) et USD (par yields Treasury) sont lus
dans data/groupe_taux/, copie à l'identique des sorties du sous-groupe taux (output/) pour que
le dossier Change fonctionne seul.
"""

from __future__ import annotations

import copy
import csv
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from fx.config import (
    ErreurConfig,
    charger_contexte,
    config_depuis_dict,
    echeancier_coupons,
    lire_json,
    valider_marche,
)
from fx.courbes import construire_courbes
from fx.digitales import proba_replication, valoriser_coupons
from fx.smile import construire_surface

RACINE = Path(__file__).resolve().parent.parent
CONFIG = RACINE / "config" / "valo_2026-11-13.json"
SORTIES_TAUX = RACINE / "data" / "groupe_taux"
# Dossier d'origine du sous-groupe taux, s'il est présent à côté de Change/ (facultatif).
DOSSIER_TAUX = RACINE.parent / "Taux EUR courbe de taux  calibration de Hull-White sur les swaptions" / "output"
FICHIERS_TAUX = ("curve_eur.csv", "curve_usd.csv", "discount_coupon_dates.csv", "hw_params.json")


@pytest.fixture(scope="module")
def contexte():
    return charger_contexte(CONFIG)


@pytest.fixture(scope="module")
def courbes(contexte):
    return construire_courbes(contexte.marche, contexte.config)


@pytest.fixture(scope="module")
def surface(contexte, courbes):
    return construire_surface(contexte.marche, courbes, contexte.config)


@pytest.fixture
def bruts():
    return lire_json(CONFIG), lire_json(RACINE / "data" / "marche_2026-11-13.json")


def facteurs_groupe_taux() -> list[tuple[float, float, float]]:
    with (SORTIES_TAUX / "discount_coupon_dates.csv").open(encoding="utf-8-sig", newline="") as f:
        return [(float(r["t"]), float(r["P_eur"]), float(r["P_usd"])) for r in csv.DictReader(f)]


# --- Copie locale des sorties du sous-groupe taux ------------------------------------------


def test_copie_locale_complete():
    assert all((SORTIES_TAUX / f).is_file() for f in FICHIERS_TAUX)


@pytest.mark.skipif(not DOSSIER_TAUX.exists(), reason="dossier du sous-groupe taux absent : Change/ fonctionne seul")
@pytest.mark.parametrize("nom", FICHIERS_TAUX)
def test_copie_locale_a_jour(nom):
    """Si le sous-groupe taux a relancé curves.py, recopier ses sorties dans data/groupe_taux/."""
    assert (SORTIES_TAUX / nom).read_bytes() == (DOSSIER_TAUX / nom).read_bytes(), (
        f"data/groupe_taux/{nom} diffère de la sortie du sous-groupe taux : la recopier puis relancer run_all.py"
    )


# --- Données et date commune --------------------------------------------------------------


def test_date_commune_avec_le_sous_groupe_taux(contexte):
    hw = lire_json(SORTIES_TAUX / "hw_params.json")
    assert contexte.config.date_valorisation == date.fromisoformat(hw["date"]) == date(2026, 11, 13)


def test_spot_bce_du_13_11_et_barriere_inchangee(contexte):
    assert contexte.marche.spot == pytest.approx(1.1052)
    assert contexte.barriere.niveau == pytest.approx(1.1770)
    assert contexte.barriere.date_pic == date(2026, 5, 7)


def test_fixings_identiques_a_l_ancien_fichier_sur_la_fenetre():
    from fx.config import lire_fixings_bce

    ancien = dict(lire_fixings_bce(RACINE / "data" / "eurusd_bce_2026-05-06_2026-11-06.csv"))
    nouveau = dict(lire_fixings_bce(RACINE / "data" / "eurusd_bce_2026-05-06_2026-11-13.csv"))
    assert all(nouveau[d] == x for d, x in ancien.items())


def test_coupons_aux_dates_du_sous_groupe_taux(contexte):
    coupons = echeancier_coupons(contexte.config)
    assert len(coupons) == 14
    assert coupons[0].date == date(2027, 5, 13) and coupons[-1].date == date(2033, 11, 13)
    # t en ACT/365F à quelques jours près des t = 0,5 ... 7 ans du sous-groupe taux
    assert max(abs(c.t_annees - 0.5 * c.numero) for c in coupons) < 0.01


# --- Courbes ----------------------------------------------------------------------------------


def test_df_identiques_a_ceux_du_sous_groupe_taux(courbes):
    for t, p_eur, p_usd in facteurs_groupe_taux():
        assert courbes.eur.df(t) == pytest.approx(p_eur, abs=1e-14)
        assert courbes.usd.df(t) == pytest.approx(p_usd, abs=1e-14)


def test_forward_par_parite_des_taux(courbes):
    for t, p_eur, p_usd in facteurs_groupe_taux():
        assert courbes.forward(t) == pytest.approx(1.1052 * p_eur / p_usd, rel=1e-12)
    assert courbes.forward(7.0) > courbes.forward(1.0) > courbes.spot  # r_USD > r_EUR


def test_courbes_marquees_comme_venant_du_sous_groupe_taux(contexte):
    for c in (contexte.marche.courbe_eur, contexte.marche.courbe_usd):
        assert c.type_taux == "df" and not c.hypothese
        assert c.fichier is not None and c.fichier.parent.resolve() == SORTIES_TAUX.resolve()


def test_fichier_de_courbe_absent_message_clair(bruts):
    config_brute, marche_brut = bruts
    marche_brut["courbes"]["USD"]["fichier"] = "../absent/curve_usd.csv"
    with pytest.raises(ErreurConfig, match="Fichier de courbe introuvable"):
        valider_marche(marche_brut, config_depuis_dict(config_brute, RACINE))


def test_colonne_absente_refusee(bruts):
    config_brute, marche_brut = bruts
    marche_brut["courbes"]["EUR"]["colonne_df"] = "P_inconnu"
    with pytest.raises(ErreurConfig, match="P_inconnu"):
        valider_marche(marche_brut, config_depuis_dict(config_brute, RACINE))


def test_fichier_avec_type_swap_refuse(bruts):
    config_brute, marche_brut = bruts
    marche_brut["courbes"]["EUR"]["type_taux"] = "swap"
    with pytest.raises(ErreurConfig, match="type_taux"):
        valider_marche(marche_brut, config_depuis_dict(config_brute, RACINE))


def test_type_df_sans_fichier_refuse(bruts):
    config_brute, marche_brut = bruts
    eur = copy.deepcopy(marche_brut["courbes"]["EUR"])
    del eur["fichier"]
    eur["points"] = [{"pilier": "1Y", "maturite_annees": 1.0, "taux_pct": 2.0}]
    marche_brut["courbes"]["EUR"] = eur
    with pytest.raises(ErreurConfig, match="fichier"):
        valider_marche(marche_brut, config_depuis_dict(config_brute, RACINE))


# --- Smile et coupons sur ces courbes ------------------------------------------------------------


@pytest.mark.parametrize("t", [0.5, 1.0, 3.0, 7.0])
def test_smile_sans_arbitrage_proba_croissante_en_k(courbes, surface, t):
    """P(X_T < K) croît avec K sur une large plage (pas de saut ni de baisse aux piliers 25Δ)."""
    dfd, dff = courbes.usd.df(t), courbes.eur.df(t)
    ks = np.linspace(0.95, 1.45, 201)
    p = np.array([proba_replication(courbes.spot, k, t, dfd, dff, lambda x: surface.vol(t, x)) for k in ks])
    vol_atm = float(surface.vols_delta(t).vol_atm)
    p_plate = np.array([proba_replication(courbes.spot, k, t, dfd, dff, lambda x: np.full_like(x, vol_atm)) for k in ks])
    assert np.all((p > 0) & (p < 1))
    assert np.all(np.diff(p) > 0)
    # Pas de saut : la densité avec smile reste du même ordre que la densité à vol plate
    # (le smile de Projet_FX, plat hors des piliers 25Δ, sautait de 1,7 point à 6 mois).
    assert np.max(np.diff(p)) < 1.5 * np.max(np.diff(p_plate))


def test_coupons_coherents(contexte, courbes, surface):
    res = valoriser_coupons(contexte.config, courbes, surface, contexte.barriere)
    assert len(res) == 14
    for r in res:
        assert 0 < r.p_smile < 1
        assert r.df_eur == pytest.approx(courbes.eur.df(r.t))
    # Forward croissant (r_USD > r_EUR) : la probabilité de rester sous K baisse avec T
    assert all(b.p_smile < a.p_smile for a, b in zip(res, res[1:]))
