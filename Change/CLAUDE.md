# Projet Dérivés Financiers ECE : sous-groupe EUR/USD

Ce fichier donne le contexte et le plan de travail du sous-groupe change. Lis-le en entier avant toute modification, puis avance étape par étape (section "Plan"). À la fin de chaque étape : lance les tests, résume ce qui a changé et attends ma validation avant l'étape suivante.

## Contexte

Projet de groupe (ECE Paris, Ing5 Finance) : structurer et pricer une note émise par ECE Bank pour ECE Capital Invest. Le groupe est découpé en sous-groupes (taux, change, crédit, moteur Monte Carlo global). Notre périmètre : **la composante EUR/USD**, avec Garman-Kohlhagen, la barrière et le smile de volatilité.

Pay-off fixé par le professeur :
- Nominal 50 M EUR, maturité 7 ans, coupons semestriels (14 dates : 0,5 ; 1,0 ; ... ; 7,0 ans).
- Coupon de 7 % versé à une date si, à cette date : swap EUR 10Y < 5 %, **EUR/USD < K**, aucune banque systémique européenne en défaut.
- K = maximum de l'EUR/USD observé du 06/05/2026 au 06/11/2026.
- Nominal remboursé à 7 ans s'il n'y a eu aucun défaut ; ECE Bank peut rappeler la note tous les 6 mois à partir de 2 ans.

Notre sous-groupe ne traite **que la condition de change** : probabilité et valeur des 14 coupons digitaux EUR/USD, sensibilités, et paramètres à livrer au moteur Monte Carlo commun.

## État actuel du dossier

- `pricing_fx.py` : script du camarade. Lit le CSV BCE, calcule K, price des digitales à 6 mois et 1 an à vol ATM plate, produit des CSV et des SVG dans `resultats_fx/`.
- `eurusd_bce_2026-05-06_2026-11-06.csv` : fixings BCE quotidiens (colonnes `date`, `eurusd`).
- `resultats_fx/02_cotations_Saxo.csv` : vols ATM et risk reversals 25Δ Saxo, de 1 semaine à 1 an (spot Saxo 1,0992, date de cotation à confirmer).
- `requirements.txt` : numpy, scipy, pandas, matplotlib.

Faits déjà vérifiés :
- **K = 1,1770**, fixing BCE du 07/05/2026.
- La formule du coupon digital est juste. Le coupon est payé en EUR, donc sous la mesure risque-neutre EUR : avec X = USD pour 1 EUR, P(X_T < K) = N(−d1), d1 = [ln(S/K) + (r_USD − r_EUR + σ²/2) T] / (σ √T). Un Monte Carlo donne 77,26 % à 1 an contre 77,24 % en formule fermée (S = 1,1165, σ = 6,76 %, taux à 3 %).

Défauts connus du script actuel :
1. Taux EUR et USD supposés égaux à 3 % : supprime la dérive du change. À 7 ans, la probabilité passe de 58 % (écart de taux nul) à 43 % (écart de 100 bp).
2. Pas de date de valorisation unique : spot BCE du 06/11 (1,1165, le CSV s'arrête là), cotations Saxo d'une autre date (spot 1,0992). `A_LIRE.txt` indique à tort "17 novembre".
3. Coupon de 7 % appliqué par semestre (3,5 M EUR par date) ; la lecture "7 % par an" (3,5 % par semestre) n'est pas encore tranchée par le prof.
4. Seulement 2 dates sur 14 ; pas de vols au-delà de 1 an.
5. Pas de smile : vol ATM plate, butterflies absents.

## Règles de travail

- **N'invente jamais une donnée de marché.** Toute valeur absente est une hypothèse explicite dans la config, marquée `"hypothese": true` avec sa justification, et fait l'objet d'un test de sensibilité.
- Tous les paramètres (date de valorisation, K, coupon, conventions, chemins) vivent dans un fichier de config, pas en dur dans le code.
- Code en Python 3, typé, commenté en français, fonctions pures et testables. Garde `pricing_fx.py` comme référence historique, ne le supprime pas.
- Ajoute un `.gitignore` qui exclut `.venv/`, `__pycache__/` et les gros fichiers générés.
- Tests avec `pytest` ; chaque étape ajoute ses tests et ils doivent tous passer.
- Demande avant de supprimer ou d'écraser un fichier qui n'a pas été créé dans cette session.
- Montants en EUR pour un nominal de 50 M EUR ; probabilités en %, vols en %, deltas exprimés pour +1 % de spot.

## Architecture cible

```
pricing_fx/
  CLAUDE.md
  config/valo_2026-11-06.json   # date de valo, K, coupon, conventions, hypothèses
  data/
    eurusd_bce_2026-05-06_2026-11-06.csv
    marche_2026-11-06.json      # spot, courbes EUR/USD, vols ATM, RR, BF par maturité
  fx/
    __init__.py
    config.py        # chargement et validation de la config et des données
    courbes.py       # facteurs d'actualisation EUR et USD, forward EUR/USD
    gk.py            # Garman-Kohlhagen : vanilles, digitales, deltas, inversion delta -> strike
    smile.py         # smile par maturité (Vanna-Volga, option SABR), interpolation en temps
    digitales.py     # 14 coupons : probabilité, valeur, avec et sans smile
    sensibilites.py  # grecques par bump-and-reprice
    vol_effective.py # barrière effective K_eff(T) (livrée) et sigma_eff(T) (information)
    historique.py    # analyse historique de la barrière
    export_mc.py     # paramètres livrés au moteur global
    rapport.py       # génération de resultats_fx/A_LIRE.md
  scripts/run_all.py # lance tout et remplit resultats_fx/
  tests/
  resultats_fx/
```

## Plan

### Étape 1 : socle et données
- Crée la structure ci-dessus, `config.py` et le `.gitignore`.
- Format de `data/marche_<date>.json` : spot ; points des courbes EUR (€STR) et USD (SOFR) en taux zéro ou en taux de swap ; ou à défaut points de terme EUR/USD ; pour chaque maturité (1W à 7Y) : ATM, RR25, BF25, avec un champ `source` et un champ `hypothese`.
- Remplis-le avec ce qui existe (fixings BCE, cotations Saxo jusqu'à 1 an). Laisse vides les données manquantes et fais échouer le chargement avec un message clair qui liste ce qui manque, sauf si une hypothèse explicite est fournie dans la config.
- Calcul de K depuis le CSV, avec la date du pic ; test : K = 1,1770 au 07/05/2026.
- Date de valorisation par défaut : 06/11/2026 (fin de la fenêtre de la barrière).

### Étape 2 : courbes et forward
- `courbes.py` : bootstrap simple des taux zéro EUR et USD (ou lecture directe de taux zéro), facteurs d'actualisation, forward F(T) = S · DF_EUR(T) / DF_USD(T).
- Si seuls des points de terme sont disponibles, en déduire l'écart de taux implicite.
- Tests : DF(0) = 1, forward cohérent avec la parité des taux d'intérêt, F croissant quand r_USD > r_EUR.

### Étape 3 : Garman-Kohlhagen et conventions EUR/USD
- `gk.py` : call et put vanilles, digitales cash-or-nothing (USD) et asset-or-nothing (paiement de 1 EUR), vega, deltas.
- Conventions EUR/USD (prime en USD, donc delta non ajusté de la prime) : delta spot jusqu'à 1 an inclus, delta forward au-delà ; ATM delta-neutre (DNS).
- Inversion delta -> strike pour les piliers 25Δ put, ATM, 25Δ call.
- Tests : parité call-put, la digitale "1 EUR si X_T < K" vaut DF_EUR · N(−d1) en EUR, vérification par Monte Carlo à 0,1 % près.

### Étape 4 : smile
- `smile.py` : pour chaque maturité, piliers σ_25P = ATM + BF − RR/2 et σ_25C = ATM + BF + RR/2 (BF traité comme smile strangle, approximation à signaler dans les limites).
- Interpolation en strike par l'approximation Vanna-Volga de Castagna-Mercurio (premier ordre, puis second ordre si stable). Option SABR calibré sur les trois piliers, pour comparaison.
- Interpolation en temps sur la variance totale σ²·T, à delta constant ; au-delà du dernier pilier coté, extrapolation à vol constante, marquée comme hypothèse.
- BF manquants : hypothèse paramétrable (0,15 à 0,25 point de vol), avec sensibilité.
- Tests : le smile repasse par les trois piliers, variance totale croissante en T (pas d'arbitrage calendaire), densité implicite positive autour de K (pas d'arbitrage papillon).

### Étape 5 : les 14 coupons digitaux
- `digitales.py` : pour chaque date T_i, probabilité P(X_T < K) sous la mesure EUR et valeur actualisée du coupon (paramètre `coupon_par_periode`).
- Trois versions côte à côte : vol ATM plate ; σ(K) injectée dans N(−d1) (à garder seulement pour montrer l'erreur) ; **avec smile par réplication**.
- Réplication : en USD, l'asset-or-nothing put vaut AoN(K) = K · ∂P/∂K − P(K), avec P le put vanille pricé à σ(K) et la dérivée prise numériquement (différences centrées, pas relatif ~1e-4). Probabilité EUR = AoN / (S · DF_EUR(T)).
- Tests : avec un smile plat, réplication = formule fermée ; avec un skew négatif (RR < 0) et K au-dessus du forward, la réplication est inférieure au résultat obtenu en injectant σ(K).
- Sortie : `resultats_fx/coupons_fx.csv` (date, T, forward, σ_ATM, σ(K), P plate, P smile, valeur actualisée en EUR) et un graphique de P en fonction de T.

### Étape 6 : sensibilités
- `sensibilites.py` : bump-and-reprice sur la valeur totale des 14 coupons FX et par date.
- Delta (+1 % spot), gamma, vega ATM (+1 point), vanna, volga, sensibilité au RR (+0,5 point), au BF (+0,1 point), à l'écart de taux (+10 bp), à K (+0,01).
- Sortie : `resultats_fx/sensibilites_fx.csv` en EUR.

### Étape 7 : paramètres pour le Monte Carlo commun (barrière effective)
- Décision (relecture du 19/11/2026) : **ne livrer au moteur que la barrière effective**. Pour chaque date T_i, K_eff(T_i) telle qu'à la vol ATM de marché N(−d1(K_eff)) redonne la probabilité avec smile de l'étape 5. Elle est unique (la probabilité croît strictement avec K) et garde la vol de marché.
- La vol effective σ_eff (N(−d1(σ_eff)) = P_smile) est mal posée pour ce produit : dès que le forward dépasse K, deux racines, aucune réaliste (17,8 % ou 0,6 % à 4 ans). `vol_effective.py` la calcule encore, mais elle n'est exportée qu'à titre d'information jusqu'à `export_mc.sigma_eff_maturite_max_annees` (2 ans) et n'est pas livrée au moteur.
- `export_mc.py` : écrit `resultats_fx/export_mc.json`. Bloc `pour_pricer_note_ece` : `fx_spot`, `fx_vol_tenors` (dates de coupon), `fx_atm_vols` (vols ATM de marché), `fx_barrier` (une valeur par date), `usd_ois` au format `Curve(tenors, zeros)` du moteur (maturités en années, taux zéro continus en décimal, interpolés linéairement ; dates de coupon incluses). Courbe €STR jointe au même format pour comparaison avec `eur_ois`.
- Le moteur compare aujourd'hui à un K unique : changement d'une ligne à demander au sous-groupe moteur, `ok_fx = sim["fx"] < np.asarray(terms.fx_barrier).reshape(-1, 1)`.
- Tests : K_eff redonne la probabilité avec smile ; une imitation du moteur à partir du seul JSON (zéros interpolés linéairement, variance totale interpolée, lognormal sous la mesure EUR) la retrouve à 0,2 point près. Test à faire tourner dans le vrai moteur (bloc `controle_moteur`) : écart toléré 1 point (taux EUR stochastiques dans le moteur).

### Étape 8 : analyse historique de la barrière
- `historique.py` : sur un historique long des fixings BCE (à télécharger depuis le portail de données BCE, série EXR.D.USD.EUR.SP00.A, et à placer dans `data/`), fréquence empirique des hausses de l'EUR/USD supérieures à l'écart spot-barrière actuel (environ 5,4 %) sur 6 mois et 1 an, en fenêtres glissantes.
- Comparaison avec la probabilité risque-neutre de l'étape 5, et vol historique comparée à la vol implicite.
- Sortie : tableau et graphique pour la partie "vue de l'investisseur" et le backtest.

### Étape 9 : livrables
- `scripts/run_all.py` régénère tout `resultats_fx/` en une commande.
- `resultats_fx/A_LIRE.md` : sources et dates des données, liste des hypothèses, résultats principaux, limites (BF supposé ou non, vols au-delà de 1 an extrapolées, taux USD déterministes, corrélation taux/change non modélisée ici, approximation Vanna-Volga, K à confirmer : fixing BCE ou plus haut intrajournalier).

## Questions ouvertes (ne pas trancher seul, me les signaler)
- Coupon : 7 % par an (3,5 % par semestre) ou 7 % par semestre ?
- K : fixing BCE ou plus haut intrajournalier ?
- Date de valorisation commune à tout le groupe.
- Source des vols au-delà de 1 an et des butterflies (Bloomberg OVDV si l'école y donne accès).
