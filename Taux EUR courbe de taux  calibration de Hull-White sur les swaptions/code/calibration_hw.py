"""
Calibration de Hull-White 1 facteur (a, sigma_r) sur les swaptions EUR co-terminales 7Y.

    dy = -a y dt + sigma_r dW,   r(t) = f(0,t) + phi(t) + y(t)

Marche : vols normales ATM (bp), swaptions sur swaps Euribor 6M, jambe fixe annuelle.
Prix marche : Bachelier ATM  = A * sigma_N * sqrt(T / 2pi)
Prix modele : Jamshidian (payer swaption = put sur obligation a coupons).
Objectif (sujet) : min sum_k (Swpt_HW - Swpt_marche)^2.

Usage : python calibration_hw.py   (depuis le dossier code/)
"""
from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.optimize import brentq, least_squares
from scipy.stats import norm

import curves as cv

SWAPTIONS = [(2, 5), (3, 4), (4, 3), (5, 2), (6, 1)]   # (expiry, tenor), co-terminales 7Y
COLS = {(2, 5): "2Yx5Y", (3, 4): "3Yx4Y", (4, 3): "4Yx3Y", (5, 2): "5Yx2Y", (6, 1): "6Yx1Y"}


# --------------------------------------------------------------------------- #
# Donnees
# --------------------------------------------------------------------------- #
def load_vols(date: datetime = cv.VAL_DATE) -> dict[tuple, float]:
    """Vols normales ATM en decimal (89.03 bp -> 0.008903)."""
    v = pd.read_excel(cv.DATA / "VolSwaption.xlsx", sheet_name=0)
    v["Date"] = pd.to_datetime(v["Date"])
    row = v[v["Date"] == pd.Timestamp(date)]
    if row.empty:
        raise ValueError(f"Pas de vols swaption au {date:%d/%m/%Y}")
    return {k: float(row[c].iloc[0]) / 1e4 for k, c in COLS.items()}


# --------------------------------------------------------------------------- #
# Swaps / Bachelier
# --------------------------------------------------------------------------- #
def swap_schedule(T, n):
    """Dates de paiement de la jambe fixe annuelle d'un swap demarrant en T, de duree n ans."""
    return np.arange(T + 1, T + n + 1, dtype=float)


def annuity(curve, T, n):
    return float(np.sum(curve.P_at(swap_schedule(T, n))))


def fwd_swap(curve, T, n):
    return float((curve.P_at(T) - curve.P_at(T + n)) / annuity(curve, T, n))


def bachelier_payer(F, K, sigma_n, T, A):
    s = sigma_n * np.sqrt(T)
    d = (F - K) / s
    return A * ((F - K) * norm.cdf(d) + s * norm.pdf(d))


def implied_normal_vol(price, F, K, T, A):
    return brentq(lambda s: bachelier_payer(F, K, s, T, A) - price, 1e-6, 0.2)


# --------------------------------------------------------------------------- #
# Hull-White 1F
# --------------------------------------------------------------------------- #
def B(a, t, T):
    tau = np.asarray(T, float) - t
    return tau if abs(a) < 1e-8 else (1 - np.exp(-a * tau)) / a


def var_y(a, sigma, T):
    """Variance de y(T) : sigma^2 (1 - e^{-2aT}) / (2a)."""
    return sigma**2 * T if abs(a) < 1e-8 else sigma**2 * (1 - np.exp(-2 * a * T)) / (2 * a)


def hw_swaption_payer(curve, a, sigma, T, n, K):
    """Payer swaption europeenne HW1F par Jamshidian.

    Sous la mesure T-forward, en notant z = y(T) - E^T[y(T)] ~ N(0, v) :
        P(T, t_i) = P(0,t_i)/P(0,T) * exp(-B_i z - B_i^2 v / 2)
    (forme exacte : l'esperance T-forward de P(T,t_i) vaut bien P(0,t_i)/P(0,T)).
    Payer = put sur l'obligation de coupons c_i = K (et 1 + K a la fin), strike 1.
    """
    ti = swap_schedule(T, n)
    c = np.full(len(ti), K)
    c[-1] += 1.0
    PT = float(curve.P_at(T))
    Fi = curve.P_at(ti) / PT
    Bi = B(a, T, ti)
    v = var_y(a, sigma, T)

    def bond(z):
        return np.sum(c * Fi * np.exp(-Bi * z - 0.5 * Bi**2 * v)) - 1.0

    z_star = brentq(bond, -1.0, 1.0)                     # bond(z) decroissant en z
    Ki = Fi * np.exp(-Bi * z_star - 0.5 * Bi**2 * v)     # strikes de Jamshidian
    s = Bi * np.sqrt(v)
    d1 = (np.log(Fi / Ki) + 0.5 * s**2) / s
    d2 = d1 - s
    puts = PT * (Ki * norm.cdf(-d2) - Fi * norm.cdf(-d1))
    return float(np.sum(c * puts))


def hw_swaption_quadrature(curve, a, sigma, T, n, K):
    """Controle independant : integration numerique adaptative du payoff sous Q^T,
    coupee au point anguleux du payoff (sinon Gauss-Hermite converge mal)."""
    from scipy.integrate import quad
    ti = swap_schedule(T, n)
    c = np.full(len(ti), K)
    c[-1] += 1.0
    PT = float(curve.P_at(T))
    Fi = curve.P_at(ti) / PT
    Bi = B(a, T, ti)
    v = var_y(a, sigma, T)
    sd = np.sqrt(v)
    bond = lambda x: np.sum(c * Fi * np.exp(-Bi * x * sd - 0.5 * Bi**2 * v))
    f = lambda x: max(1 - bond(x), 0.0) * norm.pdf(x)
    xs = brentq(lambda x: bond(x) - 1, -10, 10)
    return float(PT * (quad(f, -12, xs, epsabs=1e-14)[0] + quad(f, xs, 12, epsabs=1e-14)[0]))


# --------------------------------------------------------------------------- #
# Calibration
# --------------------------------------------------------------------------- #
def market_quotes(curve, vols):
    q = []
    for (T, n) in SWAPTIONS:
        A, F = annuity(curve, T, n), fwd_swap(curve, T, n)
        q.append(dict(T=T, n=n, F=F, A=A, vol_mkt=vols[(T, n)],
                      price_mkt=bachelier_payer(F, F, vols[(T, n)], T, A)))
    return q


def calibrate(curve, vols, x0_list=((0.03, 0.009), (0.005, 0.007), (0.15, 0.012), (0.4, 0.02))):
    q = market_quotes(curve, vols)

    def resid(x):
        a, s = x
        return np.array([(hw_swaption_payer(curve, a, s, d["T"], d["n"], d["F"]) - d["price_mkt"]) * 1e4
                         for d in q])                    # en bp de nominal

    best, runs = None, []
    for x0 in x0_list:                                  # multi-start : verifier l'unicite
        r = least_squares(resid, x0, bounds=([1e-4, 1e-4], [1.0, 0.05]), xtol=1e-12, ftol=1e-12)
        runs.append((x0, r.x, r.cost))
        if best is None or r.cost < best.cost:
            best = r
    a, s = best.x
    for d in q:
        d["price_hw"] = hw_swaption_payer(curve, a, s, d["T"], d["n"], d["F"])
        d["vol_hw"] = implied_normal_vol(d["price_hw"], d["F"], d["F"], d["T"], d["A"])
    # erreur-type approximative des parametres (jacobien)
    J = best.jac
    dof = max(len(q) - 2, 1)
    s2 = 2 * best.cost / dof
    try:
        cov = np.linalg.inv(J.T @ J) * s2
        se = np.sqrt(np.diag(cov))
        corr = cov[0, 1] / (se[0] * se[1])
    except np.linalg.LinAlgError:
        se, corr = (np.nan, np.nan), np.nan
    return dict(a=a, sigma=s, quotes=q, runs=runs, rmse_bp=np.sqrt(2 * best.cost / len(q)),
                se=se, corr=corr)


def report(res, label):
    print(f"\n===== Calibration HW1F au {label} =====")
    print(f"a       = {res['a']:.4%}   (erreur-type ~ {res['se'][0]:.2%})")
    print(f"sigma_r = {res['sigma']*1e4:.2f} bp/an  = {res['sigma']:.4%}   (erreur-type ~ {res['se'][1]*1e4:.2f} bp)")
    print(f"correlation estimee (a, sigma) = {res['corr']:.2f}")
    print(f"RMSE prix = {res['rmse_bp']:.3f} bp de nominal")
    print(f"{'swaption':>8} {'F':>7} {'vol mkt':>8} {'vol HW':>8} {'ecart':>7} {'prix mkt':>9} {'prix HW':>9}")
    for d in res["quotes"]:
        print(f"{COLS[(d['T'], d['n'])]:>8} {d['F']:>7.3%} {d['vol_mkt']*1e4:>8.2f} {d['vol_hw']*1e4:>8.2f} "
              f"{(d['vol_hw']-d['vol_mkt'])*1e4:>+7.2f} {d['price_mkt']*1e4:>9.2f} {d['price_hw']*1e4:>9.2f}")
    print("multi-start (x0 -> solution, cout) :")
    for x0, x, c in res["runs"]:
        print(f"   a0={x0[0]:<5} s0={x0[1]:<6} -> a={x[0]:.4%}  sigma={x[1]*1e4:.2f}bp  cout={c:.3e}")


def main():
    curve = cv.bootstrap_eur(cv.load_eur_swaps())
    vols = load_vols()

    # 1) Controle du pricer : Jamshidian vs quadrature, pour des parametres quelconques
    print("Controle Jamshidian vs integration numerique (a=5%, sigma=1%) :")
    for (T, n) in SWAPTIONS:
        F = fwd_swap(curve, T, n)
        pj = hw_swaption_payer(curve, 0.05, 0.01, T, n, F)
        pq = hw_swaption_quadrature(curve, 0.05, 0.01, T, n, F)
        print(f"   {COLS[(T, n)]}: Jamshidian {pj*1e4:.5f} bp  quadrature {pq*1e4:.5f} bp  ecart {abs(pj-pq)*1e4:.1e}")

    # 2) Calibration a la date de valorisation
    res = calibrate(curve, vols)
    report(res, f"{cv.VAL_DATE:%d/%m/%Y}")

    # 3) Stabilite : meme calibration la veille (courbe + vols du 12/11)
    d12 = datetime(2026, 11, 12)
    res12 = calibrate(cv.bootstrap_eur(cv.load_eur_swaps(d12)), load_vols(d12))
    print(f"\nStabilite : 12/11 -> a = {res12['a']:.4%}, sigma = {res12['sigma']*1e4:.2f} bp, "
          f"RMSE = {res12['rmse_bp']:.3f} bp")

    # 4) Sensibilite : calibration a 'a' fixe (profil du cout)
    print("\nProfil : sigma optimal et RMSE pour 'a' fixe")
    q = market_quotes(curve, vols)
    for a_fix in (0.001, 0.01, 0.02, 0.05, 0.10):
        r = least_squares(lambda s: [(hw_swaption_payer(curve, a_fix, s[0], d["T"], d["n"], d["F"])
                                      - d["price_mkt"]) * 1e4 for d in q], [0.009], bounds=([1e-4], [0.05]))
        print(f"   a = {a_fix:>5.1%} -> sigma = {r.x[0]*1e4:6.2f} bp, RMSE = {np.sqrt(2*r.cost/len(q)):.3f} bp")

    # Sorties
    cv.OUT.mkdir(exist_ok=True)
    out = dict(date=f"{cv.VAL_DATE:%Y-%m-%d}", a=res["a"], sigma_r=res["sigma"], rmse_price_bp=res["rmse_bp"],
               vol_convention="normal ATM (bp), Euribor 6M, jambe fixe annuelle, courbe unique",
               swaptions={COLS[(d['T'], d['n'])]: dict(F=d["F"], annuity=d["A"], vol_mkt_bp=d["vol_mkt"] * 1e4,
                                                       vol_hw_bp=d["vol_hw"] * 1e4, price_mkt=d["price_mkt"],
                                                       price_hw=d["price_hw"]) for d in res["quotes"]})
    (cv.OUT / "hw_params.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    lab = [COLS[(d["T"], d["n"])] for d in res["quotes"]]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(lab, [d["vol_mkt"] * 1e4 for d in res["quotes"]], "o-", label="marche (13/11/2026)")
    ax.plot(lab, [d["vol_hw"] * 1e4 for d in res["quotes"]], "s--",
            label=f"HW1F  a={res['a']:.2%}, sigma={res['sigma']*1e4:.0f}bp")
    ax.set_ylabel("vol normale ATM (bp)"); ax.set_title("Swaptions EUR co-terminales 7Y")
    ax.grid(alpha=.3); ax.legend()
    fig.tight_layout(); fig.savefig(cv.OUT / "calibration_hw.png", dpi=130)
    print("\nFichiers ecrits : output/hw_params.json, output/calibration_hw.png")


if __name__ == "__main__":
    main()
