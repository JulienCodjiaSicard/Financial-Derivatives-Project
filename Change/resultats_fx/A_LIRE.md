# Composante EUR/USD de la note ECE Bank : résultats

Document généré le 25/11/2026 par `python scripts/run_all.py` (config `config/valo_2026-11-13.json`). Ne pas modifier à la main : il est réécrit à chaque exécution.

Périmètre : la seule condition de change du coupon (EUR/USD < K à chaque date). Les conditions de taux (swap EUR 10 ans < 5 %), de crédit et le rappel anticipé sont traités par les autres sous-groupes et par le moteur Monte Carlo commun.

## En bref

- **Barrière K = 1,1770**, plus haut fixing BCE du 06/05/2026 au 06/11/2026, atteint le 07/05/2026. Spot au 13/11/2026 : 1,1052, soit 6,50 % sous la barrière.
- **Valeur actualisée des 14 coupons FX : 22 184 587 EUR avec smile**, contre 23 391 385 EUR à vol ATM plate (coupon de 7,0 % de 50 000 000 EUR par date, hypothèse à confirmer ; avec 3,5 % par semestre, diviser par deux).
- Probabilité de paiement de la condition de change (mesure EUR, avec smile) : 90,2 % au premier coupon, 76,3 % à 1 an, 30,2 % à 7 ans. Le forward EUR/USD franchit K vers 4 ans, poussé par l'écart de taux Treasury − Euribor 6M (courbes du sous-groupe taux) (environ 137 bp à 1 an).
- Le smile retire 1 206 797 EUR (-5,2 %) à la valeur à vol plate. Injecter σ(K) dans N(−d1) ne corrige presque rien (+96 533 EUR) : seule la réplication tient compte de la pente du smile.
- **12 dates sur 14 reposent sur des vols extrapolées** (aucune vol cotée au-delà de 1 an). Paramètre de smile le plus influent : le RR, 748 141 EUR pour +0,5 point.

## Données et sources

| Donnée | Valeur | Source | Date |
|---|---|---|---|
| Fixings EUR/USD (barrière) | 133 fixings, K = 1,1770 | BCE, série EXR.D.USD.EUR.SP00.A | 06/05/2026 au 06/11/2026 |
| Spot | 1,1052 | Fixing BCE du 13/11/2026 (série EXR.D.USD.EUR.SP00.A, data/eurusd_bce_2026-05-06_2026-11-13.csv) | 13/11/2026 |
| Courbe EUR (Euribor 6M (swaps, courbe unique) - sous-groupe taux), taux zéro continus | 1 an 3,23 %, 2 ans 3,35 %, 3 ans 3,37 %, 5 ans 3,35 %, 7 ans 3,35 % | Sous-groupe taux, output/curve_eur.csv, copie dans data/groupe_taux/ (swaps EUR Euribor 6M 1Y-10Y du 13/11/2026) | 13/11/2026 |
| Courbe USD (Treasury par yields (proxy SOFR) - sous-groupe taux), taux zéro continus | 1 an 4,60 %, 2 ans 4,83 %, 3 ans 4,87 %, 5 ans 4,93 %, 7 ans 5,00 % | Sous-groupe taux, output/curve_usd.csv, copie dans data/groupe_taux/ (Daily Treasury Par Yield Curve du 13/11/2026) | 13/11/2026 |
| Vols ATM / RR25 (points de vol) | 1W 5,67 / -0,48, 1M 6,24 / -0,52, 3M 6,13 / -0,59, 6M 6,61 / -0,78, 9M 6,69 / -0,80, 1Y 6,76 / -0,84 | Saxo, tableau ATM / RR25 fourni par un camarade | **inconnue** (voir questions ouvertes) |
| Historique long EUR/USD | 7136 fixings utilisés | Portail de données BCE (`bce_historique_EXR.D.USD.EUR.SP00.A_2026-11-19.csv`) | 04/01/1999 au 13/11/2026 |

Butterflies : aucun disponible. Vols au-delà de 1 an : aucune. Ces manques sont couverts par des hypothèses explicites (ci-dessous) ; le chargement échoue si une donnée manque sans hypothèse déclarée.

## Hypothèses

Déclarées dans la config (`"hypothese": true`, avec justification) :

- **coupon_par_periode** = `0.07` : Lecture '7 % par semestre' reprise du script initial. La lecture '7 % par an' (0,035 par semestre) n'est pas tranchée par le professeur.
- **date_depart_coupons** = `2026-11-13` : Date d'émission non communiquée : échéancier compté depuis la date de valorisation commune (13/11/2026), comme les dates t = 0,5 ... 7 ans du sous-groupe taux (output/discount_coupon_dates.csv).
- **bf25_manquant** = `0.2` : Aucun butterfly 25 delta disponible. Milieu de la fourchette 0,15 à 0,25 point de vol retenue dans le plan ; sensibilité au BF prévue à l'étape 6.
- **vols_au_dela_dernier_pilier** = `vol_constante` : Aucune vol cotée au-delà de 1 an (source Saxo). Extrapolation à vol constante prévue par le plan, à remplacer par des cotations (Bloomberg OVDV) si disponibles.

Choix de méthode :

- Courbes : facteurs d'actualisation du sous-groupe taux repris tels quels (EUR : swaps Euribor 6M en courbe unique ; USD : par yields Treasury, proxy SOFR), interpolés en log-linéaire comme dans leur code : on retrouve exactement leurs P_eur et P_usd aux dates t = 0,5 ... 7 ans. Forward F(T) = S · DF_EUR / DF_USD.
- Smile : piliers σ_25P/C = ATM + BF ∓ RR/2 ; interpolation en strike par Vanna-Volga d'ordre 2 (Castagna-Mercurio), SABR β = 1,0 en comparaison ; interpolation en temps sur la variance totale à delta constant.
- Conventions EUR/USD : delta spot jusqu'à 1 an, delta forward au-delà, non ajusté de la prime ; ATM delta-neutre. Après 1 an, le delta forward tenu constant est celui du pilier 1Y (0,25 / DF_EUR(1Y) ≈ 0,258), pour éviter un saut du smile à 1 an.
- Coupon par réplication : AoN(K) = K · ∂P/∂K − P(K), P pricé à σ(K), dérivée centrée de pas relatif 1e-4 améliorée par extrapolation de Richardson.
- Sensibilités : chocs centrés ; vols inchangées à delta constant quand le spot bouge ; écart de taux choqué via les taux zéro USD.

## Les 14 coupons FX

| n° | Date | T | Forward | σ ATM | σ(K) | P plate | P smile | Valeur plate (EUR) | Valeur smile (EUR) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 13/05/2027 | 0,50 | 1,1115 | 6,61 % | 6,53 % | 88,65 % | 90,16 % | 3 053 457 | 3 105 582 |
| 2 | 13/11/2027 | 1,00 | 1,1204 | 6,76 % | 6,54 % | 75,64 % | 76,29 % | 2 563 376 | 2 585 140 |
| 3 | 13/05/2028 | 1,50 | 1,1292 | 6,76 %* | 6,57 %* | 67,72 % | 67,08 % | 2 255 486 | 2 233 951 |
| 4 | 13/11/2028 | 2,00 | 1,1383 | 6,76 %* | 6,61 %* | 61,85 % | 60,15 % | 2 024 000 | 1 968 522 |
| 5 | 13/05/2029 | 2,50 | 1,1471 | 6,76 %* | 6,66 %* | 57,45 % | 54,98 % | 1 848 491 | 1 769 240 |
| 6 | 13/11/2029 | 3,00 | 1,1560 | 6,76 %* | 6,71 %* | 53,79 % | 50,73 % | 1 701 352 | 1 604 551 |
| 7 | 13/05/2030 | 3,50 | 1,1657 | 6,76 %* | 6,75 %* | 50,53 % | 46,99 % | 1 572 483 | 1 462 194 |
| 8 | 13/11/2030 | 4,00 | 1,1757 | 6,76 %* | 6,80 %* | 47,64 % | 43,71 % | 1 457 897 | 1 337 726 |
| 9 | 13/05/2031 | 4,50 | 1,1857 | 6,76 %* | 6,84 %* | 45,11 % | 40,89 % | 1 357 909 | 1 230 972 |
| 10 | 13/11/2031 | 5,00 | 1,1962 | 6,76 %* | 6,89 %* | 42,75 % | 38,31 % | 1 265 680 | 1 134 131 |
| 11 | 13/05/2032 | 5,50 | 1,2069 | 6,76 %* | 6,93 %* | 40,61 % | 36,00 % | 1 182 492 | 1 048 274 |
| 12 | 13/11/2032 | 6,01 | 1,2181 | 6,76 %* | 6,97 %* | 38,59 % | 33,86 % | 1 104 744 | 969 361 |
| 13 | 13/05/2033 | 6,50 | 1,2293 | 6,76 %* | 7,02 %* | 36,76 % | 31,96 % | 1 034 937 | 899 759 |
| 14 | 13/11/2033 | 7,01 | 1,2409 | 6,76 %* | 7,06 %* | 35,01 % | 30,18 % | 969 082 | 835 183 |
| **Total** | | | | | | | | 23 391 385 | **22 184 587** |

\* vols extrapolées (vol constante au-delà du dernier pilier coté).

## Sensibilités de la valeur totale

Valeur de base : 22 184 587 EUR. Résultats en EUR, chocs centrés rapportés pour le choc +.

| Choc | Effet (EUR) | En % de la valeur |
|---|---|---|
| Spot EUR/USD +1 % (delta) | -1 553 685 | -7,0 % |
| Gamma (variation du delta pour +1 %) | -49 671 | -0,2 % |
| Vols ATM +1 point (vega) | -350 879 | -1,6 % |
| Vanna (1 % × 1 point) | 176 855 | 0,8 % |
| Volga (1 point) | 20 323 | 0,1 % |
| RR25 +0,5 point | 748 141 | 3,4 % |
| BF25 +0,1 point | 49 895 | 0,2 % |
| Écart de taux USD − EUR +10 bp | -479 303 | -2,2 % |
| Barrière K +0,01 | 1 320 822 | 6,0 % |
| Scénario : BF supposé = 0,15 point | -25 776 | -0,1 % |
| Scénario : BF supposé = 0,25 point | 24 162 | 0,1 % |

Détail par date : `sensibilites_fx.csv` (colonnes : delta_spot_+1pct_EUR, gamma_spot_1pct_EUR, vega_ATM_+1pt_EUR, vanna_1pct_1pt_EUR, volga_1pt_EUR, RR25_+0,5pt_EUR, BF25_+0,1pt_EUR, ecart_taux_USD-EUR_+10bp_EUR, barriere_K_+0,01_EUR).

## Paramètres pour le moteur Monte Carlo commun

`export_mc.json` livre au moteur une **barrière effective par date** (bloc `pour_pricer_note_ece`) :

- `fx_spot`, `fx_vol_tenors` (les 14 dates de coupon) et `fx_atm_vols` : vols ATM de marché à ces dates ;
- `fx_barrier` : barrière K_eff(T_i) qui, à la vol ATM, redonne la probabilité avec smile, de 1,1815 au premier coupon à 1,1490 à 7 ans. Elle est unique, car la probabilité croît strictement avec K ;
- `usd_ois` au format `Curve(tenors, zeros)` du moteur : maturités en années, taux zéro continus en décimal (interpolés linéairement par le moteur). Les dates de coupon sont incluses, donc le moteur retrouve exactement nos facteurs d'actualisation USD à ces dates. La courbe EUR utilisée ici (Euribor 6M (swaps, courbe unique) - sous-groupe taux) est jointe au même format, pour comparaison avec `eur_ois`.

**Changement d'une ligne dans `pricer_note_ece.py`**, à demander au sous-groupe moteur : comparer à la barrière de chaque date au lieu d'un K unique, `ok_fx = sim["fx"] < np.asarray(terms.fx_barrier).reshape(-1, 1)`.

**Test de contrôle** (bloc `controle_moteur`) : faire tourner le moteur sur la seule condition de change ; il doit retrouver les probabilités avec smile à 1 point près (le moteur simule des taux EUR stochastiques, supposés déterministes ici ; un écart plus grand signalerait un problème de convention). Une imitation du moteur à partir du seul fichier d'export retrouve ces probabilités à 0,2 point près (test `test_moteur_simule_retrouve_les_probabilites_avec_smile`).

**Pourquoi pas une vol effective σ_eff.** L'équation N(−d1(σ)) = P_smile a deux racines dès que le forward dépasse K, et aucune n'est réaliste (à 4 ans : 20,1 % ou 1,6 %, pour une vol ATM de 6,8 %). Quand F est proche de K, P ≈ N(−σ√T/2) : descendre nettement sous 50 % exige une vol extrême. σ_eff n'est donc reporté qu'à titre d'information jusqu'à 2 ans (`sigma_eff_information`) et n'est pas livré au moteur.

## Vue de l'investisseur : analyse historique

Fréquence, sur les fixings BCE du 04/01/1999 au 13/11/2026, des hausses de l'EUR/USD restées sous l'écart spot-barrière actuel (+6,50 %), en fenêtres glissantes quotidiennes.

| Horizon | Historique 1999–2026 | Périodes indépendantes | Depuis 2016 | Risque-neutre (smile) |
|---|---|---|---|---|
| 6 mois | 81,7 % | 55 | 85,3 % | 90,2 % |
| 12 mois | 72,5 % | 27 | 76,2 % | 76,3 % |
| 24 mois | 71,3 % | 13 | 78,0 % | 60,2 % |
| 36 mois | 66,3 % | 9 | 82,6 % | 50,7 % |
| 60 mois | 64,8 % | 5 | 96,6 % | 38,3 % |
| 84 mois | 70,4 % | 3 | 99,9 % | 30,2 % |

Jusqu'à 1 an, historique et risque-neutre sont proches. Au-delà, l'historique reste entre 65 et 71 % (pas de tendance de l'EUR/USD sur la période) quand le risque-neutre baisse avec le forward. Ce n'est pas une erreur de prix : la probabilité risque-neutre intègre l'écart de taux et le smile. Aux longs horizons, très peu de périodes indépendantes : les fréquences sont peu fiables.

| Vol | Réalisée | Implicite ATM |
|---|---|---|
| 1M | 5,91 % | 6,24 % |
| 3M | 5,18 % | 6,13 % |
| 6M | 5,04 % | 6,61 % |
| 1Y | 5,56 % | 6,76 % |
| 1999–2026 | 9,21 % | |

La vol implicite dépasse la vol réalisée récente (prime de volatilité habituelle).

## Fichiers produits

- `coupons_fx.csv` : probabilités (plate, σ(K) injectée, smile) et valeurs des 14 coupons.
- `coupons_fx_probabilites.svg` : probabilité de paiement en fonction de la maturité.
- `sensibilites_fx.csv` : sensibilités par date et au total.
- `export_mc.json` : paramètres pour le moteur Monte Carlo commun (barrière effective par date, usd_ois) et test de contrôle.
- `historique_barriere_fx.csv / .svg` : analyse historique par horizon.
- `vol_historique_vs_implicite_fx.csv` : vol réalisée et vol implicite.
- `A_LIRE.md` : ce document.

## Limites

- **Vols au-delà de 1 an extrapolées** à vol constante (ATM, RR et BF du pilier 1Y) : 12 dates sur 14. **Le risk reversal long est la donnée qui manque le plus** (environ 1,50 M EUR par point de RR, contre 0,50 M EUR par point de butterfly) : à chercher en priorité (Bloomberg OVDV).
- **Butterflies supposés** (0,20 point) ; effet limité au total (voir scénarios), plus marqué sur le premier coupon.
- BF coté traité comme un smile strangle alors que le marché cote un market strangle (écart faible ici, |RR| < 1 point).
- Approximation Vanna-Volga de Castagna-Mercurio (SABR donne moins de 0,02 point d'écart en K).
- Taux USD et EUR déterministes ; corrélation taux/change non modélisée ici (traitée, le cas échéant, par le moteur commun).
- **Courbes sans OIS** (pas d'accès dans le groupe) : EUR Euribor 6M au lieu de l'€STR, USD Treasury au lieu du SOFR. Le forward de change en dépend directement (parité des taux) ; une couverture de marché se ferait sur les courbes OIS. Ordre de grandeur : -479 303 EUR par +10 bp d'écart de taux USD − EUR.
- Courbe unique par devise (pas de base cross-currency) ; swaps supposés démarrer à la date de valorisation (pas de T+2).
- Date de cotation des vols (Saxo, tableau ATM / RR25 fourni par un camarade) inconnue ; leur spot de référence (1,0992) diffère du spot du 13/11/2026 (1,1052) et égale le fixing BCE du 19/11/2026 : les vols datent très probablement de ce jour, le spot ayant reculé de 0,5 % entre les deux dates. À garder tant qu'il n'y a pas de vols à la date de valorisation.
- K à confirmer : fixing BCE retenu ; un plus haut intrajournalier donnerait un K plus élevé (chaque +0,01 sur K vaut 1 320 822 EUR).
- Vol effective σ_eff mal posée pour ce produit au-delà d'environ 2 ans (deux racines, aucune réaliste) : non livrée au moteur, remplacée par la barrière effective.
- Analyse historique en fenêtres très chevauchantes : peu de périodes indépendantes au-delà de 3 ans.

## Questions ouvertes

- Coupon : 7 % par an (3,5 % par semestre) ou 7 % par semestre ? (valeurs calculées avec 7,0 % par date)
- K : fixing BCE ou plus haut intrajournalier ?
- Date de valorisation : le 13/11/2026, alignée sur le sous-groupe taux ; à confirmer avec les sous-groupes crédit et moteur.
- Source des vols au-delà de 1 an, en priorité les risk reversals, puis les butterflies (Bloomberg OVDV si l'école y donne accès) ; vols à la date de valorisation plutôt qu'au 19/11.
- Sous-groupe moteur : accepter une barrière par date (changement d'une ligne ci-dessus), faire tourner le test de contrôle, et vérifier que `eur_ois` du moteur correspond à la courbe EUR utilisée ici (Euribor 6M (swaps, courbe unique) - sous-groupe taux).

> Avertissement au chargement : Date de cotation des vols (Saxo, tableau ATM / RR25 fourni par un camarade) inconnue : à confirmer.
> Avertissement au chargement : Spot de référence des vols (1.0992) différent du spot de valorisation (1.1052), écart de -0.54 %.
