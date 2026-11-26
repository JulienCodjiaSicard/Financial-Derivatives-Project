# Pricing de la note : Monte-Carlo + Longstaff-Schwartz (parties 2 et 3)

Note callable à capital protégé, 50 M€, 7 ans, coupon semestriel C (premier coupon à 6 mois) payé si
EUR/USD < barrière et si les banques EUR n'ont pas fait défaut. La banque peut rembourser au pair de 2 à 6,5 ans.

| Fichier | Rôle |
|---|---|
| [donnees.py](donnees.py) | paramètres du produit et lecture des livrables des sous-groupes (rien d'autre) |
| [Longstaff.py](Longstaff.py) | simulation, Longstaff-Schwartz, coupon C, sensibilités, contrôles, graphiques |
| [Backtest.py](Backtest.py) | validation du modèle sur les 6 derniers mois (partie 4) |
| [test_pricing.py](test_pricing.py) | tests (`python -m pytest`) |
| `sorties/` | résultats régénérés à chaque lancement |

```
pip install -r requirements.txt   # seulement si numpy, pandas, matplotlib ou pytest manquent
python3 Longstaff.py         # pricing et sensibilités, environ 25 s
python3 Backtest.py          # backtest sur 6 mois, environ 1 min
python3 -m pytest            # tests
```

## Données : lues dans les dossiers des autres sous-groupes, jamais modifiées

| Donnée | Valeur | Fichier |
|---|---|---|
| Date de valorisation | 13/11/2026 | commune aux sous-groupes |
| Courbes €STR (actualisation, Hull-White) et SOFR | OIS, grille mensuelle | `Taux…/output/diffusion/courbes_20261113.csv` |
| Hull-White a, σ_r | 2,68 %, 93 bp (swaptions co-terminales 7Y) | `Taux…/output/diffusion/hw_params.json` |
| Spot EUR/USD et barrière x | 1,1052 et 1,1770 (plus haut 6 mois, fixings BCE) | `Change/data/eurusd_bce_2026-05-06_2026-11-13.csv` |
| Vols ATM, barrières effectives (smile), ρ | 6,6 à 6,8 % ; 1,1813 à 1,1498 ; ρ = 0,32 | `Change/resultats_fx/export_mc.json` |
| Survie du panier de banques EUR | 98,9 % à 6 mois, 86,2 % à 7 ans | `Credit/outputs/systemic/coupon_semesters.csv` (scénario `tail_1x`) |
| Souverain AAA zone euro 7 ans | 3,25 % | `Credit/data/proxies/ecb_aaa.csv` (BCE) |

**Smile.** Le coupon est un digital : une vol ATM unique le biaise. Le groupe Change livre, pour chaque date,
une barrière effective qui, avec la vol ATM de la date, redonne la probabilité avec smile (Vanna-Volga). Le moteur
compare EUR/USD à cette barrière date par date et simule la vol par terme (variance totale interpolée).

## Contrôles lancés à chaque exécution

1. Longstaff-Schwartz sur les 8 trajectoires du cours (0,1144 et 0,0564).
2. E[D(0,t)] = P(0,t) : le modèle reproduit la courbe €STR.
3. E[D·X] = X0·P_USD(0,t) : le change actualisé est martingale sous la mesure EUR.
4. Monte-Carlo sans call = formule fermée de la partie 2 (taux déterministes, ρ = 0).
5. Probabilités de la condition de change = celles du groupe Change à 1 point près.
6. Survie aux dates de coupon = celle du groupe Crédit.

## Résultats (13/11/2026, 2 × 100 000 scénarios antithétiques)

| | |
|---|---|
| Coupon annuel sans l'option de la banque (formule fermée) | 6,10 % |
| **Coupon annuel avec l'option (MC + LS), marge 1,5 %** | **6,72 %** > souverain 7 ans (3,25 %) |
| V0 = capital + coupons − option | 40,16 + 10,05 − 0,95 = 49,25 M€ |
| Valeur de l'option de la banque | 0,95 M€ (1,90 % du nominal) |
| Probabilité de remboursement anticipé | 49 % |
| Premier coupon (6 mois) | 88,7 % (change) × 98,9 % (crédit) = 87,8 % |

Le détail par date est dans `sorties/coupons.csv`, le résumé dans `sorties/resultats.json`.

## Règle d'exercice de la banque (Longstaff-Schwartz)

Régression de la valeur de continuation sur (r, ln X) : monômes de degré ≤ 3 et fonctions tanh(k ln X), car les
coupons sont des digitales sur le change. Régressions apprises sur un jeu de scénarios, prix calculé sur un second
jeu indépendant : la règle estimée est sous-optimale pour la banque, l'option est donc estimée par défaut. Choix de
la base par convergence (valeur de l'option, 50 000 scénarios) :

| Base | Option de la banque |
|---|---|
| degré 2 | 0,77 M€ |
| degré 3 | 0,86 M€ |
| degré 5 | 0,88 M€ |
| **degré 3 + tanh (retenue)** | **0,90 M€** |
| degré 4 + tanh | 0,90 M€ |

## Sensibilités (`sorties/sensibilites.csv`)

Chocs centrés, mêmes aléas et règle d'exercice gelée au point de base (au premier ordre, geler la règle optimale
ne change pas la sensibilité), au coupon C*.

| Facteur | Choc | Variation de la note |
|---|---|---|
| Spot EUR/USD | +1 % | −476 k€ |
| Barrière EUR/USD | +0,01 | +410 k€ |
| Vol EUR/USD | +1 point | −216 k€ |
| Corrélation taux / change | +0,10 | +77 k€ |
| Intensité de défaut des banques EUR | +10 bp | −14 k€ |
| Taux USD | +1 bp | −12 k€ |
| Taux EUR | +1 bp | −11 k€ |
| Vol des taux σ_r | +10 bp | +7 k€ |
| Retour à la moyenne a | +1 % | −1 k€ |

Le risque de la note est d'abord un risque de change (spot, barrière, vol). Quand ρ > 0, une baisse des taux EUR,
qui pousse la banque à rembourser, va avec une baisse du dollar, donc avec moins de coupons payés. L'option de la
banque en est réduite (1,20 M€ avec ρ = 0 contre 0,95 M€), ce qui explique que la note gagne à une hausse de ρ ou
de σ_r.

## Backtest sur les 6 derniers mois (partie 4, `sorties/backtest/`)

On suppose la note émise le 15/05/2026 (première date des courbes OIS hebdomadaires) : barrière = plus haut BCE
des 6 mois précédents (1,1974), coupon fixé par le modèle à l'émission (C0 = 7,45 %). Elle est revalorisée chaque
semaine jusqu'au 13/11/2026 (27 dates) avec le marché du jour :

- courbes €STR et SOFR du 13/11 décalées de la variation des taux de swap OIS observée à la date, maturité par
  maturité (`estr_ois.csv`, `sofr_ois.csv` du groupe Taux) ;
- σ_r proportionnel au niveau des vols de swaptions co-terminales 7Y du jour (`VolSwaption.xlsx`), a fixé ;
- EUR/USD : fixing BCE du jour ; survie : élevée à la puissance (niveau moyen des CDS du panier / niveau du
  20/05), dernière cotation reportée ;
- vols FX, smile et ρ du 13/11 : pas d'historique disponible.

**Valeur et P&L.** La note passe de 98,5 % à 105,2 % du nominal (+3,34 M€) : l'EUR/USD a baissé de 1,163 à 1,105,
loin de la barrière. Premier coupon (fixing le 15/11/2026) : probabilité 69,9 % à l'émission ; au 13/11 l'EUR/USD
(1,1052) est sous la barrière effective (1,2018), le coupon sera payé.

**Explication du P&L.** Chaque semaine, la variation de valeur est comparée à celle prédite par les sensibilités
de début de semaine (delta et gamma de change, taux EUR et USD, vol des taux, crédit, temps) :

| | |
|---|---|
| Part de la variance du P&L hebdomadaire expliquée | **99,1 %** |
| P&L hebdomadaire moyen (valeur absolue) | 275 k€ |
| Part non expliquée moyenne | 28 k€ |
| Cumul sur 6 mois : change / temps / taux USD / taux EUR / gamma / reste | +2,76 / +1,74 / −0,77 / −0,30 / −0,27 / +0,18 M€ |

Les sensibilités suffisent à couvrir la note : le résidu vient surtout des mouvements non parallèles des courbes.

**Hypothèses face au réalisé (15/05 – 13/11/2026)** (`hypotheses.csv`) :

| Hypothèse | Modèle | Réalisé |
|---|---|---|
| Vol EUR/USD | 6,6 % (implicite) | 5,0 % |
| Semaines hors de l'IC 95 % du change | 5 % | 0 % |
| Vol normale du swap EUR 2 / 5 / 7 ans | 83 / 79 / 77 bp | 65 / 65 / 60 bp |
| Corrélation taux EUR / X | 0,32 | 0,19 |
| Défaut dans le panier à 6 mois | 1,05 % | aucun |

Les vols implicites (change et swaptions) dépassent les vols réalisées : le modèle, calibré sur les prix de marché,
est prudent pour le vendeur des options (l'investisseur). La corrélation réalisée sur 6 mois (0,19) est plus faible que
celle estimée sur un an et tombe juste sous son intervalle de confiance (0,20 à 0,42) : ρ est l'hypothèse la moins
stable du modèle (sensibilité : +77 k€ pour +0,10).

## Hypothèses et limites

- Dates de coupon t_i = 0,5·i ans sur la grille mensuelle ; le groupe Change utilise les dates calendaires
  (13/05/2027…), écart sur les probabilités inférieur à 0,25 point.
- Dans le modèle complet (taux Hull-White corrélés au change), la probabilité de la condition de change dépasse
  celle du groupe Change, qui suppose des taux fixes, de 0 à 4 points selon la date (4 points à 7 ans). Avec des
  taux déterministes, l'écart retombe à 0,23 point (contrôle 5) : c'est l'effet des taux stochastiques et de ρ.
- Défaut supposé indépendant des taux et du change : l'indicatrice de survie est remplacée par S(t_i).
- Courbes de crédit du groupe Crédit calibrées sur des CDS du 20/05/2026, prolongées au-delà de 3 ans
  (scénario central) et appliquées depuis le 13/11/2026.
- Vols FX au-delà de 1 an extrapolées à vol constante par le groupe Change (pas de cotation disponible).
- Taux USD déterministes, Hull-White à un facteur, risque de crédit de l'émetteur ignoré (voir partie 2).
- Sensibilités au spot et à la vol FX : barrières effectives du groupe Change gardées fixes pendant le choc
  (le smile ne suit pas le spot).
- Backtest : pas d'historique des vols FX ni du smile (gardés au 13/11) ; spreads CDS rares après juillet
  (dernière cotation reportée) ; courbes historiques reconstruites par décalage des taux de swap.
- Pistes d'amélioration : G2++ pour les déformations de courbe, volatilité locale (Dupire) ou stochastique pour le
  change, intensité de défaut stochastique corrélée au change (CIR++), historique de vols FX (Bloomberg OVDV).
