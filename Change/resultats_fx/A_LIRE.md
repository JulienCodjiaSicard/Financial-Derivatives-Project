# Composante EUR/USD de la note ECE Bank : résultats

Document généré le 19/11/2026 par `python scripts/run_all.py` (config `config/valo_2026-11-06.json`). Ne pas modifier à la main : il est réécrit à chaque exécution.

Périmètre : la seule condition de change du coupon (EUR/USD < K à chaque date). Les conditions de taux (swap EUR 10 ans < 5 %), de crédit et le rappel anticipé sont traités par les autres sous-groupes et par le moteur Monte Carlo commun.

## En bref

- **Barrière K = 1,1770**, plus haut fixing BCE du 06/05/2026 au 06/11/2026, atteint le 07/05/2026. Spot au 06/11/2026 : 1,1165, soit 5,42 % sous la barrière.
- **Valeur actualisée des 14 coupons FX : 21 678 709 EUR avec smile**, contre 23 004 857 EUR à vol ATM plate (coupon de 7,0 % de 50 000 000 EUR par date, hypothèse à confirmer ; avec 3,5 % par semestre, diviser par deux).
- Probabilité de paiement de la condition de change (mesure EUR, avec smile) : 84,4 % au premier coupon, 70,4 % à 1 an, 32,7 % à 7 ans. Le forward EUR/USD franchit K vers 4 ans, poussé par l'écart de taux SOFR − €STR (environ 139 bp à 1 an).
- Le smile retire 1 326 148 EUR (-5,8 %) à la valeur à vol plate. Injecter σ(K) dans N(−d1) ne corrige presque rien (+79 307 EUR) : seule la réplication tient compte de la pente du smile.
- **12 dates sur 14 reposent sur des vols extrapolées** (aucune vol cotée au-delà de 1 an). Paramètre de smile le plus influent : le RR, 818 567 EUR pour +0,5 point.

## Données et sources

| Donnée | Valeur | Source | Date |
|---|---|---|---|
| Fixings EUR/USD (barrière) | 133 fixings, K = 1,1770 | BCE, série EXR.D.USD.EUR.SP00.A | 06/05/2026 au 06/11/2026 |
| Spot | 1,1165 | Fixing BCE du 06/11/2026 (série EXR.D.USD.EUR.SP00.A, eurusd_bce_2026-05-06_2026-11-06.csv) | 06/11/2026 |
| Courbe EUR (€STR OIS), taux de swap en % | 1M 2,45, 3M 2,56, 6M 2,67, 1Y 2,83, 2Y 2,93, 3Y 2,96, 5Y 2,98, 7Y 3,02, 10Y 3,11 | BlueGamma, clôtures quotidiennes par maturité, relevé le 19/11/2026 | 06/11/2026 |
| Courbe USD (SOFR OIS), taux de swap en % | 1M 3,89, 3M 4,06, 1Y 4,25, 2Y 4,34, 3Y 4,38, 5Y 4,36, 7Y 4,39, 10Y 4,46 | BlueGamma, clôtures quotidiennes par maturité, relevé le 19/11/2026 | 06/11/2026 |
| Vols ATM / RR25 (points de vol) | 1W 5,67 / -0,48, 1M 6,24 / -0,52, 3M 6,13 / -0,59, 6M 6,61 / -0,78, 9M 6,69 / -0,80, 1Y 6,76 / -0,84 | Saxo, tableau ATM / RR25 fourni par un camarade | **inconnue** (voir questions ouvertes) |
| Historique long EUR/USD | 7131 fixings utilisés | Portail de données BCE (`bce_historique_EXR.D.USD.EUR.SP00.A_2026-11-19.csv`) | 04/01/1999 au 06/11/2026 |

Butterflies : aucun disponible. Vols au-delà de 1 an : aucune. Ces manques sont couverts par des hypothèses explicites (ci-dessous) ; le chargement échoue si une donnée manque sans hypothèse déclarée.

## Hypothèses

Déclarées dans la config (`"hypothese": true`, avec justification) :

- **coupon_par_periode** = `0.07` : Lecture '7 % par semestre' reprise du script initial. La lecture '7 % par an' (0,035 par semestre) n'est pas tranchée par le professeur.
- **date_depart_coupons** = `2026-11-06` : Date d'émission non communiquée : échéancier compté depuis la date de valorisation (premier coupon 6 mois plus tard).
- **bf25_manquant** = `0.2` : Aucun butterfly 25 delta disponible. Milieu de la fourchette 0,15 à 0,25 point de vol retenue dans le plan ; sensibilité au BF prévue à l'étape 6.
- **vols_au_dela_dernier_pilier** = `vol_constante` : Aucune vol cotée au-delà de 1 an (source Saxo). Extrapolation à vol constante prévue par le plan, à remplacer par des cotations (Bloomberg OVDV) si disponibles.

Choix de méthode :

- Courbes : bootstrap des swaps OIS au pair (jambe fixe annuelle ACT/360), DF interpolés en log-linéaire, taux de swap des échéances annuelles non cotées (4, 6, 8, 9 ans) interpolés linéairement.
- Smile : piliers σ_25P/C = ATM + BF ∓ RR/2 ; interpolation en strike par Vanna-Volga d'ordre 2 (Castagna-Mercurio), SABR β = 1,0 en comparaison ; interpolation en temps sur la variance totale à delta constant.
- Conventions EUR/USD : delta spot jusqu'à 1 an, delta forward au-delà, non ajusté de la prime ; ATM delta-neutre. Après 1 an, le delta forward tenu constant est celui du pilier 1Y (0,25 / DF_EUR(1Y) ≈ 0,257), pour éviter un saut du smile à 1 an.
- Coupon par réplication : AoN(K) = K · ∂P/∂K − P(K), P pricé à σ(K), dérivée centrée de pas relatif 1e-4 améliorée par extrapolation de Richardson.
- Sensibilités : chocs centrés ; vols inchangées à delta constant quand le spot bouge ; écart de taux choqué via les taux zéro USD.

## Les 14 coupons FX

| n° | Date | T | Forward | σ ATM | σ(K) | P plate | P smile | Valeur plate (EUR) | Valeur smile (EUR) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 06/05/2027 | 0,50 | 1,1248 | 6,61 % | 6,44 % | 82,96 % | 84,39 % | 2 865 096 | 2 914 502 |
| 2 | 06/11/2027 | 1,00 | 1,1321 | 6,76 % | 6,55 % | 70,58 % | 70,41 % | 2 401 473 | 2 395 576 |
| 3 | 06/05/2028 | 1,50 | 1,1399 | 6,76 %* | 6,60 %* | 63,53 % | 62,12 % | 2 129 079 | 2 082 045 |
| 4 | 06/11/2028 | 2,00 | 1,1478 | 6,76 %* | 6,65 %* | 58,51 % | 56,23 % | 1 931 297 | 1 856 091 |
| 5 | 06/05/2029 | 2,50 | 1,1558 | 6,76 %* | 6,69 %* | 54,64 % | 51,72 % | 1 776 590 | 1 681 792 |
| 6 | 06/11/2029 | 3,00 | 1,1641 | 6,76 %* | 6,74 %* | 51,43 % | 48,03 % | 1 646 967 | 1 538 103 |
| 7 | 06/05/2030 | 3,50 | 1,1716 | 6,76 %* | 6,78 %* | 48,92 % | 45,18 % | 1 543 641 | 1 425 581 |
| 8 | 06/11/2030 | 4,00 | 1,1794 | 6,76 %* | 6,81 %* | 46,72 % | 42,70 % | 1 451 892 | 1 327 158 |
| 9 | 06/05/2031 | 4,50 | 1,1868 | 6,76 %* | 6,85 %* | 44,86 % | 40,64 % | 1 373 338 | 1 244 190 |
| 10 | 06/11/2031 | 5,00 | 1,1943 | 6,76 %* | 6,88 %* | 43,16 % | 38,77 % | 1 301 403 | 1 169 218 |
| 11 | 06/05/2032 | 5,50 | 1,2022 | 6,76 %* | 6,91 %* | 41,57 % | 37,05 % | 1 234 130 | 1 099 976 |
| 12 | 06/11/2032 | 6,01 | 1,2102 | 6,76 %* | 6,94 %* | 40,09 % | 35,47 % | 1 171 888 | 1 036 715 |
| 13 | 06/05/2033 | 6,50 | 1,2181 | 6,76 %* | 6,97 %* | 38,76 % | 34,06 % | 1 115 553 | 980 189 |
| 14 | 06/11/2033 | 7,01 | 1,2262 | 6,76 %* | 7,00 %* | 37,51 % | 32,75 % | 1 062 512 | 927 573 |
| **Total** | | | | | | | | 23 004 857 | **21 678 709** |

\* vols extrapolées (vol constante au-delà du dernier pilier coté).

## Sensibilités de la valeur totale

Valeur de base : 21 678 709 EUR. Résultats en EUR, chocs centrés rapportés pour le choc +.

| Choc | Effet (EUR) | En % de la valeur |
|---|---|---|
| Spot EUR/USD +1 % (delta) | -1 671 076 | -7,7 % |
| Gamma (variation du delta pour +1 %) | -41 931 | -0,2 % |
| Vols ATM +1 point (vega) | -290 628 | -1,3 % |
| Vanna (1 % × 1 point) | 215 015 | 1,0 % |
| Volga (1 point) | 20 630 | 0,1 % |
| RR25 +0,5 point | 818 567 | 3,8 % |
| BF25 +0,1 point | 51 055 | 0,2 % |
| Écart de taux USD − EUR +10 bp | -498 261 | -2,3 % |
| Barrière K +0,01 | 1 420 912 | 6,6 % |
| Scénario : BF supposé = 0,15 point | -26 350 | -0,1 % |
| Scénario : BF supposé = 0,25 point | 24 706 | 0,1 % |

Détail par date : `sensibilites_fx.csv` (colonnes : delta_spot_+1pct_EUR, gamma_spot_1pct_EUR, vega_ATM_+1pt_EUR, vanna_1pct_1pt_EUR, volga_1pt_EUR, RR25_+0,5pt_EUR, BF25_+0,1pt_EUR, ecart_taux_USD-EUR_+10bp_EUR, barriere_K_+0,01_EUR).

## Paramètres pour le moteur Monte Carlo commun

`export_mc.json` livre au moteur une **barrière effective par date** (bloc `pour_pricer_note_ece`) :

- `fx_spot`, `fx_vol_tenors` (les 14 dates de coupon) et `fx_atm_vols` : vols ATM de marché à ces dates ;
- `fx_barrier` : barrière K_eff(T_i) qui, à la vol ATM, redonne la probabilité avec smile, de 1,1802 au premier coupon à 1,1502 à 7 ans. Elle est unique, car la probabilité croît strictement avec K ;
- `usd_ois` au format `Curve(tenors, zeros)` du moteur : maturités en années, taux zéro continus en décimal (interpolés linéairement par le moteur). Les dates de coupon sont incluses, donc le moteur retrouve exactement nos facteurs d'actualisation USD à ces dates. La courbe €STR utilisée ici est jointe au même format, pour comparaison avec `eur_ois`.

**Changement d'une ligne dans `pricer_note_ece.py`**, à demander au sous-groupe moteur : comparer à la barrière de chaque date au lieu d'un K unique, `ok_fx = sim["fx"] < np.asarray(terms.fx_barrier).reshape(-1, 1)`.

**Test de contrôle** (bloc `controle_moteur`) : faire tourner le moteur sur la seule condition de change ; il doit retrouver les probabilités avec smile à 1 point près (le moteur simule des taux EUR stochastiques, supposés déterministes ici ; un écart plus grand signalerait un problème de convention). Une imitation du moteur à partir du seul fichier d'export retrouve ces probabilités à 0,2 point près (test `test_moteur_simule_retrouve_les_probabilites_avec_smile`).

**Pourquoi pas une vol effective σ_eff.** L'équation N(−d1(σ)) = P_smile a deux racines dès que le forward dépasse K, et aucune n'est réaliste (à 4 ans : 17,8 % ou 0,6 %, pour une vol ATM de 6,8 %). Quand F est proche de K, P ≈ N(−σ√T/2) : descendre nettement sous 50 % exige une vol extrême. σ_eff n'est donc reporté qu'à titre d'information jusqu'à 2 ans (`sigma_eff_information`) et n'est pas livré au moteur.

## Vue de l'investisseur : analyse historique

Fréquence, sur les fixings BCE du 04/01/1999 au 06/11/2026, des hausses de l'EUR/USD restées sous l'écart spot-barrière actuel (+5,42 %), en fenêtres glissantes quotidiennes.

| Horizon | Historique 1999–2026 | Périodes indépendantes | Depuis 2016 | Risque-neutre (smile) |
|---|---|---|---|---|
| 6 mois | 76,3 % | 55 | 80,7 % | 84,4 % |
| 12 mois | 67,3 % | 27 | 71,1 % | 70,4 % |
| 24 mois | 66,3 % | 13 | 69,6 % | 56,2 % |
| 36 mois | 63,7 % | 9 | 79,1 % | 48,0 % |
| 60 mois | 62,2 % | 5 | 94,6 % | 38,8 % |
| 84 mois | 68,6 % | 3 | 98,0 % | 32,7 % |

Jusqu'à 1 an, historique et risque-neutre sont proches. Au-delà, l'historique reste entre 62 et 69 % (pas de tendance de l'EUR/USD sur la période) quand le risque-neutre baisse avec le forward. Ce n'est pas une erreur de prix : la probabilité risque-neutre intègre l'écart de taux et le smile. Aux longs horizons, très peu de périodes indépendantes : les fréquences sont peu fiables.

| Vol | Réalisée | Implicite ATM |
|---|---|---|
| 1M | 5,05 % | 6,24 % |
| 3M | 4,94 % | 6,13 % |
| 6M | 4,96 % | 6,61 % |
| 1Y | 5,52 % | 6,76 % |
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

Les fichiers numérotés `01_` à `04_` et `A_LIRE.txt` viennent du script initial `pricing_fx.py` (taux EUR et USD à 3 %, vol plate, 2 dates) et sont conservés pour mémoire.

## Limites

- **Vols au-delà de 1 an extrapolées** à vol constante (ATM, RR et BF du pilier 1Y) : 12 dates sur 14. **Le risk reversal long est la donnée qui manque le plus** (environ 1,64 M EUR par point de RR, contre 0,51 M EUR par point de butterfly) : à chercher en priorité (Bloomberg OVDV).
- **Butterflies supposés** (0,20 point) ; effet limité au total (voir scénarios), plus marqué sur le premier coupon.
- BF coté traité comme un smile strangle alors que le marché cote un market strangle (écart faible ici, |RR| < 1 point).
- Approximation Vanna-Volga de Castagna-Mercurio (SABR donne moins de 0,02 point d'écart en K).
- Taux USD et EUR déterministes ; corrélation taux/change non modélisée ici (traitée, le cas échéant, par le moteur commun).
- Courbe unique par devise (pas de base cross-currency) ; swaps supposés démarrer à la date de valorisation (pas de T+2).
- Date de cotation des vols (Saxo, tableau ATM / RR25 fourni par un camarade) inconnue ; leur spot de référence (1,0992) diffère du spot du 06/11/2026 (1,1165) et égale le fixing BCE du 19/11/2026 : les vols datent très probablement de ce jour, le spot ayant reculé de 1,5 % entre les deux dates. À garder tant qu'il n'y a pas de vols à la date de valorisation.
- K à confirmer : fixing BCE retenu ; un plus haut intrajournalier donnerait un K plus élevé (chaque +0,01 sur K vaut 1 420 912 EUR).
- Vol effective σ_eff mal posée pour ce produit au-delà d'environ 2 ans (deux racines, aucune réaliste) : non livrée au moteur, remplacée par la barrière effective.
- Analyse historique en fenêtres très chevauchantes : peu de périodes indépendantes au-delà de 3 ans.

## Questions ouvertes

- Coupon : 7 % par an (3,5 % par semestre) ou 7 % par semestre ? (valeurs calculées avec 7,0 % par date)
- K : fixing BCE ou plus haut intrajournalier ?
- Date de valorisation commune à tout le groupe (ici le 06/11/2026, fin de la fenêtre de la barrière).
- Source des vols au-delà de 1 an, en priorité les risk reversals, puis les butterflies (Bloomberg OVDV si l'école y donne accès) ; vols à la date de valorisation plutôt qu'au 19/11.
- Sous-groupe moteur : accepter une barrière par date (changement d'une ligne ci-dessus), faire tourner le test de contrôle, et vérifier que `eur_ois` du moteur correspond à la courbe €STR utilisée ici.

> Avertissement au chargement : Date de cotation des vols (Saxo, tableau ATM / RR25 fourni par un camarade) inconnue : à confirmer.
> Avertissement au chargement : Spot de référence des vols (1.0992) différent du spot de valorisation (1.1165), écart de -1.55 %.
