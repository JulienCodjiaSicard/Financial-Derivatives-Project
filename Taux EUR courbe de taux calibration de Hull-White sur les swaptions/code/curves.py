"""
Construction des courbes de taux EUR et USD (bootstrap OIS, cadre bi-courbe) a la date de valo.

EUR  - actualisation : courbe OIS €STR (swaps €STR 1Y..30Y, CheckMySwap / DTCC)
                       + fixing €STR overnight pour la partie [0, spot]
                       + point 6M synthetique = fixing Euribor 6M - base 1Y (si fixing dispo).
     - projection    : courbe Euribor 6M, bootstrappee sur le depot 6M (fixing Euribor 6M,
                       data/euribor6m_fixings.csv) puis les swaps Euribor 6M 1Y..10Y
                       (jambe fixe annuelle 30/360, jambe variable semestrielle) en
                       actualisant sur €STR.
USD  - actualisation : courbe OIS SOFR. A la date de valo : courbe Eris (1W..50Y, dates
                       exactes). Sinon (backtest) : swaps SOFR 1Y..30Y CheckMySwap.
                       + fixing SOFR overnight pour [0, spot].

Conventions retenues (simplifications a mentionner dans le rapport) :
- temps t en act/365 depuis la date de valorisation ; accruals OIS en act/360 ;
- spot = J+2 jours ouvres (week-ends seulement, pas de calendrier de jours feries) ;
- dates de paiement non ajustees, decalage de paiement OIS (+2j) ignore ;
- OIS <= 1 an : un seul flux a maturite ; > 1 an : flux annuels, stub court en tete ;
- interpolation log-lineaire sur ln P entre noeuds pour le bootstrap (forwards constants
  par morceaux) ; spline cubique sur le taux zero pour le forward lisse de Hull-White.

Usage : python curves.py   (depuis le dossier code/)
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta
from scipy.interpolate import CubicSpline
from scipy.optimize import brentq

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"

VAL_DATE = datetime(2026, 11, 13)          # date de valorisation / calibration
EUR_TENORS = list(range(1, 11))            # swaps Euribor 6M 1Y..10Y
MAX_OIS_YEARS = 30                         # on ignore les maturites OIS au-dela
ESTR_OIS_FILE = DATA / "estr_ois.csv"      # CheckMySwap EUR (cf. download_ois.py)
SOFR_OIS_FILE = DATA / "sofr_ois.csv"      # CheckMySwap USD
ERIS_FILE = "sofr_ois_eris_{d:%Y%m%d}.csv"
USD_TREASURY_COLS = ["6 Mo", "1 Yr", "2 Yr", "3 Yr", "5 Yr", "7 Yr", "10 Yr"]
USD_TREASURY_TENOR = {"6 Mo": 0.5, "1 Yr": 1, "2 Yr": 2, "3 Yr": 3, "5 Yr": 5, "7 Yr": 7, "10 Yr": 10}


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #
def yf(d0: datetime, d1: datetime, basis: float = 365.0) -> float:
    return (d1 - d0).days / basis


def add_bdays(d: datetime, n: int) -> datetime:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def spot_date(date: datetime) -> datetime:
    return add_bdays(date, 2)


def schedule(start: datetime, end: datetime, months: int = 12) -> list[datetime]:
    """Dates de fin de periode, generees a rebours depuis `end` (stub court en tete).
    Un instrument de duree <= `months` (+ quelques jours) n'a qu'un seul flux."""
    dates, k = [end], 1
    while True:
        d = end - relativedelta(months=months * k)
        if d <= start + timedelta(days=7):
            break
        dates.append(d)
        k += 1
    return sorted(dates)


# --------------------------------------------------------------------------- #
# Chargement des donnees
# --------------------------------------------------------------------------- #
def load_eur_swaps(date: datetime = VAL_DATE) -> dict[int, float]:
    """Taux swap Euribor 6M (en decimal) a la date donnee, par maturite entiere.
    Une maturite absente ce jour-la est ignoree (le bootstrap interpole entre ses voisines) ;
    le 1Y et le 10Y sont obligatoires. NB : l'historique EURAB6E9Y ne couvre qu'un mois."""
    key = date.strftime("%d/%m/%Y")
    out = {}
    for n in EUR_TENORS:
        df = pd.read_csv(DATA / f"EURAB6E{n}Y.csv", encoding="utf-8-sig", decimal=",")
        df.columns = [c.strip() for c in df.columns]
        row = df[df["Date"] == key]
        if not row.empty:
            out[n] = float(row["Dernier"].iloc[0]) / 100
    missing = [n for n in EUR_TENORS if n not in out]
    if EUR_TENORS[0] in missing or EUR_TENORS[-1] in missing or len(missing) > 2:
        raise ValueError(f"Swaps Euribor 6M au {key} : maturites manquantes {missing}")
    return out


def load_overnight(date: datetime = VAL_DATE):
    """Derniers fixings overnight disponibles a la date (publies avec J+1)."""
    e = pd.read_csv(DATA / "ester_7y.csv")
    e["d"] = pd.to_datetime(e["DATE"])
    e = e[e["d"] <= date].iloc[-1]
    s = pd.read_excel(DATA / "SOFR.xlsx")
    s["d"] = pd.to_datetime(s["Effective Date"], format="%m/%d/%Y")
    s = s[s["d"] <= date].sort_values("d").iloc[-1]
    return float(e.iloc[2]) / 100, e["d"], float(s["Rate (%)"]) / 100, s["d"]


def load_euribor6m_fixing(date: datetime = VAL_DATE) -> float | None:
    """Fixing Euribor 6M (depot 6M, act/360, valeur spot) du jour, en decimal ; None si absent.
    Source : euribor-rates.eu (fixings EMMI publies avec delai). Fichier : data/euribor6m_fixings.csv."""
    f = DATA / "euribor6m_fixings.csv"
    if not f.exists():
        return None
    df = pd.read_csv(f)
    row = df[df["date"] == date.strftime("%Y-%m-%d")]
    return float(row["rate"].iloc[0]) / 100 if not row.empty else None


def _cms_instruments(path: Path, date: datetime) -> list[dict]:
    """Swaps OIS CheckMySwap (tenors entiers en annees) -> instruments spot-starting."""
    if not path.exists():
        raise FileNotFoundError(f"{path.name} absent : lancer download_ois.py")
    df = pd.read_csv(path).drop_duplicates(["date", "tenor"], keep="last")
    df = df[df["date"] == date.strftime("%Y-%m-%d")]
    if df.empty:
        raise ValueError(f"{path.name} : pas de courbe au {date:%d/%m/%Y}")
    s = spot_date(date)
    ins = []
    for _, r in df.sort_values("years").iterrows():
        if r["years"] > MAX_OIS_YEARS:
            continue
        ins.append(dict(name=r["tenor"], start=s, end=s + relativedelta(years=int(r["years"])),
                        rate=float(r["rate"]) / 100))
    return ins


def load_estr_ois(date: datetime = VAL_DATE) -> list[dict]:
    return _cms_instruments(ESTR_OIS_FILE, date)


def load_sofr_ois(date: datetime = VAL_DATE) -> tuple[list[dict], str]:
    """Eris (courbe complete, dates exactes) si le fichier du jour existe, sinon CheckMySwap."""
    f = DATA / ERIS_FILE.format(d=date)
    if not f.exists():
        return _cms_instruments(SOFR_OIS_FILE, date), "CheckMySwap"
    df = pd.read_csv(f)
    ins = []
    for _, r in df.iterrows():
        if r["Symbol"] == "SOFR1D":                      # redondant avec le fixing overnight
            continue
        start = datetime.strptime(r["EffectiveDate"], "%m/%d/%Y")
        end = datetime.strptime(r["CashFlowAlignmentDate"], "%m/%d/%Y")
        if yf(date, end) > MAX_OIS_YEARS + 0.1:
            continue
        ins.append(dict(name=r["Symbol"].replace("SOFR", ""), start=start, end=end,
                        rate=float(r["FairCoupon (%)"]) / 100))
    return sorted(ins, key=lambda x: x["end"]), "Eris"


def ois_dates(path: Path | None = None) -> list[datetime]:
    """Dates disponibles dans un fichier CheckMySwap (€STR par defaut ; utile pour le backtest)."""
    path = path or ESTR_OIS_FILE
    if not path.exists():
        return []
    return sorted(datetime.strptime(d, "%Y-%m-%d") for d in pd.read_csv(path)["date"].unique())


def load_usd_treasury(date: datetime = VAL_DATE) -> dict[str, float]:
    """Par yields Treasury (decimal). Conserve uniquement pour comparaison (swap spread)."""
    lines = (DATA / "daily-treasury-rates-us.csv").read_text(encoding="utf-8-sig").splitlines()
    hdr = [h.strip('"') for h in lines[0].strip('"').replace('""', '"').split(",")]
    key = date.strftime("%m/%d/%Y")
    for line in lines[1:]:
        r = next(csv.reader([line]))
        if r and r[0] == key:
            d = dict(zip(hdr[1:], map(float, r[1:])))
            return {k: d[k] / 100 for k in USD_TREASURY_COLS}
    raise ValueError(f"Treasury : pas de ligne pour {key}")


# --------------------------------------------------------------------------- #
# Courbe d'actualisation
# --------------------------------------------------------------------------- #
class Curve:
    """Courbe de facteurs d'actualisation P(0,t) construite a partir de noeuds (t_i, P_i), t_i > 0.

    - P_at / zero : interpolation log-lineaire sur ln P (forwards constants par morceaux),
                    extrapolation a taux zero plat au-dela du dernier noeud
    - fwd(t)      : forward instantane lisse, via spline cubique naturelle sur le taux zero
                    continu z(t) (utile pour phi(t) de Hull-White : il faut f(0,t) continu).
    """

    def __init__(self, t, P):
        t = np.asarray(t, float)
        P = np.asarray(P, float)
        self.t, self.P = t, P
        self.z = -np.log(P) / t
        self._lnP = np.r_[0.0, np.log(P)]
        self._tt = np.r_[0.0, t]
        self._spl = CubicSpline(t, self.z, bc_type="natural", extrapolate=True)

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
        return np.where(t < self.t[0], self.z[0], np.where(t > self.t[-1], self.z[-1], zt))

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


class _NodeBuilder:
    """Noeuds (t, ln P) croissants + interpolation log-lineaire ; sert au bootstrap sequentiel."""

    def __init__(self):
        self.t, self.lnP = [0.0], [0.0]

    def lnP_at(self, t, extra=None):
        tt, ll = self.t, self.lnP
        if extra is not None:
            tt, ll = tt + [extra[0]], ll + [extra[1]]
        return np.interp(t, tt, ll)

    def P(self, t, extra=None):
        return np.exp(self.lnP_at(t, extra))

    def add(self, t, lnP):
        self.t.append(t)
        self.lnP.append(lnP)

    def curve(self) -> Curve:
        return Curve(self.t[1:], np.exp(self.lnP[1:]))


# --------------------------------------------------------------------------- #
# Bootstrap OIS (EUR €STR, USD SOFR)
# --------------------------------------------------------------------------- #
def bootstrap_ois(date: datetime, on_rate: float, instruments: list[dict]) -> Curve:
    """1) Noeud au spot : P(0,spot) = 1 / (1 + r_ON * j/360) (fixing overnight sur [0, spot]).
    2) Pour chaque OIS (start, end, S), par maturite croissante, on cherche ln P(end) tel que
         jambe variable = jambe fixe :  P(start) - P(end) = S * sum_k delta_k P(t_k),
       delta_k en act/360, les P(t_k) intermediaires interpoles log-lineairement."""
    nb = _NodeBuilder()
    s = min(i["start"] for i in instruments)
    nb.add(yf(date, s), -np.log1p(on_rate * yf(date, s, 360)))
    for ins in sorted(instruments, key=lambda x: x["end"]):
        pay = schedule(ins["start"], ins["end"])
        acc = np.array([yf(a, b, 360) for a, b in zip([ins["start"]] + pay[:-1], pay)])
        tp = np.array([yf(date, d) for d in pay])
        ts, te = yf(date, ins["start"]), tp[-1]

        def pv(x):
            ex = (te, x)
            return nb.P(ts, ex) - np.exp(x) - ins["rate"] * np.sum(acc * nb.P(tp, ex))

        last = nb.lnP[-1]
        nb.add(te, brentq(pv, last - 2.0, last + 0.5, xtol=1e-14))
    return nb.curve()


def reprice_ois(curve: Curve, date: datetime, instruments: list[dict]) -> dict[str, float]:
    err = {}
    for ins in instruments:
        pay = schedule(ins["start"], ins["end"])
        acc = np.array([yf(a, b, 360) for a, b in zip([ins["start"]] + pay[:-1], pay)])
        tp = np.array([yf(date, d) for d in pay])
        par = (curve.P_at(yf(date, ins["start"])) - curve.P_at(tp[-1])) / np.sum(acc * curve.P_at(tp))
        err[ins["name"]] = float(par - ins["rate"])
    return err


# --------------------------------------------------------------------------- #
# Bootstrap Euribor 6M (projection) avec actualisation €STR
# --------------------------------------------------------------------------- #
def _euribor_legs(date: datetime, n: int):
    """Dates (en t) de la jambe fixe annuelle (30/360 -> delta = 1) et de la jambe variable 6M."""
    s = spot_date(date)
    t_fix = np.array([yf(date, s + relativedelta(years=k)) for k in range(1, n + 1)])
    t_flt = np.array([yf(date, s + relativedelta(months=6 * j)) for j in range(0, 2 * n + 1)])
    return t_fix, t_flt


def bootstrap_euribor6m(date: datetime, swaps: dict[int, float], disc: Curve,
                        depo6m: float | None = None) -> Curve:
    """1) Depot 6M (fixing Euribor 6M, act/360) : P_E(spot+6M) = P_E(spot) / (1 + L * j/360).
    2) Swap Euribor 6M de maturite n, actualise sur €STR :
         S_n * sum_k P_ois(T_k) = sum_j P_ois(t_j) * (P_E(t_{j-1}) / P_E(t_j) - 1)
    Inconnue par swap : P_E(n) ; les points semestriels intermediaires sont interpoles
    log-lineairement. Sans depot, le premier fixing 6M est implicite dans le swap 1Y."""
    nb = _NodeBuilder()
    s = spot_date(date)
    t_spot = yf(date, s)
    nb.add(t_spot, float(np.log(disc.P_at(t_spot))))   # normalisation : seuls les ratios comptent
    if depo6m is not None:
        e6 = s + relativedelta(months=6)
        nb.add(yf(date, e6), nb.lnP[-1] - np.log1p(depo6m * yf(s, e6, 360)))
    for n in sorted(swaps):
        t_fix, t_flt = _euribor_legs(date, n)
        ann = np.sum(disc.P_at(t_fix))
        Pd = disc.P_at(t_flt[1:])

        def pv(x):
            PE = nb.P(t_flt, (t_flt[-1], x))
            return np.sum(Pd * (PE[:-1] / PE[1:] - 1)) - swaps[n] * ann

        last = nb.lnP[-1]
        nb.add(t_flt[-1], brentq(pv, last - 1.0, last + 0.2, xtol=1e-14))
    return nb.curve()


def reprice_euribor(proj: Curve, disc: Curve, date: datetime, swaps: dict[int, float]) -> dict[int, float]:
    err = {}
    for n, S in swaps.items():
        t_fix, t_flt = _euribor_legs(date, n)
        PE = proj.P_at(t_flt)
        flt = np.sum(disc.P_at(t_flt[1:]) * (PE[:-1] / PE[1:] - 1))
        err[n] = float(flt / np.sum(disc.P_at(t_fix)) - S)
    return err


# --------------------------------------------------------------------------- #
# Constructeurs haut niveau (utilises par la calibration, le pricing, le backtest)
# --------------------------------------------------------------------------- #
def estr_6m_from_euribor(date: datetime, depo6m: float, swaps: dict[int, float],
                         ois: list[dict]) -> dict | None:
    """Point OIS €STR 6M synthetique (CheckMySwap ne cote rien sous 1 an) :
         OIS_6M = Euribor 6M - base,  base = swap Euribor 6M 1Y - OIS €STR 1Y
    (base supposee plate entre 6M et 1Y). None si le 1Y manque d'un cote ou de l'autre."""
    o1 = next((i["rate"] for i in ois if i["name"] == "1Y"), None)
    if o1 is None or 1 not in swaps:
        return None
    s = spot_date(date)
    return dict(name="6M*", start=s, end=s + relativedelta(months=6), rate=depo6m - (swaps[1] - o1),
                synthetic=True, basis=swaps[1] - o1)


def build_eur(date: datetime = VAL_DATE, use_depo: bool = True):
    """-> (courbe €STR d'actualisation, courbe Euribor 6M de projection, infos).
    Si le fixing Euribor 6M du jour est disponible (et use_depo) : depot 6M en premier pilier
    de la courbe Euribor, et point €STR 6M synthetique dans la courbe d'actualisation."""
    estr, d_e, _, _ = load_overnight(date)
    ins = load_estr_ois(date)
    swaps = load_eur_swaps(date)
    depo = load_euribor6m_fixing(date) if use_depo else None
    syn = estr_6m_from_euribor(date, depo, swaps, ins) if depo is not None else None
    ins_all = ([syn] if syn else []) + ins
    disc = bootstrap_ois(date, estr, ins_all)
    proj = bootstrap_euribor6m(date, swaps, disc, depo)
    return disc, proj, dict(ois=ins_all, swaps=swaps, on=estr, on_date=d_e, depo6m=depo, estr6m=syn)


def build_usd(date: datetime = VAL_DATE):
    """-> (courbe SOFR d'actualisation, infos)."""
    _, _, sofr, d_s = load_overnight(date)
    ins, src = load_sofr_ois(date)
    return bootstrap_ois(date, sofr, ins), dict(ois=ins, source=src, on=sofr, on_date=d_s)


def forward_rate(curve: Curve, t1, t2):
    """Forward simple (act/1) entre t1 et t2."""
    return (curve.P_at(t1) / curve.P_at(t2) - 1) / (t2 - t1)


def main():
    OUT.mkdir(exist_ok=True)
    eur, e6m, ie = build_eur()
    usd, iu = build_usd()

    print(f"Date de valorisation : {VAL_DATE:%d/%m/%Y}  (spot {spot_date(VAL_DATE):%d/%m/%Y})")
    print(f"Overnight : €STR {ie['on']:.3%} ({ie['on_date']:%d/%m}), SOFR {iu['on']:.3%} ({iu['on_date']:%d/%m})\n")

    print("== EUR €STR OIS -> courbe d'actualisation")
    print(f"{'tenor':>5} {'OIS':>8} {'t':>6} {'P(0,t)':>9} {'zero cc':>9}")
    for i in ie["ois"]:
        t = yf(VAL_DATE, i["end"])
        print(f"{i['name']:>5} {i['rate']:>8.3%} {t:>6.2f} {float(eur.P_at(t)):>9.5f} {float(eur.zero(t)):>9.3%}")
    print("Erreur max de repricing OIS €STR :", f"{max(abs(v) for v in reprice_ois(eur, VAL_DATE, ie['ois']).values()):.1e}")
    if ie["estr6m"]:
        print(f"  6M* = point synthetique : Euribor 6M {ie['depo6m']:.3%} - base 1Y {ie['estr6m']['basis']*1e4:.1f} pb")
    else:
        print("  pas de point €STR < 1 an (fixing Euribor 6M absent) : forward plat entre ON et 1Y")

    print("\n== EUR Euribor 6M (projection, actualisation €STR)")
    if ie["depo6m"] is not None:
        s6 = spot_date(VAL_DATE)
        e6 = s6 + relativedelta(months=6)
        L = (e6m.P_at(yf(VAL_DATE, s6)) / e6m.P_at(yf(VAL_DATE, e6)) - 1) / yf(s6, e6, 360)
        print(f"Depot 6M (fixing) {ie['depo6m']:.3%} -> reprice {float(L):.3%}")
    print(f"{'T':>3} {'swap':>8} {'OIS':>8} {'base':>7} {'fwd6M a T-0.5':>14}")
    ois_par = {int(i["name"][:-1]): i["rate"] for i in ie["ois"] if i["name"].endswith("Y")}
    for n in sorted(ie["swaps"]):
        t1, t2 = _euribor_legs(VAL_DATE, n)[1][-2:]
        S, o = ie["swaps"][n], ois_par.get(n)
        o_txt = f"{o:.3%}" if o else "-"
        b_txt = f"{(S - o) * 1e4:.1f}" if o else "-"
        print(f"{n:>3} {S:>8.3%} {o_txt:>8} {b_txt:>7} {float(forward_rate(e6m, t1, t2)):>14.3%}")
    print("Erreur max de repricing swaps Euribor 6M :",
          f"{max(abs(v) for v in reprice_euribor(e6m, eur, VAL_DATE, ie['swaps']).values()):.1e}")

    print(f"\n== USD SOFR OIS ({iu['source']}) -> courbe d'actualisation")
    print(f"{'tenor':>5} {'OIS':>8} {'t':>6} {'P(0,t)':>9} {'zero cc':>9}")
    for i in iu["ois"]:
        t = yf(VAL_DATE, i["end"])
        if t <= 10.1:
            print(f"{i['name']:>5} {i['rate']:>8.3%} {t:>6.2f} {float(usd.P_at(t)):>9.5f} {float(usd.zero(t)):>9.3%}")
    print("Erreur max de repricing OIS SOFR :", f"{max(abs(v) for v in reprice_ois(usd, VAL_DATE, iu['ois']).values()):.1e}")
    try:
        tr = load_usd_treasury()
        print("Swap spread SOFR - Treasury (pb) :",
              ", ".join(f"{k} {(float(-np.log(usd.P_at(USD_TREASURY_TENOR[k]))/USD_TREASURY_TENOR[k]) - tr[k])*1e4:+.0f}"
                        for k in ("2 Yr", "5 Yr", "7 Yr", "10 Yr")), "(zero SOFR vs par Treasury, indicatif)")
    except (ValueError, FileNotFoundError):
        pass

    ti = np.arange(0.5, 7.0 + 1e-9, 0.5)          # dates de coupon du produit
    print(f"\nP(0,7) €STR = {float(eur.P_at(7)):.5f}   P(0,7) SOFR = {float(usd.P_at(7)):.5f}")

    # Sorties CSV (grille mensuelle jusqu'a 10Y)
    tg = np.round(np.arange(0, 10 + 1e-9, 1 / 12), 6)
    tg_pos = np.where(tg == 0, 1e-6, tg)
    for name, c in (("eur", eur), ("eur6m", e6m), ("usd", usd)):
        pd.DataFrame({
            "t": tg,
            "P_loglin": c.P_at(tg_pos),
            "zero_cc": c.zero(tg_pos),
            "fwd_inst_loglin": c.fwd_pc(tg_pos),
            "fwd_inst_lisse": c.fwd(tg_pos),
            "P_lisse": c.P_smooth(tg_pos),
        }).to_csv(OUT / f"curve_{name}.csv", index=False)
    pd.DataFrame({"t": ti, "P_eur_estr": eur.P_at(ti), "P_usd_sofr": usd.P_at(ti)}).to_csv(
        OUT / "discount_coupon_dates.csv", index=False)

    # Graphiques
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(1, 3, figsize=(16, 4.5))
    ax[0].plot(tg[1:], 100 * eur.zero(tg[1:]), label="€STR zero cc")
    ax[0].plot(tg[1:], 100 * e6m.zero(tg[1:]), label="Euribor 6M zero cc")
    ax[0].plot(tg[1:], 100 * usd.zero(tg[1:]), label="SOFR zero cc")
    oe = [(yf(VAL_DATE, i["end"]), i["rate"]) for i in ie["ois"] if yf(VAL_DATE, i["end"]) <= 10.1]
    ou = [(yf(VAL_DATE, i["end"]), i["rate"]) for i in iu["ois"] if yf(VAL_DATE, i["end"]) <= 10.1]
    ax[0].plot(*zip(*[(t, 100 * r) for t, r in oe]), "o", ms=4, label="OIS €STR (cotes)")
    ax[0].plot(list(ie["swaps"]), [100 * v for v in ie["swaps"].values()], "^", ms=4, label="swaps Euribor 6M")
    ax[0].plot(*zip(*[(t, 100 * r) for t, r in ou]), "s", ms=4, label="OIS SOFR (cotes)")
    ax[0].set_title("Taux zero-coupon (%)"); ax[0].set_xlabel("maturite (ans)"); ax[0].legend(fontsize=7)
    for c, lab in ((eur, "€STR"), (e6m, "Euribor 6M"), (usd, "SOFR")):
        ax[1].plot(tg[1:], 100 * c.fwd(tg[1:]), label=f"{lab} f(0,t) lisse")
    ax[1].set_title("Forward instantane (%)"); ax[1].set_xlabel("maturite (ans)"); ax[1].legend(fontsize=8)
    ax[2].plot(tg, eur.P_at(tg_pos), label="P €STR"); ax[2].plot(tg, usd.P_at(tg_pos), label="P SOFR")
    ax[2].set_title("Facteurs d'actualisation P(0,t)"); ax[2].set_xlabel("maturite (ans)"); ax[2].legend()
    for a in ax: a.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(OUT / "courbes.png", dpi=130)
    print("\nFichiers ecrits dans", OUT)


if __name__ == "__main__":
    main()
