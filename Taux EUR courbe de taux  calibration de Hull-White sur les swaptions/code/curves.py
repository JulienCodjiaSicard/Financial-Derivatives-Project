"""
Construction des courbes de taux EUR et USD (bootstrap) a la date de valorisation.

EUR : swaps Euribor 6M (jambe fixe annuelle), courbe unique (actualisation = projection).
USD : Daily Treasury Par Yield Curve (coupon semestriel), proxy de la courbe SOFR.

Usage : python curves.py   (depuis le dossier code/)
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.interpolate import CubicSpline, PchipInterpolator

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"

VAL_DATE = datetime(2026, 11, 13)          # date de valorisation / calibration
EUR_TENORS = list(range(1, 11))            # 1Y..10Y
USD_COLS = ["6 Mo", "1 Yr", "2 Yr", "3 Yr", "5 Yr", "7 Yr", "10 Yr"]
USD_TENOR = {"6 Mo": 0.5, "1 Yr": 1, "2 Yr": 2, "3 Yr": 3, "5 Yr": 5, "7 Yr": 7, "10 Yr": 10}


# --------------------------------------------------------------------------- #
# Chargement des donnees
# --------------------------------------------------------------------------- #
def load_eur_swaps(date: datetime = VAL_DATE) -> dict[int, float]:
    """Taux swap Euribor 6M (en decimal) a la date donnee, par maturite entiere."""
    key = date.strftime("%d/%m/%Y")
    out = {}
    for n in EUR_TENORS:
        df = pd.read_csv(DATA / f"EURAB6E{n}Y.csv", encoding="utf-8-sig", decimal=",")
        df.columns = [c.strip() for c in df.columns]
        row = df[df["Date"] == key]
        if row.empty:
            raise ValueError(f"EURAB6E{n}Y : pas de ligne pour {key}")
        out[n] = float(row["Dernier"].iloc[0]) / 100
    return out


def load_usd_par(date: datetime = VAL_DATE) -> dict[str, float]:
    """Par yields Treasury (en decimal) a la date donnee. Le CSV a un en-tete mal quote."""
    lines = (DATA / "daily-treasury-rates-us.csv").read_text(encoding="utf-8-sig").splitlines()
    hdr = [h.strip('"') for h in lines[0].strip('"').replace('""', '"').split(",")]
    key = date.strftime("%m/%d/%Y")
    for line in lines[1:]:
        r = next(csv.reader([line]))
        if r and r[0] == key:
            d = dict(zip(hdr[1:], map(float, r[1:])))
            return {k: d[k] / 100 for k in USD_COLS}
    raise ValueError(f"Treasury : pas de ligne pour {key}")


def load_overnight(date: datetime = VAL_DATE):
    """Derniers fixings overnight disponibles a la date (publies avec J+1)."""
    e = pd.read_csv(DATA / "ester_7y.csv")
    e["d"] = pd.to_datetime(e["DATE"])
    e = e[e["d"] <= date].iloc[-1]
    s = pd.read_excel(DATA / "SOFR.xlsx")
    s["d"] = pd.to_datetime(s["Effective Date"], format="%m/%d/%Y")
    s = s[s["d"] <= date].sort_values("d").iloc[-1]
    return float(e.iloc[2]) / 100, e["d"], float(s["Rate (%)"]) / 100, s["d"]


# --------------------------------------------------------------------------- #
# Courbe d'actualisation
# --------------------------------------------------------------------------- #
class Curve:
    """Courbe de facteurs d'actualisation P(0,t) construite a partir de noeuds (t_i, P_i).

    - P(t) / zero(t) : interpolation log-lineaire sur ln P (forwards constants par morceaux)
    - fwd(t)         : forward instantane lisse, via spline cubique naturelle sur le taux zero
                       continu z(t) (utile pour phi(t) de Hull-White : il faut f(0,t) continu).
      Pour t < premier noeud, z est prolonge a plat (ou ancre sur un taux court si fourni).
    """

    def __init__(self, t, P, short_rate: float | None = None):
        t = np.asarray(t, float)
        P = np.asarray(P, float)
        z = -np.log(P) / t
        if short_rate is not None:           # ancrage optionnel a t ~ 0
            t = np.r_[1e-6, t]
            z = np.r_[short_rate, z]
            P = np.exp(-z * t)
        self.t, self.P, self.z = t, P, z
        self._lnP = np.r_[0.0, np.log(P)]
        self._tt = np.r_[0.0, t]
        self._spl = CubicSpline(t, z, bc_type="natural", extrapolate=True)

    # actualisation (log-lineaire en ln P, extrapolation a taux zero plat)
    def P_at(self, t):
        t = np.asarray(t, float)
        lnP = np.interp(t, self._tt, self._lnP)
        beyond = t > self._tt[-1]
        if np.any(beyond):
            lnP = np.where(beyond, -self.z[-1] * t, lnP)
        return np.exp(lnP)

    def zero(self, t):
        t = np.asarray(t, float)
        return np.where(t > 0, -np.log(self.P_at(t)) / np.maximum(t, 1e-12), np.nan)

    def fwd_pc(self, t):
        """Forward instantane de la courbe log-lineaire (constant par morceaux)."""
        t = np.atleast_1d(np.asarray(t, float))
        eps = 1e-6
        return -(np.log(self.P_at(t + eps)) - np.log(self.P_at(np.maximum(t - eps, 0)))) / (
            t + eps - np.maximum(t - eps, 0)
        )

    def z_smooth(self, t):
        t = np.asarray(t, float)
        zt = self._spl(np.clip(t, self.t[0], self.t[-1]))
        return np.where(t < self.t[0], self.z[0], zt)

    def fwd(self, t):
        """f(0,t) = z + t z'(t), lisse."""
        t = np.atleast_1d(np.asarray(t, float))
        tc = np.clip(t, self.t[0], self.t[-1])
        dz = self._spl(tc, 1)
        dz = np.where((t < self.t[0]) | (t > self.t[-1]), 0.0, dz)
        return self.z_smooth(t) + t * dz

    def P_smooth(self, t):
        t = np.asarray(t, float)
        return np.exp(-self.z_smooth(t) * t)


# --------------------------------------------------------------------------- #
# Bootstrap EUR : swaps annuels, courbe unique
# --------------------------------------------------------------------------- #
def bootstrap_eur(swaps: dict[int, float], short_rate: float | None = None) -> Curve:
    """Taux swap par : S_n = (1 - P_n) / sum_{i<=n} P_i  (delta = 1, jambe fixe annuelle)
    => P_n = (1 - S_n * sum_{i<n} P_i) / (1 + S_n). Tous les n de 1 a 10 sont cotes : pas d'interpolation."""
    t, P, annuity = [], [], 0.0
    for n in sorted(swaps):
        Pn = (1 - swaps[n] * annuity) / (1 + swaps[n])
        annuity += Pn
        t.append(float(n))
        P.append(Pn)
    return Curve(t, P, short_rate)


# --------------------------------------------------------------------------- #
# Bootstrap USD : par yields Treasury, coupon semestriel
# --------------------------------------------------------------------------- #
def bootstrap_usd(par: dict[str, float], short_rate: float | None = None, tmax: float = 10.0) -> Curve:
    """1) 6M : P = 1/(1 + y/2).
    2) Par yields interpoles (PCHIP) sur la grille semestrielle 1.0..tmax (aux noeuds cotes, exact).
    3) Pour chaque echeance T_n : P_n = (1 - c_n/2 * sum_{i<n} P_i) / (1 + c_n/2)."""
    kn = np.array([USD_TENOR[k] for k in USD_COLS])
    yk = np.array([par[k] for k in USD_COLS])
    pchip = PchipInterpolator(kn, yk)
    grid = np.arange(0.5, tmax + 1e-9, 0.5)
    c = pchip(grid)
    P, ann = [], 0.0
    for T, ci in zip(grid, c):
        Pn = (1 - ci / 2 * ann) / (1 + ci / 2)
        ann += Pn
        P.append(Pn)
    return Curve(grid, P, short_rate), grid, c


# --------------------------------------------------------------------------- #
# Controles
# --------------------------------------------------------------------------- #
def reprice_eur(curve: Curve, swaps: dict[int, float]) -> dict[int, float]:
    err = {}
    for n, S in swaps.items():
        ann = sum(curve.P_at(i) for i in range(1, n + 1))
        err[n] = float((1 - curve.P_at(n)) / ann - S)
    return err


def reprice_usd(curve: Curve, grid, c, par: dict[str, float]) -> dict[str, float]:
    err = {}
    for k in USD_COLS:
        T = USD_TENOR[k]
        if T == 0.5:
            err[k] = float(2 * (1 / curve.P_at(0.5) - 1) - par[k])
            continue
        g = grid[grid <= T + 1e-9]
        ann = sum(curve.P_at(x) for x in g)
        err[k] = float(2 * (1 - curve.P_at(T)) / ann - par[k])
    return err


def forward_rate(curve: Curve, t1, t2):
    """Forward simple (act/1) entre t1 et t2."""
    return (curve.P_at(t1) / curve.P_at(t2) - 1) / (t2 - t1)


def main():
    OUT.mkdir(exist_ok=True)
    swaps = load_eur_swaps()
    par = load_usd_par()
    estr, d_e, sofr, d_s = load_overnight()

    eur = bootstrap_eur(swaps)
    usd, g_usd, c_usd = bootstrap_usd(par)
    eur_anch = bootstrap_eur(swaps, short_rate=estr)

    print(f"Date de valorisation : {VAL_DATE:%d/%m/%Y}")
    print(f"Overnight : EUR ESTR {estr:.3%} ({d_e:%d/%m}), SOFR {sofr:.3%} ({d_s:%d/%m})\n")

    print("== EUR : swaps Euribor 6M -> facteurs d'actualisation")
    print(f"{'T':>3} {'swap':>8} {'P(0,T)':>9} {'zero cc':>9} {'fwd 1Y':>9}")
    for n in EUR_TENORS:
        f1 = forward_rate(eur, n - 1, n) if n > 1 else float(eur.zero(1))
        print(f"{n:>3} {swaps[n]:>8.3%} {eur.P_at(n):>9.5f} {float(eur.zero(n)):>9.3%} {f1:>9.3%}")
    print("Erreur max de repricing swaps EUR :", max(abs(v) for v in reprice_eur(eur, swaps).values()))

    print("\n== USD : par yields Treasury -> facteurs d'actualisation")
    print(f"{'T':>4} {'par':>8} {'P(0,T)':>9} {'zero cc':>9}")
    for k in USD_COLS:
        T = USD_TENOR[k]
        print(f"{T:>4} {par[k]:>8.3%} {usd.P_at(T):>9.5f} {float(usd.zero(T)):>9.3%}")
    print("Erreur max de repricing par yields USD :",
          max(abs(v) for v in reprice_usd(usd, g_usd, c_usd, par).values()))

    # Dates de coupon du produit (semestrielles, 0.5..7)
    ti = np.arange(0.5, 7.0 + 1e-9, 0.5)
    print("\n== Sensibilite a l'ancrage ESTR (EUR), P(0, ti) coupons du produit")
    print(f"{'ti':>4} {'sans ancrage':>13} {'ancre ESTR':>11} {'ecart':>10}")
    for x in ti[:4]:
        a, b = float(eur.P_smooth(x)), float(eur_anch.P_smooth(x))
        print(f"{x:>4} {a:>13.5f} {b:>11.5f} {b - a:>10.5f}")
    print(f"P(0,7) EUR = {float(eur.P_at(7)):.5f}   P(0,7) USD = {float(usd.P_at(7)):.5f}")

    # Sorties CSV (grille mensuelle jusqu'a 10Y)
    tg = np.round(np.arange(0, 10 + 1e-9, 1 / 12), 6)
    tg_pos = np.where(tg == 0, 1e-6, tg)
    for name, cv in (("eur", eur), ("usd", usd)):
        pd.DataFrame({
            "t": tg,
            "P_loglin": cv.P_at(tg_pos),
            "zero_cc": cv.zero(tg_pos),
            "fwd_inst_loglin": cv.fwd_pc(tg_pos),
            "fwd_inst_lisse": cv.fwd(tg_pos),
            "P_lisse": cv.P_smooth(tg_pos),
        }).to_csv(OUT / f"curve_{name}.csv", index=False)
    pd.DataFrame({"t": ti, "P_eur": eur.P_at(ti), "P_usd": usd.P_at(ti)}).to_csv(
        OUT / "discount_coupon_dates.csv", index=False)

    # Graphiques
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(1, 3, figsize=(16, 4.5))
    ax[0].plot(tg[1:], 100 * eur.zero(tg[1:]), label="EUR zero cc")
    ax[0].plot(tg[1:], 100 * usd.zero(tg[1:]), label="USD zero cc")
    ax[0].plot(list(swaps), [100 * v for v in swaps.values()], "o", ms=4, label="swaps EUR (cotes)")
    ax[0].plot([USD_TENOR[k] for k in USD_COLS], [100 * par[k] for k in USD_COLS], "s", ms=4, label="par yields USD")
    ax[0].set_title("Taux zero-coupon (%)"); ax[0].set_xlabel("maturite (ans)"); ax[0].legend(fontsize=8)
    ax[1].step(tg[1:], 100 * eur.fwd_pc(tg[1:]), where="post", alpha=.5, label="EUR fwd (log-lin)")
    ax[1].plot(tg[1:], 100 * eur.fwd(tg[1:]), label="EUR f(0,t) lisse")
    ax[1].step(tg[1:], 100 * usd.fwd_pc(tg[1:]), where="post", alpha=.5, label="USD fwd (log-lin)")
    ax[1].plot(tg[1:], 100 * usd.fwd(tg[1:]), label="USD f(0,t) lisse")
    ax[1].set_title("Forward instantane (%)"); ax[1].set_xlabel("maturite (ans)"); ax[1].legend(fontsize=8)
    ax[2].plot(tg, eur.P_at(tg_pos), label="P EUR"); ax[2].plot(tg, usd.P_at(tg_pos), label="P USD")
    ax[2].set_title("Facteurs d'actualisation P(0,t)"); ax[2].set_xlabel("maturite (ans)"); ax[2].legend()
    for a in ax: a.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(OUT / "courbes.png", dpi=130)
    print("\nFichiers ecrits dans", OUT)


if __name__ == "__main__":
    main()
