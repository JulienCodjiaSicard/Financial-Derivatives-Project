"""
donnees.py : TOUTES les données et paramètres du pricer (rien d'autre).

  - paramètres du produit (énoncé)
  - données de marché lues dans les livrables des sous-groupes (Taux, Change, Crédit), en lecture seule :
      Taux   -> courbes €STR / SOFR et paramètres Hull-White calibrés sur les swaptions co-terminales 7Y
      Change -> fixings BCE EUR/USD (spot et barrière), vol au strike, corrélation taux/change
      Crédit -> survie du panier de banques EUR aux dates de coupon
      BCE    -> taux souverain AAA zone euro 7 ans (contrainte C > souverain)
Date de valorisation commune : 13/11/2026. Temps en années ACT/365F depuis cette date.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_TAUX = RACINE / "Taux EUR courbe de taux calibration de Hull-White sur les swaptions" / "output" / "diffusion"
FICHIER_COURBES = DOSSIER_TAUX / "courbes_20261113.csv"
FICHIER_HW = DOSSIER_TAUX / "hw_params.json"
FICHIER_FX_HISTO = RACINE / "Change" / "data" / "eurusd_bce_2026-05-06_2026-11-13.csv"
FICHIER_FX_EXPORT = RACINE / "Change" / "resultats_fx" / "export_mc.json"
FICHIER_CREDIT = RACINE / "Credit" / "outputs" / "systemic" / "coupon_semesters.csv"
FICHIER_SOUVERAIN = RACINE / "Credit" / "data" / "proxies" / "ecb_aaa.csv"
SCENARIO_CREDIT = "tail_1x"         # prolongement central des intensités au-delà du dernier CDS (groupe Crédit)


# =====================================================================================
# 1. PARAMETRES
# =====================================================================================
DATE_VALO = "2026-11-13"            # date de valorisation (t = 0), commune aux sous-groupes

# Le produit (énoncé)
N = 50e6                            # nominal : 50 M EUR
T = 7.0                             # maturité : 7 ans
DELTA = 0.5                         # un coupon tous les 6 mois (le premier tombe à 6 mois)
DATES_COUPON = DELTA * np.arange(1, 15)        # 0,5 ; 1 ; ... ; 7  (14 coupons, indices i = 0..13)
CALLS = list(range(3, 13))          # indices des dates de call : t = 2 ; 2,5 ; ... ; 6,5
PAS_PAR_AN = 12                     # grille mensuelle
NB_SCENARIOS = 100_000              # par jeu de simulation
MARGE = 0.015                       # marge de la banque m


# =====================================================================================
# 2. PARAMETRES DES MODELES (lus dans les livrables des sous-groupes)
# =====================================================================================
def _lire(fichier):
    if not fichier.exists():
        raise FileNotFoundError(f"Livrable introuvable (faire `git pull`) : {fichier}")
    return fichier


# Taux : Hull-White 1 facteur
_hw = json.loads(_lire(FICHIER_HW).read_text())
A = float(_hw["a"])                 # vitesse de retour à la moyenne (2,68 %)
SIGMA_R = float(_hw["sigma_r"])     # volatilité du taux court (93 bp)

# Change : le coupon est un digital, très sensible au smile (sujet, section 4.3). Le groupe Change livre pour
# chaque date de coupon la vol ATM de marché et une barrière effective qui, à cette vol, redonne la probabilité
# avec smile (Vanna-Volga). La barrière contractuelle x (plus haut 6 mois) sert aux graphiques et aux contrôles.
_export_fx = json.loads(_lire(FICHIER_FX_EXPORT).read_text())
_bloc_fx = _export_fx["pour_pricer_note_ece"]
FX_TENORS = np.array(_bloc_fx["fx_vol_tenors"], dtype=float)         # maturités des vols (années)
FX_VOLS_ATM = np.array(_bloc_fx["fx_atm_vols"], dtype=float)         # vols ATM aux 14 dates de coupon
BARRIERES_SMILE = np.array(_bloc_fx["fx_barrier"], dtype=float)      # barrière effective par date de coupon
P_FX_GROUPE_CHANGE = np.array(_export_fx["controle_moteur"]["p_smile_attendues"], dtype=float)   # pour contrôle

# Corrélation : le groupe Change livre rho = corr(Z_r, Z_X) avec X = 1/FX = EUR pour 1 USD.
# Longstaff.py prend rho_X = -RHO_FX_TAUX (corrélation avec FX = USD pour 1 EUR), d'où le signe moins.
RHO_FX_TAUX = -float(_bloc_fx["fx_rate_corr"])


def variance_fx(t, vols=FX_VOLS_ATM):
    """Variance totale w(t) = sigma^2(t) t du change, interpolée linéairement entre les piliers ATM
    (vol constante au-delà du dernier). Les vols locales par pas sont les accroissements de w."""
    t = np.asarray(t, dtype=float)
    w = np.interp(t, np.r_[0.0, FX_TENORS], np.r_[0.0, vols ** 2 * FX_TENORS])
    return np.where(t > FX_TENORS[-1], vols[-1] ** 2 * t, w)

# Crédit : survie du premier défaut du panier (UniCredit, Société Générale, Commerzbank), indépendance
_credit = pd.read_csv(_lire(FICHIER_CREDIT))
_credit = _credit[_credit["scenario"] == SCENARIO_CREDIT].sort_values("time_years")
_t_credit = np.r_[0.0, _credit["time_years"].to_numpy()]
_log_survie = np.r_[0.0, np.log(_credit["independent_survival"].to_numpy())]


def _survie_credit(t):
    """S(t) interpolée log-linéairement (intensité constante par morceaux entre les dates du groupe Crédit)."""
    t = np.asarray(t, dtype=float)
    pente = (_log_survie[-1] - _log_survie[-2]) / (_t_credit[-1] - _t_credit[-2])
    log_s = np.where(t > _t_credit[-1], _log_survie[-1] + pente * (t - _t_credit[-1]),
                     np.interp(t, _t_credit, _log_survie))
    return np.exp(log_s)


SURVIE_CREDIT = _survie_credit
SURVIE_GROUPE_CREDIT = _credit["independent_survival"].to_numpy()     # S aux 14 dates, pour contrôle


# =====================================================================================
# 3. DONNEES DE MARCHE
# =====================================================================================
def recuperer_donnees():
    """Rend (cours_fx, courbe_usd, courbe_eur, taux_souverain, sources).
    Une courbe = (maturités en années, taux zéro continus)."""
    # EUR/USD : fixings BCE des 6 derniers mois. Le dernier = spot du 13/11/2026, le max = barrière x.
    histo = pd.read_csv(_lire(FICHIER_FX_HISTO))
    cours_fx = histo["eurusd"].astype(float).to_numpy()

    # Courbes €STR et SOFR du groupe Taux (grille mensuelle, colonnes lisses P = exp(-intégrale de f))
    courbes = pd.read_csv(_lire(FICHIER_COURBES))
    courbes = courbes[courbes["t"] > 0]
    t = courbes["t"].to_numpy()
    courbe_eur = (list(t), list(-np.log(courbes["P_estr"].to_numpy()) / t))
    courbe_usd = (list(t), list(-np.log(courbes["P_sofr"].to_numpy()) / t))

    # Souverain AAA zone euro 7 ans (BCE), dernier fixing à la date de valorisation
    aaa = pd.read_csv(_lire(FICHIER_SOUVERAIN))
    aaa = aaa[aaa["series_key"].str.endswith("SR_7Y") & (aaa["valuation_date"] <= DATE_VALO)]
    ligne = aaa.sort_values("valuation_date").iloc[-1]
    souverain = float(ligne["observed_rate_percent"]) / 100

    sources = {
        "EUR/USD": f"Change : fixings BCE {histo['date'].iloc[0]} au {histo['date'].iloc[-1]}",
        "courbe EUR": "Taux : OIS €STR (courbes_20261113.csv)",
        "courbe USD": "Taux : OIS SOFR (courbes_20261113.csv)",
        "taux souverain 7 ans": f"BCE souverains AAA zone euro ({ligne['valuation_date']})",
        "a, sigma_r": f"Taux : Hull-White sur swaptions co-terminales 7Y (a = {A:.2%}, sigma_r = {SIGMA_R * 1e4:.0f} bp)",
        "vols FX, barrières, rho": f"Change : vols ATM {FX_VOLS_ATM[0]:.2%} à {FX_VOLS_ATM[-1]:.2%}, barrières effectives (smile) "
                                   f"{BARRIERES_SMILE[0]:.4f} à {BARRIERES_SMILE[-1]:.4f}, rho(taux, X) = {-RHO_FX_TAUX:.2f}",
        "survie": f"Crédit : panier de banques EUR, scénario {SCENARIO_CREDIT}",
    }
    return cours_fx, courbe_usd, courbe_eur, souverain, sources


def survie(t):
    """S(t) = probabilité qu'aucune banque EUR n'ait fait défaut avant t."""
    return SURVIE_CREDIT(t)
