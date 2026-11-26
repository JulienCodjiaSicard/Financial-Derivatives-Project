# Donnees de calibration pour la diffusion (Monte Carlo)

Date de valorisation : **13/11/2026** (spot 17/11/2026). Tous les temps `t` sont en annees (act/365)
depuis la date de valorisation. Fichiers regeneres par `code/export_diffusion.py`.

## Fichiers

| Fichier | Contenu |
|---|---|
| `courbes_20261113.csv` | grille mensuelle 0..10 ans : `P_estr`, `f_estr`, `phi_hw`, `f_plus_phi`, `P_sofr`, `f_sofr` (+ versions log-lineaires et courbe de projection Euribor 6M) |
| `coupons_20261113.csv` | P(0,t_i) EUR et USD aux 14 dates de coupon t_i = 0,5 ... 7 et ratio pour le forward FX |
| `hw_params.json` | parametres Hull-White calibres + detail des 5 swaptions |
| `donnees_marche_20261113.csv` | tous les taux de marche utilises, avec leur source |

## Parametres Hull-White 1F (taux court €STR)

- a (retour a la moyenne) = **2.6764%** (`0.0267637`)
- sigma_r = **93.18 bp/an** (`0.00931847`)
- calibres sur les swaptions EUR co-terminales 7Y (2Yx5Y ... 6Yx1Y), vols normales ATM,
  prix marche (Bachelier) vs prix modele (Jamshidian), RMSE = 2.28 bp de nominal.

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
  coupon : EUR 3.7e-04, USD 1.1e-04.
- Integration de r sur la grille mensuelle : prendre le trapeze ou r en milieu de pas, sinon
  biais de discretisation visible sur D(0,7).
- Controle rapide : P_estr(7) = 0.80311, P_sofr(7) = 0.72072.
- `f_sofr` = r_usd deterministe (simplification du sujet, section 4.2).

## Sources et conventions

- €STR : OIS 1Y-30Y CheckMySwap (transactions DTCC), fixing BCE overnight, et un point 6M
  **synthetique** = fixing Euribor 6M - base (swap Euribor 1Y - OIS €STR 1Y). Probablement
  ~5-7 pb trop bas (la base a 6M est plus faible qu'a 1Y).
- Euribor 6M (projection uniquement) : depot 6M (fixing EMMI) + swaps 1Y-10Y, actualises €STR.
- SOFR : courbe OIS Eris (1W-50Y) + fixing NY Fed overnight.
- Accruals act/360, jours feries ignores, decalage de paiement OIS ignore.
