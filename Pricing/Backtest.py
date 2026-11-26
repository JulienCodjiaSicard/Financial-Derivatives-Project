"""
Backtest.py : validation du modèle sur les 6 derniers mois (Partie 4)

Lancer :  python Backtest.py          (environ 3 minutes)

Idée : on suppose la note émise le 15/05/2026, il y a 6 mois.
  1. Emission : barrière = plus haut EUR/USD des 6 mois précédents, coupon C0 fixé par le modèle (V0 = N (1 - m)).
  2. Chaque semaine jusqu'au 13/11/2026, on revalorise la note avec le marché du jour (courbes €STR et SOFR,
     vols de swaptions, EUR/USD, spreads CDS) et on calcule ses sensibilités.
  3. Explication du P&L : la variation de valeur d'une semaine est comparée à celle prédite par les sensibilités
     (delta, gamma, taux, vol, crédit, temps). Une part non expliquée faible valide le modèle de couverture.
  4. Hypothèses du modèle face à ce qui s'est réalisé : vol du change, vol des taux, corrélation, défauts.

Données historiques lues dans les dossiers des sous-groupes, sans les modifier. Résultats dans sorties/backtest/.
"""
import json
import math
import os

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import Longstaff as L
from donnees import (RACINE, N, DELTA, DATES_COUPON, MARGE, A, SIGMA_R, RHO_FX_TAUX, FX_TENORS, FX_VOLS_ATM,
                     BARRIERES_SMILE, SURVIE_CREDIT, recuperer_donnees)

DOSSIER = os.path.join(L.DOSSIER_SORTIES, "backtest")
DOSSIER_TAUX = RACINE / "Taux EUR courbe de taux calibration de Hull-White sur les swaptions" / "data"
FICHIER_FX_LONG = RACINE / "Change" / "data" / "bce_historique_EXR.D.USD.EUR.SP00.A_2026-11-19.csv"
FICHIER_CDS = RACINE / "Credit" / "outputs" / "systemic" / "observed_credit_history.csv"
BANQUES = ["UniCredit S.p.A.", "Société Générale SA", "Commerzbank AG"]     # panier du groupe Crédit
DATE_CDS_BASE = "2026-05-20"                     # date des CDS sur lesquels le groupe Crédit a calibré la survie

EMISSION = pd.Timestamp("2026-05-15")            # première date où les courbes €STR et SOFR sont disponibles
FIN = pd.Timestamp("2026-11-13")                 # date de valorisation commune
NB = 40_000                                      # scénarios par jeu (backtest : 27 dates x 13 valorisations)


# =====================================================================================
# 1. HISTORIQUES (lecture seule)
# =====================================================================================
def historiques():
    h = {}
    for devise, fichier in (("eur", "estr_ois.csv"), ("usd", "sofr_ois.csv")):
        ois = pd.read_csv(DOSSIER_TAUX / fichier, parse_dates=["date"])
        h[devise] = {d: (g["years"].to_numpy(float), g["rate"].to_numpy(float) / 100)
                     for d, g in ois.sort_values("years").groupby("date")}

    vols = pd.read_excel(DOSSIER_TAUX / "VolSwaption.xlsx")
    vols["Date"] = pd.to_datetime(vols["Date"])
    h["vol_swaption"] = vols.set_index("Date").mean(axis=1)            # moyenne des 5 co-terminales 7Y (bp)

    fx = pd.read_csv(FICHIER_FX_LONG, usecols=["TIME_PERIOD", "OBS_VALUE"], parse_dates=["TIME_PERIOD"])
    h["fx"] = fx.dropna().set_index("TIME_PERIOD")["OBS_VALUE"].astype(float).sort_index()

    swaps = {}
    for tenor in (2, 5, 7):
        s = pd.read_csv(DOSSIER_TAUX / f"EURAB6E{tenor}Y.csv", encoding="utf-8-sig")
        swaps[tenor] = pd.Series(s["Dernier"].str.replace(",", ".").astype(float).to_numpy(),
                                 index=pd.to_datetime(s["Date"], dayfirst=True)).sort_index()
    h["swaps"] = pd.DataFrame(swaps)

    cds = pd.read_csv(FICHIER_CDS, parse_dates=["valuation_date"])
    cds = cds[cds["bank_name"].isin(BANQUES) & cds["seniority"].eq("subordinated")]
    h["cds"] = cds.pivot_table(index="valuation_date", columns="bank_name", values="spread_bps").sort_index()
    return h


def dernier(serie, date):
    """Dernière valeur disponible à la date (report de la dernière observation)."""
    return serie.loc[:date].iloc[-1]


def dernier_dict(dico, date):
    return dico[max(d for d in dico if d <= date)]


# =====================================================================================
# 2. LE MARCHE A UNE DATE PASSEE
# =====================================================================================
def taux_par(courbe, t):
    """Taux de swap OIS interpolés linéairement en maturité (prolongés à plat aux extrémités)."""
    return np.interp(t, courbe[0], courbe[1])


def marche_a_la_date(date, h, base, barriere):
    """Modèle du pricer à la date : on part des courbes du 13/11 (groupe Taux) et on les décale de la variation des
    taux de swap OIS observée entre la date et le 13/11, maturité par maturité (variation du taux par ~ variation
    du taux zéro). σ_r suit le niveau des vols de swaptions co-terminales (a fixé), la survie suit le niveau des
    spreads CDS du panier. Les vols FX et ρ, sans historique, restent ceux du 13/11."""
    tau = (date - EMISSION).days / 365
    dates = DATES_COUPON - tau                                       # coupons restants, vus depuis la date

    courbes = {}
    for devise in ("eur", "usd"):
        t_base, z_base = np.array(base[devise][0]), np.array(base[devise][1])
        decalage = taux_par(dernier_dict(h[devise], date), t_base) - taux_par(h[devise][FIN], t_base)
        courbes[devise] = (list(t_base), list(z_base + decalage))

    vol_sw = h["vol_swaption"]
    sigma_r = SIGMA_R * dernier(vol_sw, date) / dernier(vol_sw, FIN)

    niveau_cds = dernier(h["cds"].ffill(), date).mean() / h["cds"].ffill().loc[DATE_CDS_BASE].mean()

    fx0 = dernier(h["fx"], date)
    # smile : même rapport barrière effective / barrière contractuelle que celui livré par le groupe Change
    # pour la même maturité restante
    rapport_smile = np.interp(dates, FX_TENORS, BARRIERES_SMILE / base["x"])
    return {"eur": courbes["eur"], "usd": courbes["usd"], "a": A, "sigma_r": sigma_r,
            "FX0": fx0, "X0": 1 / fx0, "x": barriere, "barrieres": barriere * rapport_smile,
            "vols_fx": FX_VOLS_ATM, "rho_X": -RHO_FX_TAUX,
            "survie": SURVIE_CREDIT(dates) ** niveau_cds, "dates": dates,
            "niveau_cds": niveau_cds, "tau": tau}


# =====================================================================================
# 3. VALORISATION ET SENSIBILITES A UNE DATE
# =====================================================================================
def taux_moyen(courbe, dates):
    """Taux zéro moyen sur les dates de coupon : mesure du niveau de la courbe (pour l'explication du P&L)."""
    return float(np.mean(-np.log(L.zero_coupon(courbe, dates)) / dates))


def valoriser(mod, C, graine_A, graine_B):
    sim_A, sim_B = L.simuler(mod, NB, graine_A), L.simuler(mod, NB, graine_B)
    res = L.prix_note(mod, sim_A, sim_B, C)

    def prix(m):           # même aléa, règle d'exercice gelée
        sim = L.simuler(m, NB, graine_B)
        return L.recurrence_retrograde(sim, L.coupon_conditions(sim, m), C, res["regles"])[0].mean()

    base = prix(mod)
    semaine = 7 / 365
    chocs = {
        "fx": lambda s: dict(mod, FX0=mod["FX0"] * (1 + s * 0.01), X0=mod["X0"] / (1 + s * 0.01)),
        "eur": lambda s: dict(mod, eur=L.choquer_courbe(mod["eur"], s * 1e-4)),
        "usd": lambda s: dict(mod, usd=L.choquer_courbe(mod["usd"], s * 1e-4)),
        "sigma_r": lambda s: dict(mod, sigma_r=mod["sigma_r"] + s * 1e-3),
        "credit": lambda s: dict(mod, survie=mod["survie"] * np.exp(-s * 1e-3 * mod["dates"])),
    }
    g = {"V": base, "V0_LS": res["V0"], "V_sans_call": res["V0_sans_call"], "option": res["call"]}
    for nom, choc in chocs.items():
        haut, bas = prix(choc(+1)), prix(choc(-1))
        g[f"sens_{nom}"] = (haut - bas) / 2
        if nom == "fx":
            g["gamma_fx"] = haut + bas - 2 * base
    # thêta : même marché, une semaine de moins avant chaque flux
    vieilli = dict(mod, dates=mod["dates"] - semaine, survie=mod["survie"]
                   * SURVIE_CREDIT(mod["dates"] - semaine) ** mod["niveau_cds"] / SURVIE_CREDIT(mod["dates"]) ** mod["niveau_cds"])
    g["theta_semaine"] = prix(vieilli) - base
    return g


# =====================================================================================
# 4. EXPLICATION DU P&L
# =====================================================================================
def expliquer_pnl(v):
    """Variation hebdomadaire réelle contre variation prédite par les sensibilités de début de semaine."""
    lignes = []
    for k in range(len(v) - 1):
        a, b = v.iloc[k], v.iloc[k + 1]
        dfx = (b["fx"] / a["fx"] - 1) / 0.01                     # en nombre de chocs de 1 %
        effets = {
            "change (delta)": a["sens_fx"] * dfx,
            "change (gamma)": 0.5 * a["gamma_fx"] * dfx ** 2,
            "taux EUR": a["sens_eur"] * (b["z_eur"] - a["z_eur"]) / 1e-4,
            "taux USD": a["sens_usd"] * (b["z_usd"] - a["z_usd"]) / 1e-4,
            "vol des taux": a["sens_sigma_r"] * (b["sigma_r"] - a["sigma_r"]) / 1e-3,
            # survie S^k : variation de ln S équivalente à un choc parallèle d'intensité sur 7 ans
            "crédit": a["sens_credit"] * (b["niveau_cds"] - a["niveau_cds"]) * a["intensite_moyenne"] / 1e-3,
            "temps": a["theta_semaine"] * (b["date"] - a["date"]).days / 7,
        }
        reel = b["V"] - a["V"]
        explique = sum(effets.values())
        lignes.append({"debut": a["date"].date(), "fin": b["date"].date(), "pnl_reel": reel, **effets,
                       "pnl_explique": explique, "non_explique": reel - explique})
    return pd.DataFrame(lignes)


# =====================================================================================
# 5. HYPOTHESES DU MODELE FACE AU REALISE
# =====================================================================================
def hypotheses(h, v):
    periode = slice(EMISSION, FIN)
    fx = h["fx"].loc[periode]
    rend = np.log(fx).diff().dropna()
    vol_fx_realisee = rend.std() * math.sqrt(252)
    vol_fx_implicite = float(np.sqrt(np.interp(0.5, FX_TENORS, FX_VOLS_ATM ** 2)))
    hebdo = np.log(fx.resample("W-FRI").last()).diff().dropna()
    hors_ic = float(np.mean(np.abs(hebdo / (vol_fx_implicite * math.sqrt(1 / 52))) > 1.96))

    swaps = h["swaps"].loc[periode]
    dsw = swaps.diff().dropna() * 100                                 # en bp par jour
    lignes = [
        {"hypothese": "Vol EUR/USD (annuelle)", "modele": vol_fx_implicite, "realise": vol_fx_realisee,
         "commentaire": "vol implicite ATM 6M (13/11) contre vol réalisée des fixings BCE"},
        {"hypothese": "Rendements hebdo EUR/USD hors IC 95 % du modèle", "modele": 0.05, "realise": hors_ic,
         "commentaire": "part des semaines où |rendement| > 1,96 vol implicite"},
    ]
    sigma_moyen = float(v["sigma_r"].mean())
    for tenor in (2, 5, 7):
        vol_modele = sigma_moyen * (1 - math.exp(-A * tenor)) / (A * tenor) * 1e4
        lignes.append({"hypothese": f"Vol normale du swap EUR {tenor} ans (bp/an)", "modele": vol_modele,
                       "realise": dsw[tenor].std() * math.sqrt(252),
                       "commentaire": "Hull-White (σ_r moyen de la période) contre variations journalières réalisées"})
    x = -np.log(h["fx"]).diff()                                       # ln X = -ln FX
    joint = pd.concat([swaps[2].diff(), x], axis=1, join="inner").dropna()
    lignes.append({"hypothese": "Corrélation taux EUR / X = 1/FX", "modele": -RHO_FX_TAUX,
                   "realise": joint.corr().iloc[0, 1],
                   "commentaire": "swap 2 ans contre ln X, variations journalières sur la période"})
    lignes.append({"hypothese": "Défauts dans le panier de banques EUR", "modele": 1 - float(v["survie_6m"].iloc[0]),
                   "realise": 0.0, "commentaire": "probabilité de défaut à 6 mois à l'émission contre défaut observé"})
    return pd.DataFrame(lignes)


# =====================================================================================
# 6. PROGRAMME PRINCIPAL
# =====================================================================================
def main():
    os.makedirs(DOSSIER, exist_ok=True)
    h = historiques()
    cours_fx, courbe_usd, courbe_eur, _, _ = recuperer_donnees()
    base = {"eur": courbe_eur, "usd": courbe_usd, "x": float(cours_fx.max())}     # courbes et barrière du 13/11

    # émission : barrière = plus haut des 6 mois précédents, coupon fixé par le modèle
    barriere = float(h["fx"].loc[EMISSION - pd.DateOffset(months=6):EMISSION].max())
    mod0 = marche_a_la_date(EMISSION, h, base, barriere)
    sim_A, sim_B = L.simuler(mod0, NB, 101), L.simuler(mod0, NB, 202)
    C0 = L.trouver_coupon(mod0, sim_A, sim_B)
    p_fx1 = float((sim_B["X"][:, sim_B["idx"][0]] > 1 / mod0["barrieres"][0]).mean())
    print(f"Emission le {EMISSION.date()} : spot {mod0['FX0']:.4f}, barrière {barriere:.4f}, coupon C0 = {C0:.3%}")

    dates = sorted(d for d in h["eur"] if EMISSION <= d <= FIN)
    lignes = []
    for d in dates:
        m = marche_a_la_date(d, h, base, barriere)
        g = valoriser(m, C0, 101, 202)
        lignes.append({"date": d, "fx": m["FX0"], "z_eur": taux_moyen(m["eur"], m["dates"]),
                       "z_usd": taux_moyen(m["usd"], m["dates"]), "sigma_r": m["sigma_r"],
                       "niveau_cds": m["niveau_cds"], "survie_6m": float(m["survie"][0]),
                       "intensite_moyenne": float(-np.log(SURVIE_CREDIT(m["dates"][-1])) / m["dates"][-1]), **g})
        print(f"  {d.date()}  EUR/USD {m['FX0']:.4f}  V = {g['V'] / 1e6:7.3f} M EUR  option {g['option'] / 1e6:.3f}")
    v = pd.DataFrame(lignes)
    pnl = expliquer_pnl(v)
    hyp = hypotheses(h, v)

    part_expliquee = 1 - pnl["non_explique"].var() / pnl["pnl_reel"].var()
    m_fin = marche_a_la_date(FIN, h, base, barriere)
    resume = {
        "emission": str(EMISSION.date()), "fin": str(FIN.date()), "barriere": barriere, "coupon_C0": C0,
        "V_emission_EUR": float(v["V"].iloc[0]), "V_fin_EUR": float(v["V"].iloc[-1]),
        "pnl_total_EUR": float(v["V"].iloc[-1] - v["V"].iloc[0]),
        "part_variance_pnl_expliquee": float(part_expliquee),
        "erreur_moyenne_absolue_EUR": float(pnl["non_explique"].abs().mean()),
        "pnl_hebdo_moyen_absolu_EUR": float(pnl["pnl_reel"].abs().mean()),
        "premier_coupon": {"date_fixing": str((EMISSION + pd.DateOffset(months=6)).date()),
                           "proba_fx_a_l_emission": p_fx1, "survie_a_l_emission": float(mod0["survie"][0]),
                           "eurusd_au_13_11": float(m_fin["FX0"]), "barriere_effective": float(m_fin["barrieres"][0]),
                           "condition_fx_respectee_au_13_11": bool(m_fin["FX0"] < m_fin["barrieres"][0])},
        "nb_scenarios_par_jeu": NB,
    }
    v.to_csv(os.path.join(DOSSIER, "valorisations.csv"), index=False, float_format="%.6g")
    pnl.to_csv(os.path.join(DOSSIER, "explication_pnl.csv"), index=False, float_format="%.6g")
    hyp.to_csv(os.path.join(DOSSIER, "hypotheses.csv"), index=False, float_format="%.6g")
    with open(os.path.join(DOSSIER, "resume.json"), "w", encoding="utf-8") as f:
        json.dump(resume, f, ensure_ascii=False, indent=2)

    print(f"\nP&L total du 15/05 au 13/11 : {resume['pnl_total_EUR'] / 1e6:+.3f} M EUR")
    print(f"Part de la variance du P&L hebdomadaire expliquée par les sensibilités : {part_expliquee:.1%}")
    print(f"P&L hebdo moyen {resume['pnl_hebdo_moyen_absolu_EUR']:,.0f} EUR ; "
          f"non expliqué moyen {resume['erreur_moyenne_absolue_EUR']:,.0f} EUR")
    pc = resume["premier_coupon"]
    print(f"Premier coupon (fixing {pc['date_fixing']}) : proba à l'émission {pc['proba_fx_a_l_emission'] * pc['survie_a_l_emission']:.1%} ; "
          f"au 13/11 EUR/USD {pc['eurusd_au_13_11']:.4f} < barrière {pc['barriere_effective']:.4f} : "
          f"{'oui' if pc['condition_fx_respectee_au_13_11'] else 'non'}")
    print("\nHypothèses du modèle face au réalisé :")
    for _, l in hyp.iterrows():
        print(f"  {l['hypothese']:50s} modèle {l['modele']:8.4f}   réalisé {l['realise']:8.4f}")
    graphiques(v, pnl, barriere)
    print(f"\nRésultats enregistrés dans {DOSSIER}")


def graphiques(v, pnl, barriere):
    def enregistrer(nom, titre):
        plt.suptitle(titre, fontsize=10)
        plt.tight_layout()
        plt.savefig(os.path.join(DOSSIER, f"{nom}.png"), dpi=130)
        plt.close()

    plt.figure(figsize=(11, 6.5))
    plt.subplot(2, 2, 1)
    plt.plot(v["date"], v["V"] / N * 100, "o-", ms=3)
    plt.axhline((1 - MARGE) * 100, color="gray", ls=":", label="prix d'émission N (1 - m)")
    plt.ylabel("valeur de la note (% du nominal)"); plt.legend(fontsize=8); plt.grid(alpha=0.3)
    plt.subplot(2, 2, 2)
    plt.plot(v["date"], v["fx"], "o-", ms=3)
    plt.axhline(barriere, color="r", ls="--", label=f"barrière {barriere:.4f}")
    plt.ylabel("EUR/USD"); plt.legend(fontsize=8); plt.grid(alpha=0.3)
    plt.subplot(2, 2, 3)
    plt.plot(v["date"], v["z_eur"] * 100, label="€STR (taux zéro moyen)")
    plt.plot(v["date"], v["z_usd"] * 100, label="SOFR (taux zéro moyen)")
    plt.ylabel("taux (%)"); plt.legend(fontsize=8); plt.grid(alpha=0.3)
    plt.subplot(2, 2, 4)
    plt.plot(v["date"], v["option"] / 1e6, label="option de la banque (M EUR)")
    plt.plot(v["date"], v["sigma_r"] * 1e4 / 100, label="σ_r (centaines de bp)")
    plt.legend(fontsize=8); plt.grid(alpha=0.3)
    for ax in plt.gcf().axes:
        ax.tick_params(axis="x", labelrotation=30, labelsize=7)
    enregistrer("7_backtest_valeur", "Backtest : note émise le 15/05/2026, revalorisée chaque semaine")

    plt.figure(figsize=(11, 4.2))
    plt.subplot(1, 2, 1)
    x = np.arange(len(pnl))
    effets = ["change (delta)", "change (gamma)", "taux EUR", "taux USD", "vol des taux", "crédit", "temps"]
    bas_pos, bas_neg = np.zeros(len(pnl)), np.zeros(len(pnl))
    for e in effets:
        val = pnl[e].to_numpy() / 1e3
        bas = np.where(val >= 0, bas_pos, bas_neg)
        plt.bar(x, val, bottom=bas, label=e, width=0.8)
        bas_pos += np.maximum(val, 0); bas_neg += np.minimum(val, 0)
    plt.plot(x, pnl["pnl_reel"] / 1e3, "k_", ms=12, mew=2, label="P&L réel")
    plt.xlabel("semaine"); plt.ylabel("k EUR"); plt.legend(fontsize=7, ncol=2); plt.grid(alpha=0.3, axis="y")
    plt.subplot(1, 2, 2)
    plt.scatter(pnl["pnl_explique"] / 1e3, pnl["pnl_reel"] / 1e3)
    lim = np.abs(pnl[["pnl_explique", "pnl_reel"]].to_numpy()).max() / 1e3 * 1.1
    plt.plot([-lim, lim], [-lim, lim], "r--", lw=1)
    plt.xlabel("P&L prédit par les sensibilités (k EUR)"); plt.ylabel("P&L réel (k EUR)"); plt.grid(alpha=0.3)
    enregistrer("8_backtest_pnl", "Explication du P&L hebdomadaire par les sensibilités")


if __name__ == "__main__":
    main()
