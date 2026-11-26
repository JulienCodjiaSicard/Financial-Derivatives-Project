"""
Export des donnees calibrees pour le groupe "diffusion" (Monte Carlo).

Ecrit dans output/diffusion/ :
  - courbes_<date>.csv        : grille mensuelle 0..10 ans (P, f(0,t), phi(t) HW)
  - coupons_<date>.csv        : valeurs aux 14 dates de coupon du produit
  - donnees_marche_<date>.csv : tous les instruments utilises (tracabilite)
  - hw_params.json            : copie de la calibration Hull-White
  - README.md                 : mode d'emploi

Prerequis : avoir lance calibration_hw.py (pour output/hw_params.json).
Usage : python export_diffusion.py   (depuis le dossier code/)
"""
from __future__ import annotations

import json
import shutil

import numpy as np
import pandas as pd

import curves as cv

OUT = cv.OUT / "diffusion"


def phi_hw(t, a, sigma):
    """Terme deterministe du sujet : sigma^2 / (2 a^2) * (1 - e^{-a t})^2."""
    return sigma**2 / (2 * a**2) * (1 - np.exp(-a * t)) ** 2


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    d = cv.VAL_DATE
    hw = json.loads((cv.OUT / "hw_params.json").read_text(encoding="utf-8"))
    if hw["date"] != f"{d:%Y-%m-%d}":
        raise ValueError("hw_params.json n'est pas a la date de valorisation : relancer calibration_hw.py")
    a, sigma = hw["a"], hw["sigma_r"]

    eur, e6m, ie = cv.build_eur(d)
    usd, iu = cv.build_usd(d)

    # 1) Courbes sur la grille mensuelle (celle du Monte Carlo du sujet), etendue a 10 ans
    t = np.round(np.arange(0, 10 + 1e-9, 1 / 12), 6)
    tp = np.where(t == 0, 1e-6, t)
    f_eur = eur.fwd(tp)
    courbes = pd.DataFrame({
        "t": t,
        "P_estr": eur.P_smooth(tp),
        "f_estr": f_eur,
        "phi_hw": phi_hw(t, a, sigma),
        "f_plus_phi": f_eur + phi_hw(t, a, sigma),     # = E^Q[r(t)] hors y ; r(t) = f_plus_phi + y(t)
        "P_sofr": usd.P_smooth(tp),
        "f_sofr": usd.fwd(tp),
        "P_estr_loglin": eur.P_at(tp),
        "P_sofr_loglin": usd.P_at(tp),
        "P_euribor6m_proj": e6m.P_at(tp),
    })
    courbes.loc[0, ["P_estr", "P_sofr", "P_estr_loglin", "P_sofr_loglin", "P_euribor6m_proj"]] = 1.0
    courbes.to_csv(OUT / f"courbes_{d:%Y%m%d}.csv", index=False, float_format="%.10g")

    # 2) Dates de coupon du produit
    ti = np.arange(0.5, 7.0 + 1e-9, 0.5)
    coupons = pd.DataFrame({
        "t": ti,
        "P_estr": eur.P_smooth(ti), "P_sofr": usd.P_smooth(ti),
        "P_estr_loglin": eur.P_at(ti), "P_sofr_loglin": usd.P_at(ti),
        "ratio_P_estr_sur_P_sofr": eur.P_smooth(ti) / usd.P_smooth(ti),   # F_i = FX(0) * ce ratio
    })
    coupons.to_csv(OUT / f"coupons_{d:%Y%m%d}.csv", index=False, float_format="%.10g")

    # 3) Donnees de marche utilisees
    rows = [dict(devise="EUR", instrument="ON", tenor="ON", taux=ie["on"],
                 source=f"BCE €STR (fixing {ie['on_date']:%d/%m/%Y})")]
    for i in ie["ois"]:
        syn = i.get("synthetic", False)
        rows.append(dict(devise="EUR", instrument="OIS €STR", tenor=i["name"], taux=i["rate"],
                         source="synthetique : Euribor 6M - base 1Y" if syn else "CheckMySwap (DTCC)"))
    if ie["depo6m"] is not None:
        rows.append(dict(devise="EUR", instrument="Depot Euribor 6M", tenor="6M", taux=ie["depo6m"],
                         source="euribor-rates.eu (EMMI)"))
    for n, s in sorted(ie["swaps"].items()):
        rows.append(dict(devise="EUR", instrument="Swap Euribor 6M", tenor=f"{n}Y", taux=s, source="data/EURAB6E*.csv"))
    rows.append(dict(devise="USD", instrument="ON", tenor="ON", taux=iu["on"],
                     source=f"NY Fed SOFR (fixing {iu['on_date']:%d/%m/%Y})"))
    for i in iu["ois"]:
        rows.append(dict(devise="USD", instrument="OIS SOFR", tenor=i["name"], taux=i["rate"], source=iu["source"]))
    pd.DataFrame(rows).to_csv(OUT / f"donnees_marche_{d:%Y%m%d}.csv", index=False, float_format="%.6g")

    shutil.copy(cv.OUT / "hw_params.json", OUT / "hw_params.json")

    # 4) Controle : ecart entre courbe lisse (a utiliser en MC) et courbe log-lineaire (bootstrap)
    ecart = np.max(np.abs(coupons["P_estr"] - coupons["P_estr_loglin"]))
    ecart_u = np.max(np.abs(coupons["P_sofr"] - coupons["P_sofr_loglin"]))

    (OUT / "README.md").write_text(README.format(
        date=f"{d:%d/%m/%Y}", d=f"{d:%Y%m%d}", spot=f"{cv.spot_date(d):%d/%m/%Y}",
        a=a, a_pct=f"{a:.4%}", sigma=sigma, sigma_bp=f"{sigma * 1e4:.2f}", rmse=f"{hw['rmse_price_bp']:.2f}",
        P7e=f"{float(eur.P_smooth(7)):.5f}", P7u=f"{float(usd.P_smooth(7)):.5f}",
        ecart=f"{ecart:.1e}", ecart_u=f"{ecart_u:.1e}", src_usd=iu["source"],
    ), encoding="utf-8")
    print("Export ecrit dans", OUT)
    for f in sorted(OUT.iterdir()):
        print("  ", f.name)
    print(f"Ecart max P lisse / P log-lineaire aux dates de coupon : EUR {ecart:.1e}, USD {ecart_u:.1e}")


README = """# Donnees de calibration pour la diffusion (Monte Carlo)

Date de valorisation : **{date}** (spot {spot}). Tous les temps `t` sont en annees (act/365)
depuis la date de valorisation. Fichiers regeneres par `code/export_diffusion.py`.

## Fichiers

| Fichier | Contenu |
|---|---|
| `courbes_{d}.csv` | grille mensuelle 0..10 ans : `P_estr`, `f_estr`, `phi_hw`, `f_plus_phi`, `P_sofr`, `f_sofr` (+ versions log-lineaires et courbe de projection Euribor 6M) |
| `coupons_{d}.csv` | P(0,t_i) EUR et USD aux 14 dates de coupon t_i = 0,5 ... 7 et ratio pour le forward FX |
| `hw_params.json` | parametres Hull-White calibres + detail des 5 swaptions |
| `donnees_marche_{d}.csv` | tous les taux de marche utilises, avec leur source |

## Parametres Hull-White 1F (taux court €STR)

- a (retour a la moyenne) = **{a_pct}** (`{a:.6g}`)
- sigma_r = **{sigma_bp} bp/an** (`{sigma:.6g}`)
- calibres sur les swaptions EUR co-terminales 7Y (2Yx5Y ... 6Yx1Y), vols normales ATM,
  prix marche (Bachelier) vs prix modele (Jamshidian), RMSE = {rmse} bp de nominal.

## Comment s'en servir (notations du sujet, section 4)

Taux EUR :

    r(t) = f_estr(t) + phi_hw(t) + y(t) = f_plus_phi(t) + y(t)
    y(t+D) = y(t) e^(-a D) + sigma_r * sqrt((1 - e^(-2 a D)) / (2a)) * Z_r,   y(0) = 0
    D(0,t) ~ exp(- sum r D)

Taux USD (deterministe) : `r_usd(t) = f_sofr(t)`.

Change (X = 1/FX, EUR pour 1 USD) :

    ln X(t+D) = ln X(t) + (r(t) - f_sofr(t) - sigma_FX^2 / 2) D + sigma_FX sqrt(D) Z_X

Zero-coupon dans le modele (Longstaff-Schwartz) : utiliser `P_estr` pour P^M(0,.) et
`f_estr` pour f^M(0,.).

## Points d'attention

- **Utiliser les colonnes lisses** (`P_estr`, `f_estr`, `P_sofr`, `f_sofr`) : elles sont coherentes
  entre elles (P = exp(-integrale de f)), donc le MC sans call doit retrouver `P_estr` aux erreurs
  de Monte Carlo pres. Les colonnes `_loglin` sont celles du bootstrap ; ecart max aux dates de
  coupon : EUR {ecart}, USD {ecart_u}.
- Integration de r sur la grille mensuelle : prendre le trapeze ou r en milieu de pas, sinon
  biais de discretisation visible sur D(0,7).
- Controle rapide : P_estr(7) = {P7e}, P_sofr(7) = {P7u}.
- `f_sofr` = r_usd deterministe (simplification du sujet, section 4.2).

## Sources et conventions

- €STR : OIS 1Y-30Y CheckMySwap (transactions DTCC), fixing BCE overnight, et un point 6M
  **synthetique** = fixing Euribor 6M - base (swap Euribor 1Y - OIS €STR 1Y). Probablement
  ~5-7 pb trop bas (la base a 6M est plus faible qu'a 1Y).
- Euribor 6M (projection uniquement) : depot 6M (fixing EMMI) + swaps 1Y-10Y, actualises €STR.
- SOFR : courbe OIS {src_usd} (1W-50Y) + fixing NY Fed overnight.
- Accruals act/360, jours feries ignores, decalage de paiement OIS ignore.
"""


if __name__ == "__main__":
    main()
