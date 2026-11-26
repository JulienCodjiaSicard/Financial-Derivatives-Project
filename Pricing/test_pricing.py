"""Tests du pricer : python -m pytest  (depuis le dossier Pricing)."""
import numpy as np
import pytest

import Longstaff as L
from donnees import (A, SIGMA_R, RHO_FX_TAUX, FX_VOLS_ATM, BARRIERES_SMILE, DATES_COUPON, CALLS, N, MARGE,
                     NB_SCENARIOS, recuperer_donnees, survie)


@pytest.fixture(scope="module")
def mod():
    cours_fx, courbe_usd, courbe_eur, _, _ = recuperer_donnees()
    return {"eur": courbe_eur, "usd": courbe_usd, "a": A, "sigma_r": SIGMA_R,
            "FX0": cours_fx[-1], "X0": 1 / cours_fx[-1], "x": cours_fx.max(),
            "barrieres": BARRIERES_SMILE, "vols_fx": FX_VOLS_ATM, "rho_X": -RHO_FX_TAUX,
            "survie": survie(DATES_COUPON)}


def test_produit():
    assert DATES_COUPON[0] == 0.5 and DATES_COUPON[-1] == 7.0 and len(DATES_COUPON) == 14   # 1er coupon à 6 mois
    assert [DATES_COUPON[i] for i in CALLS] == list(np.arange(2.0, 7.0, 0.5))                # calls de 2 à 6,5 ans


def test_donnees_de_marche(mod):
    assert mod["FX0"] == pytest.approx(1.1052) and mod["x"] == pytest.approx(1.1770)
    assert L.zero_coupon(mod["eur"], 7.0) == pytest.approx(0.80311, abs=1e-5)    # contrôle donné par le groupe Taux
    assert L.zero_coupon(mod["usd"], 7.0) == pytest.approx(0.72072, abs=1e-5)
    assert np.all(np.diff(mod["survie"]) < 0) and 0.8 < mod["survie"][-1] < 1


def test_controles(mod):
    assert L.lancer_controles(mod)


def test_coupon_et_option(mod):
    sim_A = L.simuler(mod, NB_SCENARIOS, L.GRAINE_APPRENTISSAGE)
    sim_B = L.simuler(mod, NB_SCENARIOS, L.GRAINE_PRICING)
    C = L.trouver_coupon(mod, sim_A, sim_B)
    res = L.prix_note(mod, sim_A, sim_B, C)
    assert res["V0"] == pytest.approx(N * (1 - MARGE), abs=1e3)
    assert res["call"] > 0                                   # l'option de la banque a une valeur positive
    assert res["V0"] <= res["V0_sans_call"]                  # la note callable vaut moins que la note simple
    assert C > 0.0325                                        # contrainte de l'investisseur : C > souverain 7 ans


def test_backtest_raccorde_au_13_11(mod):
    """Au 13/11, le marché reconstruit par le backtest est celui du pricing (aucun décalage de courbe ni de vol)."""
    import pandas as pd
    import Backtest as B
    h = B.historiques()
    base = {"eur": mod["eur"], "usd": mod["usd"], "x": mod["x"]}
    m = B.marche_a_la_date(B.FIN, h, base, mod["x"])
    assert np.allclose(m["eur"][1], mod["eur"][1]) and np.allclose(m["usd"][1], mod["usd"][1])
    assert m["sigma_r"] == pytest.approx(mod["sigma_r"]) and m["FX0"] == pytest.approx(mod["FX0"])
    assert B.EMISSION == pd.Timestamp("2026-05-15") and len(m["dates"]) == 14 and m["dates"][0] > 0
