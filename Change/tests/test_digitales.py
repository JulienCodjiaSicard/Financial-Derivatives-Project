"""Tests de l'étape 5 : les 14 coupons digitaux."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from fx.config import charger_contexte
from fx.courbes import construire_courbes
from fx.digitales import ecrire_csv, proba_replication, tracer_probabilites, valoriser_coupons
from fx.gk import probabilite_eur_sous_barriere, vega
from fx.smile import construire_smile, construire_surface, densite_implicite, vols_depuis_cotations

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


# --- Réplication -----------------------------------------------------------------------


@pytest.mark.parametrize("t, dfd, dff", [(0.5, 0.98, 0.987), (1.0, 0.9587, 0.9721), (7.0, 0.7369, 0.8093)])
def test_smile_plat_replication_egale_formule_fermee(t, dfd, dff):
    p = proba_replication(1.1165, 1.1770, t, dfd, dff, lambda k: np.full_like(k, 0.0676, dtype=float))
    assert p == pytest.approx(float(probabilite_eur_sous_barriere(1.1165, 1.1770, t, 0.0676, dfd, dff)), abs=1e-9)


def test_replication_insensible_au_pas(surface, contexte, courbes):
    sm = surface.smile(1.0)
    args = (courbes.spot, contexte.barriere.niveau, 1.0, sm.df_dom, sm.df_for, sm.vol)
    ps = [proba_replication(*args, pas_relatif=h) for h in (1e-3, 1e-4, 1e-5)]
    assert max(ps) - min(ps) < 1e-7


def test_skew_negatif_k_au_dessus_du_forward_replication_inferieure():
    """Cas du CLAUDE.md, avec K entre le forward et le strike 25Δ call (pente du smile < 0)."""
    s, t, dfd, dff = 1.1165, 1.0, 0.9587, 0.9721
    sm = construire_smile(t, s, dfd, dff, vols_depuis_cotations(6.76, -0.84, 0.10), 1.0)
    k = 0.5 * (sm.forward + sm.strikes[2])
    assert sm.forward < k < sm.strikes[2]
    p_rep = proba_replication(s, k, t, dfd, dff, sm.vol)
    p_inj = float(probabilite_eur_sous_barriere(s, k, t, float(sm.vol(k)), dfd, dff))
    assert p_rep < p_inj


def test_ecart_replication_injection_egal_terme_de_pente(resultats, surface, courbes, contexte):
    """Identité : P_smile − P_σ(K) = K · vega(σ(K)) · σ'(K) / (S · DF_EUR), aux 14 dates."""
    k = contexte.barriere.niveau
    for r in resultats:
        sm = surface.smile(r.t)
        h = k * 1e-4
        pente = float(4 * (sm.vol(k + h / 2) - sm.vol(k - h / 2)) / h - (sm.vol(k + h) - sm.vol(k - h)) / (2 * h)) / 3
        terme = k * float(vega(courbes.spot, k, r.t, r.vol_k, sm.df_dom, sm.df_for)) * pente / (courbes.spot * sm.df_for)
        assert r.p_smile - r.p_vol_k == pytest.approx(terme, abs=1e-8), r.numero


def test_replication_egale_integrale_de_la_densite(surface, courbes, contexte):
    """Contrôle indépendant : P_EUR(X < K) = DF_USD / (S · DF_EUR) · ∫ x q(x) dx sur ]0, K]."""
    k = contexte.barriere.niveau
    for t in (0.5, 1.0):
        sm = surface.smile(t)
        # Borne basse 0,5 K : l'aile put du smile (~9 % de vol) laisse ~3e-4 de masse sous 0,7 K.
        xs = np.linspace(0.50 * k, k, 30001)
        integrale = np.trapezoid(xs * densite_implicite(sm, xs, pas_relatif=1e-4), xs)
        p_dens = sm.df_dom / (courbes.spot * sm.df_for) * integrale
        assert proba_replication(courbes.spot, k, t, sm.df_dom, sm.df_for, sm.vol) == pytest.approx(p_dens, abs=1e-6)


# --- Résultats sur les données réelles -------------------------------------------------


def test_quatorze_coupons(resultats, contexte):
    assert [r.numero for r in resultats] == list(range(1, 15))
    assert all(0 < r.p_smile < 1 and 0 < r.p_plate < 1 for r in resultats)
    assert resultats[0].montant_coupon_eur == pytest.approx(50_000_000 * 0.07)
    assert [r.vol_extrapolee for r in resultats] == [False, False] + [True] * 12


def test_valeur_actualisee(resultats):
    r = resultats[1]
    assert r.valeur_smile == pytest.approx(3_500_000 * r.df_eur * r.p_smile, rel=1e-14)


def test_version_plate_et_injectee_coherentes(resultats, courbes, contexte):
    k = contexte.barriere.niveau
    for r in resultats:
        dfd, dff = courbes.usd.df(r.t), courbes.eur.df(r.t)
        assert r.p_plate == pytest.approx(float(probabilite_eur_sous_barriere(courbes.spot, k, r.t, r.vol_atm, dfd, dff)), abs=1e-15)
        assert r.p_vol_k == pytest.approx(float(probabilite_eur_sous_barriere(courbes.spot, k, r.t, r.vol_k, dfd, dff)), abs=1e-15)


def test_probabilites_decroissantes_en_t(resultats):
    """Spot sous K et forward croissant : la probabilité baisse d'une date à l'autre."""
    for attr in ("p_plate", "p_smile"):
        ps = [getattr(r, attr) for r in resultats]
        assert all(b < a for a, b in zip(ps, ps[1:]))


# --- Sorties ---------------------------------------------------------------------------


def test_csv_et_graphique(resultats, contexte, surface, tmp_path):
    chemin = tmp_path / "coupons_fx.csv"
    ecrire_csv(resultats, chemin)
    with chemin.open(encoding="utf-8-sig") as f:
        lignes = list(csv.reader(f, delimiter=";"))
    assert len(lignes) == 1 + 14 + 1 and lignes[0][0] == "numero" and lignes[-1][0] == "total"
    assert float(lignes[-1][-1]) == pytest.approx(sum(r.valeur_smile for r in resultats), abs=0.01)
    svg = tmp_path / "p.svg"
    tracer_probabilites(resultats, contexte.barriere.niveau, svg, surface.t_dernier_pilier)
    assert svg.read_text(encoding="utf-8").lstrip().startswith("<?xml")
