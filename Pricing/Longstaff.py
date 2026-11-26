"""
Longstaff.py : Monte-Carlo + Longstaff-Schwartz pour la note callable (Partie 2 sections 3 et 5, Partie 3)

Lancer :  python Longstaff.py
Le programme : 1) charge les livrables des sous-groupes  2) lance les contrôles  3) price la note et trouve le
coupon C  4) calcule les sensibilités  5) enregistre résultats et graphiques dans sorties/

Toutes les données et paramètres sont dans donnees.py (lus dans les dossiers Taux, Change et Crédit).
"""
import json
import math
import os

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from donnees import (DATE_VALO, N, T, DELTA, DATES_COUPON, CALLS, PAS_PAR_AN, NB_SCENARIOS,
                     A, SIGMA_R, RHO_FX_TAUX, MARGE, FX_VOLS_ATM, BARRIERES_SMILE,
                     P_FX_GROUPE_CHANGE, SURVIE_GROUPE_CREDIT, recuperer_donnees, survie, variance_fx)

DOSSIER_SORTIES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sorties")
GRAINE_APPRENTISSAGE, GRAINE_PRICING = 101, 202


# =====================================================================================
# 1. PETITS OUTILS
# =====================================================================================
def cdf_normale(x):
    """Fonction de répartition de la loi normale N(0,1)."""
    return np.array([0.5 * (1 + math.erf(v / math.sqrt(2))) for v in np.atleast_1d(x)])


def dichotomie(f, a, b, precision=1e-9):
    """Trouve x dans [a, b] tel que f(x) = 0 (f change de signe entre a et b)."""
    fa = f(a)
    while b - a > precision:
        milieu = (a + b) / 2
        fm = f(milieu)
        if fa * fm <= 0:
            b = milieu
        else:
            a, fa = milieu, fm
    return (a + b) / 2


def zero_coupon(courbe, t):
    """P(0,t) pour une courbe (maturités, taux zéro). On interpole z*t linéairement
    (= interpolation log-linéaire des facteurs d'actualisation), au-delà du dernier point on prolonge."""
    temps = np.array([0.0] + list(courbe[0]))
    z_fois_t = np.array([0.0] + list(np.array(courbe[1]) * np.array(courbe[0])))
    t = np.asarray(t, dtype=float)
    pente = (z_fois_t[-1] - z_fois_t[-2]) / (temps[-1] - temps[-2])
    zt = np.where(t > temps[-1], z_fois_t[-1] + pente * (t - temps[-1]), np.interp(t, temps, z_fois_t))
    return np.exp(-zt)


def choquer_courbe(courbe, choc):
    """Décalage parallèle des taux zéro continus."""
    return (courbe[0], [z + choc for z in courbe[1]])


def moyenne_et_erreur(x):
    """Moyenne et erreur standard d'un estimateur Monte-Carlo avec variables antithétiques :
    on moyenne d'abord chaque scénario avec son antithétique (scénario m et m + M/2)."""
    moitie = len(x) // 2
    paires = (x[:moitie] + x[moitie:]) / 2
    return paires.mean(), paires.std(ddof=1) / math.sqrt(moitie)


# =====================================================================================
# 2. SIMULATION (sous la probabilité risque-neutre EUR)
#    Taux EUR (Hull-White) : r(t) = f(0,t) + phi(t) + y(t),   dy = -a y dt + sigma_r dW_r
#    Change X = 1/FX (EUR pour 1 USD) : dX/X = (r_EUR - r_USD) dt + sigma_FX(t) dW_X, corr(dW_X, dW_r) = rho
#    sigma_FX(t) : vol locale déduite de la structure par terme des vols ATM (variance totale w(t)).
# =====================================================================================
def phi(t, a, sigma_r):
    return sigma_r ** 2 / (2 * a ** 2) * (1 - np.exp(-a * t)) ** 2


def integrale_phi(debut, fin, a, sigma_r):
    return sigma_r ** 2 / (2 * a ** 2) * ((fin - debut) - 2 * (np.exp(-a * debut) - np.exp(-a * fin)) / a
                                          + (np.exp(-2 * a * debut) - np.exp(-2 * a * fin)) / (2 * a))


def dates_de(mod):
    """Dates des coupons restants, en années depuis la date de valorisation (0,5 ; 1 ; ... ; 7 par défaut).
    Le backtest les décale quand la valorisation se fait entre deux dates de coupon."""
    return np.asarray(mod.get("dates", DATES_COUPON), dtype=float)


def simuler(mod, nb, graine):
    """Simule nb scénarios sur la grille mensuelle (complétée par les dates de coupon si elles n'y tombent pas).
    Rend les taux r, le change X et le facteur d'actualisation D."""
    a, sigma_r = mod["a"], mod["sigma_r"]
    dates = dates_de(mod)
    t = np.unique(np.round(np.r_[np.arange(0, dates[-1], 1 / PAS_PAR_AN), dates], 10))
    dt = np.diff(t)
    nb_pas = len(dt)

    # tirages normaux, avec variables antithétiques (la 2e moitié est l'opposé de la 1re)
    rng = np.random.default_rng(graine)
    Z = rng.standard_normal((nb // 2, nb_pas, 2))
    Z = np.concatenate([Z, -Z])
    Z_taux = Z[:, :, 0]
    rho = mod["rho_X"]
    Z_change = rho * Z_taux + math.sqrt(1 - rho ** 2) * Z[:, :, 1]       # corrélation par Cholesky

    # parties déterministes de l'intégrale des taux sur chaque pas (calculées exactement avec les courbes)
    Pe = zero_coupon(mod["eur"], t)
    Pu = zero_coupon(mod["usd"], t)
    int_f = np.log(Pe[:-1] / Pe[1:])                  # intégrale de f(0,s) entre deux dates
    int_r_usd = np.log(Pu[:-1] / Pu[1:])              # intégrale du taux USD (courbe déterministe)
    var_change = np.diff(variance_fx(t, mod["vols_fx"]))       # variance du log-change sur chaque pas
    decroissance = np.exp(-a * dt)
    ecart_type_y = sigma_r * np.sqrt((1 - np.exp(-2 * a * dt)) / (2 * a))

    y = np.zeros((nb, nb_pas + 1))
    ln_D = np.zeros((nb, nb_pas + 1))
    ln_X = np.full((nb, nb_pas + 1), math.log(mod["X0"]))
    for j in range(nb_pas):
        y[:, j + 1] = y[:, j] * decroissance[j] + ecart_type_y[j] * Z_taux[:, j]      # Ornstein-Uhlenbeck exact
        int_r = int_f[j] + integrale_phi(t[j], t[j + 1], a, sigma_r) + dt[j] * (y[:, j] + y[:, j + 1]) / 2
        ln_D[:, j + 1] = ln_D[:, j] - int_r
        ln_X[:, j + 1] = (ln_X[:, j] + int_r - int_r_usd[j] - 0.5 * var_change[j]
                          + math.sqrt(var_change[j]) * Z_change[:, j])

    r = np.append(int_f / dt, int_f[-1] / dt[-1]) + phi(t, a, sigma_r) + y     # taux court r(t) = f + phi + y
    indices_coupons = np.searchsorted(t, np.round(dates, 10))
    return {"t": t, "r": r, "X": np.exp(ln_X), "D": np.exp(ln_D), "idx": indices_coupons}


# =====================================================================================
# 3. LONGSTAFF-SCHWARTZ SUR LA NOTE
#    La banque rembourse N à une date de call si la valeur de continuation (ce qu'elle devrait encore payer)
#    est supérieure à N. On estime cette valeur de continuation par régression sur l'état (r, ln X).
# =====================================================================================
def coupon_conditions(sim, mod):
    """1{X(t_i) > 1/K_i} * S(t_i) : le coupon est payé si FX < K_i (donc X > 1/K_i) et si les banques sont en vie.
    K_i = barrière effective de la date i (smile). On remplace l'indicatrice de survie par sa probabilité S(t_i) :
    même prix (défaut indépendant des taux et du change), moins de bruit."""
    X_aux_coupons = sim["X"][:, sim["idx"]]
    return (X_aux_coupons > 1 / mod["barrieres"]) * mod["survie"]


def fonctions_de_base(r, X, moyennes=None, ecarts=None):
    """Régresseurs de la valeur de continuation, sur l'état (r, ln X) centré-réduit :
    - tous les monômes r^i (ln X)^j de degré total <= 3 (10 fonctions) ;
    - tanh(k ln X) et r tanh(k ln X), k = 1, 2, 4 (6 fonctions) : les coupons sont des digitales sur le change,
      la continuation a donc une forme en marche que les polynômes seuls approchent mal.
    Base choisie par convergence : au-delà (degré 4, 5), la valeur de l'option ne bouge plus (écart < 1 erreur standard).
    Les moyennes et écarts du jeu d'apprentissage sont réutilisés sur le jeu de pricing."""
    etat = np.column_stack([r, np.log(X)])
    if moyennes is None:
        moyennes, ecarts = etat.mean(axis=0), etat.std(axis=0)
    z = (etat - moyennes) / ecarts
    rr, xx = z[:, 0], z[:, 1]
    colonnes = [rr ** i * xx ** j for i in range(4) for j in range(4 - i)]
    colonnes += [np.tanh(k * xx) for k in (1, 2, 4)] + [rr * np.tanh(k * xx) for k in (1, 2, 4)]
    return np.column_stack(colonnes), moyennes, ecarts


def recurrence_retrograde(sim, indic, C, regles=None, trace=None):
    """On remonte le temps de 7 ans à 0,5 an.
    'valeur' = valeur en 0 (actualisée) de tous les flux postérieurs à la date courante, selon la stratégie de la banque.
    - regles=None : on APPREND les régressions (jeu d'apprentissage)
    - sinon on APPLIQUE les régressions déjà apprises (jeu de pricing, indépendant du premier)."""
    D = sim["D"][:, sim["idx"]]                    # D(0, t_i) pour les 14 dates de coupon
    coupons = N * C * DELTA * indic                # coupons (non actualisés), forme (scénarios, 14)
    valeur = D[:, 13] * (coupons[:, 13] + N)       # à 7 ans : dernier coupon + capital
    date_remboursement = np.full(len(valeur), 13)
    apprentissage = regles is None
    if apprentissage:
        regles = {}

    for i in range(12, -1, -1):
        if i in CALLS:
            continuation = valeur / D[:, i]        # ce que la banque paierait encore (en euros de t_i) si elle continue
            r_i, X_i = sim["r"][:, sim["idx"][i]], sim["X"][:, sim["idx"][i]]
            if apprentissage:
                base, moy, ecart = fonctions_de_base(r_i, X_i)
                beta = np.linalg.lstsq(base, continuation, rcond=None)[0]    # moindres carrés
                regles[i] = (beta, moy, ecart)
            beta, moy, ecart = regles[i]
            base, _, _ = fonctions_de_base(r_i, X_i, moy, ecart)
            continuation_estimee = base @ beta
            if trace is not None:
                trace[i] = (continuation, continuation_estimee)
            rembourse = continuation_estimee > N   # la banque rembourse si continuer lui coûte plus que le pair
            valeur = np.where(rembourse, D[:, i] * N, valeur)     # les flux futurs sont remplacés par N en t_i
            date_remboursement = np.where(rembourse, i, date_remboursement)
        valeur = valeur + D[:, i] * coupons[:, i]  # coupon de t_i (payé même si la banque rembourse à cette date)
    return valeur, date_remboursement, regles


def valeur_sans_call(sim, indic, C):
    """Même flux, mais la banque ne rembourse jamais avant 7 ans."""
    D = sim["D"][:, sim["idx"]]
    return (D * (N * C * DELTA * indic)).sum(axis=1) + D[:, 13] * N


def prix_note(mod, sim_A, sim_B, C, indic_A=None, indic_B=None):
    """Les régressions sont apprises sur sim_A, le prix est calculé sur sim_B (scénarios indépendants).
    Si on utilisait les mêmes scénarios pour les deux, le prix serait biaisé."""
    indic_A = coupon_conditions(sim_A, mod) if indic_A is None else indic_A
    indic_B = coupon_conditions(sim_B, mod) if indic_B is None else indic_B
    regles = recurrence_retrograde(sim_A, indic_A, C)[2]
    valeur, date_rembt, _ = recurrence_retrograde(sim_B, indic_B, C, regles)
    sans_call = valeur_sans_call(sim_B, indic_B, C)
    V0, erreur_V0 = moyenne_et_erreur(valeur)
    V0_sans_call, erreur_sans_call = moyenne_et_erreur(sans_call)
    call, erreur_call = moyenne_et_erreur(sans_call - valeur)      # Call0 = V0 sans call - V0
    return {"V0": V0, "erreur": erreur_V0, "V0_sans_call": V0_sans_call, "erreur_sans_call": erreur_sans_call,
            "call": call, "erreur_call": erreur_call, "date_rembt": date_rembt, "regles": regles}


def trouver_coupon(mod, sim_A, sim_B):
    """On cherche C tel que V0(C) = N (1 - m), par dichotomie, avec les mêmes aléas à chaque essai."""
    indic_A, indic_B = coupon_conditions(sim_A, mod), coupon_conditions(sim_B, mod)

    def ecart(C):
        return prix_note(mod, sim_A, sim_B, C, indic_A, indic_B)["V0"] - N * (1 - MARGE)

    return dichotomie(ecart, 0.0, 0.30)


def probabilites_fx_fermees(mod, t=None):
    """Q^{t_i}(FX(t_i) < K_i) = Phi(d_i), d_i = (ln(K_i/F_i) - w_i/2) / sqrt(w_i)  (taux déterministes)."""
    t = dates_de(mod) if t is None else t
    forward = mod["FX0"] * zero_coupon(mod["eur"], t) / zero_coupon(mod["usd"], t)
    variance = variance_fx(t, mod["vols_fx"])
    return cdf_normale((np.log(mod["barrieres"] / forward) - 0.5 * variance) / np.sqrt(variance))


def prix_formule_fermee_sans_call(mod, C):
    """Formule de la Partie 2 : N P(0,T) + somme N C delta P(0,t_i) S(t_i) Phi(d_i). Sert à tester le Monte-Carlo."""
    dates = dates_de(mod)
    P_eur = zero_coupon(mod["eur"], dates)
    return (N * zero_coupon(mod["eur"], dates[-1])
            + N * C * DELTA * np.sum(P_eur * mod["survie"] * probabilites_fx_fermees(mod)))


# =====================================================================================
# 4. SENSIBILITES (Partie 3)
#    Chocs centrés, mêmes aléas (graine du jeu de pricing) et règle d'exercice gelée (celle apprise au point de
#    base) : la variation de prix ne mesure que l'effet du facteur, pas du bruit de régression. Au premier ordre,
#    geler la règle optimale ne change pas la sensibilité (théorème de l'enveloppe).
# =====================================================================================
CHOCS = [
    # (facteur, libellé du choc, fonction qui rend le modèle choqué pour un choc de signe s = +1 ou -1)
    ("Taux EUR (€STR)", "+1 bp parallèle", lambda m, s: dict(m, eur=choquer_courbe(m["eur"], s * 1e-4))),
    ("Taux USD (SOFR)", "+1 bp parallèle", lambda m, s: dict(m, usd=choquer_courbe(m["usd"], s * 1e-4))),
    ("Vol des taux sigma_r", "+10 bp", lambda m, s: dict(m, sigma_r=m["sigma_r"] + s * 1e-3)),
    ("Retour à la moyenne a", "+1 %", lambda m, s: dict(m, a=m["a"] + s * 1e-2)),
    ("Spot EUR/USD", "+1 %", lambda m, s: dict(m, FX0=m["FX0"] * (1 + s * 0.01), X0=m["X0"] / (1 + s * 0.01))),
    ("Vol EUR/USD", "+1 point", lambda m, s: dict(m, vols_fx=m["vols_fx"] + s * 0.01)),
    ("Corrélation taux / change", "+0.10", lambda m, s: dict(m, rho_X=m["rho_X"] + s * 0.10)),
    ("Intensité de défaut des banques EUR", "+10 bp",
     lambda m, s: dict(m, survie=m["survie"] * np.exp(-s * 1e-3 * dates_de(m)))),
    ("Barrière EUR/USD", "+0.01", lambda m, s: dict(m, barrieres=m["barrieres"] + s * 0.01)),
]


def sensibilites(mod, C, regles):
    lignes = []

    def prix(m):
        sim = simuler(m, NB_SCENARIOS, GRAINE_PRICING)
        indic = coupon_conditions(sim, m)
        return (recurrence_retrograde(sim, indic, C, regles)[0].mean(),
                valeur_sans_call(sim, indic, C).mean())

    for facteur, choc, choquer in CHOCS:
        haut, haut_sc = prix(choquer(mod, +1))
        bas, bas_sc = prix(choquer(mod, -1))
        lignes.append({"facteur": facteur, "choc": choc,
                       "variation_note_EUR": (haut - bas) / 2,
                       "variation_note_bp_nominal": (haut - bas) / 2 / N * 1e4,
                       "variation_sans_call_EUR": (haut_sc - bas_sc) / 2,
                       "convexite_EUR": haut + bas - 2 * prix(mod)[0]})
    return pd.DataFrame(lignes)


# =====================================================================================
# 5. CONTROLES
# =====================================================================================
def put_americain_8_trajectoires():
    """Exemple du PDF (Longstaff-Schwartz 2001) : put américain K = 1,10, r = 6 %, 8 trajectoires.
    Résultat attendu : 0,1144 (et 0,0564 pour le put européen)."""
    S = np.array([[1, 1.09, 1.08, 1.34], [1, 1.16, 1.26, 1.54], [1, 1.22, 1.07, 1.03], [1, 0.93, 0.97, 0.92],
                  [1, 1.11, 1.56, 1.52], [1, 0.76, 0.77, 0.90], [1, 0.92, 0.84, 1.01], [1, 0.88, 1.22, 1.34]])
    K, facteur = 1.10, math.exp(-0.06)
    gain = np.maximum(K - S[:, 3], 0)               # à l'échéance (t = 3)
    date = np.full(8, 3)
    for k in (2, 1):                                # on remonte : t = 2 puis t = 1
        exercice = np.maximum(K - S[:, k], 0)
        dans_la_monnaie = exercice > 0              # on ne régresse que sur les trajectoires dans la monnaie
        Y = gain[dans_la_monnaie] * facteur ** (date[dans_la_monnaie] - k)
        coefficients = np.polyfit(S[dans_la_monnaie, k], Y, 2)          # parabole
        continuation = np.polyval(coefficients, S[dans_la_monnaie, k])
        a_exercer = np.where(dans_la_monnaie)[0][exercice[dans_la_monnaie] > continuation]
        gain[a_exercer] = exercice[a_exercer]       # on exerce : on remplace le flux plus tardif
        date[a_exercer] = k
    europeen = np.mean(np.maximum(K - S[:, 3], 0)) * facteur ** 3
    return np.mean(gain * facteur ** date), europeen


def lancer_controles(mod):
    tout_ok = True

    def verifier(nom, condition, detail):
        nonlocal tout_ok
        tout_ok = tout_ok and bool(condition)
        print(f"  [{'OK' if condition else 'ECHEC'}] {nom} {detail}")

    americain, europeen = put_americain_8_trajectoires()
    verifier("Longstaff-Schwartz, 8 trajectoires du PDF (0,1144 et 0,0564)",
             abs(americain - 0.1144) < 5e-4 and abs(europeen - 0.0564) < 5e-4,
             f"-> {americain:.4f} et {europeen:.4f}")

    sim = simuler(mod, 40_000, graine=11)
    t = sim["t"]
    ecart_D = np.max(np.abs(sim["D"].mean(axis=0) / zero_coupon(mod["eur"], t) - 1))
    verifier("E[D(0,t)] = P(0,t) (le modèle reproduit la courbe €STR)", ecart_D < 2e-3, f"-> écart max {ecart_D:.1e}")
    ecart_X = np.max(np.abs((sim["D"] * sim["X"]).mean(axis=0) / (mod["X0"] * zero_coupon(mod["usd"], t)) - 1))
    verifier("E[D X] = X0 P_USD(0,t) (le change actualisé est martingale)", ecart_X < 3e-3, f"-> écart max {ecart_X:.1e}")

    # Avec sigma_r = 0 et rho = 0, le Monte-Carlo sans call doit retrouver la formule fermée
    mod_det = dict(mod, sigma_r=1e-12, rho_X=0.0)
    sim0 = simuler(mod_det, 100_000, graine=12)
    prix_mc, erreur = moyenne_et_erreur(valeur_sans_call(sim0, coupon_conditions(sim0, mod_det), 0.05))
    prix_exact = prix_formule_fermee_sans_call(mod_det, 0.05)
    verifier("Monte-Carlo sans call = formule fermée (3 erreurs standard)", abs(prix_mc - prix_exact) < 3 * erreur,
             f"-> MC {prix_mc / 1e6:.4f} +/- {erreur / 1e6:.4f} ; formule {prix_exact / 1e6:.4f} M EUR")

    # Raccordement aux livrables des autres sous-groupes
    p_mc = (sim0["X"][:, sim0["idx"]] > 1 / mod["barrieres"]).mean(axis=0)
    ecart_fx = np.max(np.abs(p_mc - P_FX_GROUPE_CHANGE))
    verifier("Probabilités FX = celles du groupe Change avec smile (tolérance 1 point)", ecart_fx < 0.01,
             f"-> écart max {ecart_fx * 100:.2f} point")
    ecart_s = np.max(np.abs(mod["survie"] - SURVIE_GROUPE_CREDIT))
    verifier("Survie aux dates de coupon = celle du groupe Crédit", ecart_s < 1e-12, f"-> écart max {ecart_s:.0e}")
    return tout_ok


# =====================================================================================
# 6. PROGRAMME PRINCIPAL
# =====================================================================================
def main():
    os.makedirs(DOSSIER_SORTIES, exist_ok=True)

    cours_fx, courbe_usd, courbe_eur, souverain, sources = recuperer_donnees()
    print(f"Date de valorisation : {DATE_VALO}\n\nOrigine des données :")
    for nom, source in sources.items():
        print(f"  - {nom:24s}: {source}")

    mod = {"eur": courbe_eur, "usd": courbe_usd, "a": A, "sigma_r": SIGMA_R,
           "FX0": cours_fx[-1], "X0": 1 / cours_fx[-1],
           "x": cours_fx.max(),                    # barrière contractuelle = plus haut des 6 derniers mois
           "barrieres": BARRIERES_SMILE,           # barrière effective par date (smile, groupe Change)
           "vols_fx": FX_VOLS_ATM,
           "rho_X": -RHO_FX_TAUX,                  # X = 1/FX : la corrélation change de signe
           "survie": survie(DATES_COUPON)}
    print(f"\nEUR/USD spot = {mod['FX0']:.4f} ; barrière x = {mod['x']:.4f} ; taux souverain 7 ans = {souverain:.3%}")

    print("\nContrôles :")
    if not lancer_controles(mod):
        print("Un contrôle a échoué, on s'arrête.")
        return

    sim_A = simuler(mod, NB_SCENARIOS, GRAINE_APPRENTISSAGE)      # jeu 1 : apprentissage des régressions
    sim_B = simuler(mod, NB_SCENARIOS, GRAINE_PRICING)            # jeu 2 : calcul du prix

    # coupon sans option : la formule fermée est linéaire en C, on l'inverse directement
    capital_actualise = N * zero_coupon(mod["eur"], T)
    C_sans_call = (N * (1 - MARGE) - capital_actualise) / (prix_formule_fermee_sans_call(mod, 1.0) - capital_actualise)
    C = trouver_coupon(mod, sim_A, sim_B)
    res = prix_note(mod, sim_A, sim_B, C)

    # probabilités par date de coupon
    p_fx = (sim_B["X"][:, sim_B["idx"]] > 1 / mod["barrieres"]).mean(axis=0)
    p_vivant = np.array([np.mean(res["date_rembt"] >= i) for i in range(14)])      # note encore en vie en t_i
    p_rembt = np.array([np.mean(res["date_rembt"] == i) if i in CALLS else 0.0 for i in range(14)])
    table = pd.DataFrame({
        "t_annees": DATES_COUPON, "P_eur": zero_coupon(mod["eur"], DATES_COUPON),
        "barriere_effective": mod["barrieres"], "vol_atm": mod["vols_fx"],
        "proba_fx_mc": p_fx, "proba_fx_groupe_change": P_FX_GROUPE_CHANGE, "survie_banques": mod["survie"],
        "proba_coupon_paye": p_fx * mod["survie"], "proba_note_en_vie": p_vivant, "proba_remboursement": p_rembt,
        "coupon_si_paye_EUR": N * C * DELTA})

    print("\nRésultats")
    print(f"  Coupon sans l'option de la banque (formule fermée) : {C_sans_call:.3%}")
    print(f"  Coupon avec l'option de la banque (MC + LS)        : {C:.3%}")
    print(f"  V0 = {res['V0'] / 1e6:.3f} +/- {res['erreur'] / 1e6:.3f} M EUR   (cible N(1-m) = {N * (1 - MARGE) / 1e6:.3f})")
    print(f"  Décomposition : capital {capital_actualise / 1e6:.3f} + coupons "
          f"{(res['V0_sans_call'] - capital_actualise) / 1e6:.3f} - option de la banque {res['call'] / 1e6:.3f} M EUR")
    print(f"  Valeur de l'option de la banque = {res['call'] / 1e6:.3f} +/- {res['erreur_call'] / 1e6:.3f} M EUR "
          f"({res['call'] / N:.2%} du nominal)")
    print(f"  Probabilité de remboursement anticipé : {np.mean(res['date_rembt'] < 13):.1%}")
    print(f"  Premier coupon (6 mois) : FX {p_fx[0]:.1%} x survie {mod['survie'][0]:.1%} = {p_fx[0] * mod['survie'][0]:.1%}")
    print(f"  C > taux souverain 7 ans ({souverain:.2%}) ? {'oui' if C > souverain else 'NON : ajuster la barrière ou le 1er call'}")

    print("\nSensibilités (note au coupon C*, chocs centrés) :")
    sens = sensibilites(mod, C, res["regles"])
    for _, l in sens.iterrows():
        print(f"  {l['facteur']:36s} {l['choc']:16s} {l['variation_note_EUR']:>12,.0f} EUR "
              f"({l['variation_note_bp_nominal']:+.1f} bp du nominal)")

    # enregistrement
    table.to_csv(os.path.join(DOSSIER_SORTIES, "coupons.csv"), index=False, float_format="%.6g")
    sens.to_csv(os.path.join(DOSSIER_SORTIES, "sensibilites.csv"), index=False, float_format="%.6g")
    resume = {"date_valorisation": DATE_VALO, "nominal_EUR": N, "maturite_annees": T, "marge": MARGE,
              "coupon_annuel_avec_call": C, "coupon_annuel_sans_call": C_sans_call,
              "V0_EUR": res["V0"], "erreur_standard_V0_EUR": res["erreur"],
              "V0_sans_call_EUR": res["V0_sans_call"], "capital_actualise_EUR": capital_actualise,
              "option_banque_EUR": res["call"], "erreur_standard_option_EUR": res["erreur_call"],
              "proba_remboursement_anticipe": float(np.mean(res["date_rembt"] < 13)),
              "taux_souverain_7y": souverain, "spot_eurusd": float(mod["FX0"]), "barriere_x": float(mod["x"]),
              "hull_white": {"a": A, "sigma_r": SIGMA_R}, "rho_taux_X": -RHO_FX_TAUX,
              "nb_scenarios_par_jeu": NB_SCENARIOS, "sources": sources}
    with open(os.path.join(DOSSIER_SORTIES, "resultats.json"), "w", encoding="utf-8") as f:
        json.dump(resume, f, ensure_ascii=False, indent=2)

    faire_graphiques(mod, sim_A, sim_B, C, res, souverain, table)
    print(f"\nRésultats et graphiques enregistrés dans {DOSSIER_SORTIES}")


# =====================================================================================
# 7. GRAPHIQUES
# =====================================================================================
def faire_graphiques(mod, sim_A, sim_B, C, res, souverain, table):
    def enregistrer(nom, titre):
        plt.suptitle(titre, fontsize=10)
        plt.tight_layout()
        plt.savefig(os.path.join(DOSSIER_SORTIES, f"{nom}.png"), dpi=130)
        plt.close()

    t = sim_B["t"]

    # 1. trajectoires
    plt.figure(figsize=(11, 4))
    plt.subplot(1, 2, 1)
    plt.plot(t, sim_B["r"][:30].T * 100, lw=0.6, alpha=0.7)
    plt.xlabel("t (années)"); plt.ylabel("taux court r(t) (%)")
    plt.subplot(1, 2, 2)
    plt.plot(t, 1 / sim_B["X"][:30].T, lw=0.6, alpha=0.7)
    plt.axhline(mod["x"], color="r", ls="--", label=f"barrière x = {mod['x']:.4f}")
    plt.step(DATES_COUPON, mod["barrieres"], where="mid", color="k", lw=1.2, label="barrière effective (smile)")
    plt.xlabel("t (années)"); plt.ylabel("EUR/USD"); plt.legend(fontsize=8)
    enregistrer("1_trajectoires", "30 trajectoires simulées : taux court EUR (Hull-White) et EUR/USD")

    # 2. convergence du Monte-Carlo vers la formule fermée (sans l'option de la banque, taux déterministes)
    mod_det = dict(mod, sigma_r=1e-12, rho_X=0.0)
    sim_det = simuler(mod_det, NB_SCENARIOS, GRAINE_PRICING)
    valeurs = valeur_sans_call(sim_det, coupon_conditions(sim_det, mod_det), C)
    moitie = len(valeurs) // 2
    paires = (valeurs[:moitie] + valeurs[moitie:]) / 2 / 1e6
    n = np.arange(1, moitie + 1)
    moyenne = np.cumsum(paires) / n
    erreur = np.sqrt(np.maximum(np.cumsum(paires ** 2) / n - moyenne ** 2, 0) / n)
    plt.figure(figsize=(7, 4))
    plt.semilogx(n[20:], moyenne[20:], label="Monte-Carlo sans call")
    plt.fill_between(n[20:], (moyenne - 1.96 * erreur)[20:], (moyenne + 1.96 * erreur)[20:], alpha=0.25, label="IC 95 %")
    plt.axhline(prix_formule_fermee_sans_call(mod_det, C) / 1e6, color="r", ls="--", label="formule fermée")
    plt.xlabel("nombre de paires de scénarios"); plt.ylabel("V0 sans call (M EUR)"); plt.legend(); plt.grid(alpha=0.3)
    enregistrer("2_convergence", "Convergence du Monte-Carlo vers la formule fermée (sans call, taux déterministes)")

    # 3. la régression de Longstaff-Schwartz à deux dates de call (t = 2 ans et t = 4 ans)
    trace = {}
    recurrence_retrograde(sim_B, coupon_conditions(sim_B, mod), C, res["regles"], trace)
    plt.figure(figsize=(11, 4.2))
    for numero, i in enumerate([3, 7]):
        reel, estime = trace[i]
        plt.subplot(1, 2, numero + 1)
        plt.scatter(estime[:3000] / N, reel[:3000] / N, s=4, alpha=0.3)
        plt.axvline(1, color="r", ls="--"); plt.axhline(1, color="gray", lw=0.5)
        plt.xlabel("continuation estimée par la régression / N"); plt.ylabel("continuation réalisée / N")
        plt.title(f"t = {0.5 * (i + 1):.1f} ans : à droite de la ligne rouge la banque rembourse", fontsize=9)
        plt.grid(alpha=0.3)
    enregistrer("3_longstaff_schwartz", "On décide avec la régression, pas avec le futur réel")

    # 4. dates de remboursement
    dates, nombres = np.unique(0.5 * (res["date_rembt"] + 1), return_counts=True)
    plt.figure(figsize=(7, 4))
    plt.bar(dates, nombres / len(res["date_rembt"]) * 100, width=0.4)
    plt.xlabel("date de remboursement (années ; 7 = pas de call)"); plt.ylabel("% des scénarios")
    plt.grid(alpha=0.3, axis="y")
    enregistrer("4_dates_remboursement", "Quand la banque rembourse")

    # 5. prix en fonction du coupon
    coupons = np.linspace(max(C - 0.02, 0), C + 0.02, 9)
    plt.figure(figsize=(7, 4))
    plt.plot(coupons * 100, [prix_formule_fermee_sans_call(mod, c) / 1e6 for c in coupons], label="sans option (formule)")
    plt.plot(coupons * 100, [prix_note(mod, sim_A, sim_B, c)["V0"] / 1e6 for c in coupons], label="avec option (MC + LS)")
    plt.axhline(N * (1 - MARGE) / 1e6, color="k", ls=":", label="N (1 - m)")
    plt.axvline(C * 100, color="C1", ls="--", label=f"C* = {C:.2%}")
    plt.axvline(souverain * 100, color="gray", ls="--", label=f"souverain 7 ans = {souverain:.2%}")
    plt.xlabel("coupon annuel C (%)"); plt.ylabel("V0 (M EUR)"); plt.legend(fontsize=8); plt.grid(alpha=0.3)
    enregistrer("5_prix_vs_coupon", "Détermination du coupon par dichotomie")

    # 6. probabilité de paiement de chaque coupon
    plt.figure(figsize=(8, 4))
    plt.plot(DATES_COUPON, table["proba_fx_mc"] * 100, "o-", label="condition de change (modèle complet, taux HW et rho)")
    plt.plot(DATES_COUPON, table["proba_fx_groupe_change"] * 100, "k+", ms=9, label="groupe Change (smile, taux déterministes)")
    plt.plot(DATES_COUPON, table["survie_banques"] * 100, "s-", label="survie des banques EUR")
    plt.plot(DATES_COUPON, table["proba_coupon_paye"] * 100, "^-", label="coupon payé (les deux)")
    plt.plot(DATES_COUPON, table["proba_note_en_vie"] * 100, ":", color="gray", label="note pas encore remboursée")
    plt.xlabel("date de coupon (années)"); plt.ylabel("probabilité (%)"); plt.ylim(0, 102)
    plt.legend(fontsize=8); plt.grid(alpha=0.3)
    enregistrer("6_probabilites_coupons", "Probabilité de paiement de chaque coupon")


if __name__ == "__main__":
    main()
