"""
Telechargement des courbes OIS (sources gratuites, sans Bloomberg).

- EUR €STR OIS 1Y..30Y : CheckMySwap (API publique, taux construits sur les swaps
  declares a la DTCC / CFTC).            -> data/estr_ois.csv
- USD SOFR OIS 1Y..30Y : CheckMySwap, meme source et meme format, pour l'historique
  du backtest.                           -> data/sofr_ois.csv
- USD SOFR OIS 1D..50Y : Eris Futures, courbe EOD complete (avec la partie courte
  1W..9M) a la date de valorisation.     -> data/sofr_ois_eris_<date>.csv
  (le serveur Eris ne garde qu'environ 2-3 mois d'historique)

Format des CSV CheckMySwap (long) : date, tenor, years, rate (%), method, trades
  method = "close"/"close-thin" : taux de cloture observe ; "derived"/"all-day" :
  interpole / moyenne du jour sur les transactions DTCC.

Usage : python download_ois.py            (depuis le dossier code/)
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

VAL_DATE = datetime(2026, 11, 13)
HIST_START = VAL_DATE - timedelta(days=183)   # 6 mois pour le backtest

CMS_URL = "https://www.checkmyswap.com/api/curves/{ccy}?raw=1&date={d}"
ERIS_URL = "https://files.erisfutures.com/ftp/Eris_{d}_EOD_ParCouponCurve_SOFR.csv"
UA = {"User-Agent": "Mozilla/5.0 (projet etudiant derives)"}
TENOR_YEARS = {"1Y": 1, "2Y": 2, "3Y": 3, "4Y": 4, "5Y": 5, "6Y": 6, "7Y": 7, "8Y": 8,
               "9Y": 9, "10Y": 10, "12Y": 12, "15Y": 15, "20Y": 20, "25Y": 25, "30Y": 30}


def _get(url: str, retries: int = 6) -> bytes | None:
    req = urllib.request.Request(url, headers=UA)
    for k in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            if e.code == 429 and k < retries - 1:   # rate limit : on attend et on reessaie
                wait = int(e.headers.get("Retry-After") or 15 * (k + 1)) + 5
                print(f"  429, attente {wait}s")
                time.sleep(wait)
                continue
            raise


def business_days(start: datetime, end: datetime):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def backtest_dates(start: datetime, end: datetime, step_days: int):
    """Date de valo d'abord, puis une date tous les `step_days` jours ouvres en remontant."""
    days = list(business_days(start, end))[::-1]
    return days[::step_days]


def fetch_cms(ccy: str, dates, out: Path) -> pd.DataFrame:
    """Ecriture incrementale : une date deja presente dans `out` n'est pas re-telechargee,
    donc le script peut etre relance apres un blocage (HTTP 429) sans rien perdre."""
    old = pd.read_csv(out) if out.exists() else pd.DataFrame()
    done = set(old["date"]) if not old.empty else set()
    for d in dates:
        key = d.strftime("%Y-%m-%d")
        if key in done:
            continue
        raw = _get(CMS_URL.format(ccy=ccy, d=key))
        time.sleep(3)                            # politesse envers l'API (rate limit strict)
        js = json.loads(raw) if raw else {}
        if "curve" not in js:                    # jour non stocke (ferie, etc.)
            print(f"{ccy} {key} : pas de donnees")
            continue
        rows = pd.DataFrame([{
            "date": js["date"], "tenor": p["tenor"],
            "years": TENOR_YEARS.get(p["tenor"], p["days"] / 365),
            "rate": p["rate"], "method": p.get("method"), "trades": p.get("trades"),
        } for p in js["curve"]])
        rows.to_csv(out, mode="a", header=not out.exists(), index=False)
        print(f"{ccy} {key} ok ({len(rows)} points)")
    return pd.read_csv(out) if out.exists() else pd.DataFrame()


def fetch_eris(date: datetime) -> Path | None:
    raw = _get(ERIS_URL.format(d=date.strftime("%Y%m%d")))
    if raw is None:
        print(f"Eris : pas de fichier pour {date:%d/%m/%Y} (historique serveur limite)")
        return None
    out = DATA / f"sofr_ois_eris_{date:%Y%m%d}.csv"
    out.write_bytes(raw)
    print("Eris ->", out.name)
    return out


def main(step_days: int = 5):
    """step_days = 5 : une courbe par semaine sur 6 mois (~27 dates par devise).
    Mettre 1 pour l'historique quotidien (beaucoup plus long a cause du rate limit)."""
    DATA.mkdir(exist_ok=True)
    if not (DATA / f"sofr_ois_eris_{VAL_DATE:%Y%m%d}.csv").exists():
        fetch_eris(VAL_DATE)
    dates = backtest_dates(HIST_START, VAL_DATE, step_days)
    for ccy, name in (("EUR", "estr_ois.csv"), ("USD", "sofr_ois.csv")):
        df = fetch_cms(ccy, dates, DATA / name)
        if not df.empty:
            print(f"{ccy} -> {name} : {df['date'].nunique()} dates, "
                  f"{df['date'].min()} .. {df['date'].max()}")


if __name__ == "__main__":
    main()
