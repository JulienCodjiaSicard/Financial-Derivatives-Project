"""Tests de l'étape 7 : vol effective et export vers le moteur Monte Carlo."""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path

import numpy as np
import pytest

from fx.config import charger_contexte
from fx.courbes import construire_courbes
from fx.digitales import valoriser_coupons
from fx.export_mc import construire_export, ecrire_export
from fx.gk import probabilite_eur_sous_barriere
from fx.smile import VolsDelta, construire_surface
from fx.vol_effective import ErreurVolEffective, barriere_effective, racines_sigma, vols_effectives

RACINE = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def contexte():
    return charger_contexte(RACINE / "config" / "valo_2026-11-06.json")


@pytest.fixture(scope="module")
def courbes(contexte):
    return construire_courbes(contexte.marche, contexte.config)


@pytest.fixture(scope="module")
def surface(contexte, courbes):
    return construire_surface(contexte.marche, courbes, contexte.config)


@pytest.fixture(scope="module")
def resultats(contexte, courbes, surface):
    return valoriser_coupons(contexte.config, courbes, surface, contexte.barriere)


@pytest.fixture(scope="module")
def vols(resultats, courbes, contexte):
    return vols_effectives(resultats, courbes, contexte.barriere.niveau)


def test_sigma_eff_redonne_la_probabilite_avec_smile(vols, courbes, contexte):
    """Test du plan : σ_eff réinjecté dans Garman-Kohlhagen redonne P_smile à 1e-6 près."""
    sorties, avert = vols
    assert avert == ()
    k = contexte.barriere.niveau
    for v in sorties:
        p = float(probabilite_eur_sous_barriere(courbes.spot, k, v.t, v.sigma_eff, v.df_usd, v.df_eur))
        assert p == pytest.approx(v.p_smile, abs=1e-6)
        assert v.p_reproduite == pytest.approx(v.p_smile, abs=1e-12) and not v.corrigee


def test_variance_totale_croissante(vols):
    w = [v.variance_totale for v in vols[0]]
    assert all(b > a for a, b in zip(w, w[1:]))


def test_choix_de_racine_continu(vols):
    """Après le passage F > K (4 ans), deux racines : on garde la branche continue (haute)."""
    sorties = vols[0]
    assert [v.nb_racines for v in sorties] == [1] * 7 + [2] * 7
    sig = [v.sigma_eff for v in sorties]
    assert all(b > a for a, b in zip(sig, sig[1:]))
    v8 = sorties[7]
    assert v8.forward > 1.1770 and v8.sigma_eff > math.sqrt(2 * math.log(v8.forward / 1.1770) / v8.t)


def test_smile_plat_sigma_eff_egal_vol_plate(contexte, courbes, surface):
    """Smile plat : σ_eff = σ à toutes les dates, y compris quand F > K (branche basse)."""
    plat = dataclasses.replace(surface, vols=tuple(VolsDelta(0.0676, 0.0676, 0.0676) for _ in surface.vols))
    res = valoriser_coupons(contexte.config, courbes, plat, contexte.barriere)
    sorties, avert = vols_effectives(res, courbes, contexte.barriere.niveau)
    assert avert == ()
    assert [v.sigma_eff for v in sorties] == pytest.approx([0.0676] * 14, abs=1e-6)


def test_deux_racines_quand_k_sous_le_forward():
    s, t, dfd, dff = 1.10, 5.0, 0.80, 0.86  # F = 1,1825 > K
    k = 1.15
    p_vol = float(probabilite_eur_sous_barriere(s, k, t, 0.07, dfd, dff))
    r = racines_sigma(s, k, t, dfd, dff, p_vol)
    assert len(r) == 2 and r[0] == pytest.approx(0.07, abs=1e-10)
    for x in r:
        assert float(probabilite_eur_sous_barriere(s, k, t, x, dfd, dff)) == pytest.approx(p_vol, abs=1e-12)


def test_probabilite_inatteignable_refusee():
    # F > K : P plafonnée à son maximum en σ_crit (< 50 %), 0,6 est inatteignable
    with pytest.raises(ErreurVolEffective, match="Aucune vol"):
        racines_sigma(1.10, 1.15, 5.0, 0.80, 0.86, 0.60)


def test_correction_si_variance_decroissante(resultats, courbes, contexte):
    """Forcer une probabilité qui fait baisser la variance totale : correction minimale signalée."""
    res = list(resultats)
    # Date 2 : probabilité très élevée → σ_eff très faible → variance totale < date 1
    res[1] = dataclasses.replace(res[1], p_smile=0.9999)
    sorties, avert = vols_effectives(res[:3], courbes, contexte.barriere.niveau)
    assert len(avert) == 1 and "Coupon 2" in avert[0] and "relevé" in avert[0]
    assert sorties[1].corrigee
    assert sorties[1].variance_totale == pytest.approx(sorties[0].variance_totale * (1 + 1e-9), rel=1e-12)


def test_barriere_effective(vols, courbes):
    for v in vols[0]:
        p = float(probabilite_eur_sous_barriere(courbes.spot, v.k_effective_vol_atm, v.t, v.vol_atm, v.df_usd, v.df_eur))
        assert p == pytest.approx(v.p_smile, abs=1e-12)
    assert barriere_effective(1.1, 1.0, 0.07, 0.96, 0.97, 0.5) == pytest.approx(1.1 * 0.97 / 0.96 * math.exp(0.5 * 0.07**2), rel=1e-12)


@pytest.fixture(scope="module")
def export(contexte, courbes, vols, tmp_path_factory):
    sorties, avert = vols
    e = construire_export(contexte.config, contexte.marche, courbes, contexte.barriere, sorties, avert)
    chemin = tmp_path_factory.mktemp("export") / "export_mc.json"
    ecrire_export(e, chemin)
    return json.loads(chemin.read_text(encoding="utf-8"))


def test_export_json_livre_la_barriere_effective(export, vols):
    sorties = vols[0]
    assert export["date_valorisation"] == "2026-11-06" and export["barriere_K"] == pytest.approx(1.1770)
    pm = export["pour_pricer_note_ece"]
    assert set(pm) == {"fx_spot", "fx_vol_tenors", "fx_atm_vols", "fx_barrier", "usd_ois", "changement_moteur", "note"}
    assert pm["fx_spot"] == pytest.approx(1.1165)
    assert pm["fx_vol_tenors"] == pytest.approx([v.t for v in sorties])
    assert pm["fx_atm_vols"] == pytest.approx([v.vol_atm for v in sorties])  # vols de marché, pas σ_eff
    assert pm["fx_barrier"] == pytest.approx([v.k_effective_vol_atm for v in sorties])
    assert "reshape(-1, 1)" in pm["changement_moteur"]
    assert set(pm["usd_ois"]) == {"tenors", "zeros"}  # format Curve(tenors, zeros) du moteur


def test_sigma_eff_seulement_en_information_jusqu_a_2_ans(export):
    for d in export["dates_coupon"]:
        if d["T"] <= 2.0:
            assert d["sigma_eff_information"] is not None
        else:
            assert d["sigma_eff_information"] is None
    assert any("σ_eff non reporté au-delà de 2 ans" in a for a in export["avertissements"])
    assert any("Date de cotation" in a for a in export["avertissements"])


def test_usd_ois_au_format_moteur_redonne_nos_df(export, courbes):
    """Le moteur interpole linéairement les taux zéro : aux dates de coupon, DF identiques aux nôtres."""
    o = export["pour_pricer_note_ece"]["usd_ois"]
    assert all(b > a for a, b in zip(o["tenors"], o["tenors"][1:]))
    for t in export["pour_pricer_note_ece"]["fx_vol_tenors"]:
        z = float(np.interp(t, o["tenors"], o["zeros"]))
        assert math.exp(-z * t) == pytest.approx(courbes.usd.df(t), rel=1e-12)
    for t in courbes.usd.temps[1:]:  # les nœuds de la courbe sont aussi présents
        assert any(abs(t - x) < 1e-9 for x in o["tenors"])


def test_moteur_simule_retrouve_les_probabilites_avec_smile(export):
    """Imitation du moteur à partir du seul JSON : taux zéro interpolés linéairement, variance
    totale interpolée linéairement, EUR/USD lognormal sous la mesure EUR, comparaison à
    fx_barrier date par date. Doit retrouver P_smile à l'erreur Monte Carlo près."""
    pm, eur = export["pour_pricer_note_ece"], export["reference_eur_ois_sous_groupe_fx"]
    ctrl = export["controle_moteur"]
    ts = np.array(pm["fx_vol_tenors"])
    w = np.array(pm["fx_atm_vols"]) ** 2 * ts
    rng = np.random.default_rng(7)
    z = rng.standard_normal(400_000)
    z = np.concatenate([z, -z])
    for t, k_eff, p_att in zip(ts, pm["fx_barrier"], ctrl["p_smile_attendues"]):
        z_usd = np.interp(t, pm["usd_ois"]["tenors"], pm["usd_ois"]["zeros"])
        z_eur = np.interp(t, eur["tenors"], eur["zeros"])
        f = pm["fx_spot"] * math.exp((z_usd - z_eur) * t)
        var = float(np.interp(t, ts, w))
        x_t = f * np.exp(0.5 * var + math.sqrt(var) * z)  # mesure EUR
        assert np.mean(x_t < k_eff) == pytest.approx(p_att, abs=2e-3)
    assert ctrl["tolerance_points"] == pytest.approx(1.0)


def test_moteur_interpolant_la_variance_totale_retrouve_les_probabilites(vols, courbes, contexte):
    """Simulation simple d'un moteur : variance totale interpolée linéairement entre les
    dates fournies ; aux dates fournies, les probabilités sont celles du smile."""
    sorties = vols[0]
    ts = np.array([v.t for v in sorties])
    ws = np.array([v.variance_totale for v in sorties])
    k = contexte.barriere.niveau
    for v in sorties:
        w = float(np.interp(v.t, ts, ws))
        sigma = math.sqrt(w / v.t)
        p = float(probabilite_eur_sous_barriere(courbes.spot, k, v.t, sigma, v.df_usd, v.df_eur))
        assert p == pytest.approx(v.p_smile, abs=1e-10)
