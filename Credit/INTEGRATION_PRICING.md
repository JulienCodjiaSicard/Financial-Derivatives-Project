# Intégration du module Crédit avec Taux, FX et Pricing

Statut de la livraison Crédit autonome. **PENDING EXTERNAL TEAMS** pour les essais avec les vrais fichiers et trajectoires collectifs.

## Interface Taux

Le [gabarit d’exemple EUR](data/integration/eur_taux_exemple.csv) utilise des dates de piliers exactes et les facteurs de la reconstruction OIS du 20 mai. Il sert de fichier externe pour vérifier le branchement. La courbe définitive doit être fournie et validée par Taux.

| Champ | Contrat |
|---|---|
| `valuation_date` | ISO, identique à celle des quotes CDS et de la grille Pricing. |
| `pillar_date` ou `time_years` | Dates ISO ou années. `pillar_date` est prioritaire et exige `ACT/365F`. |
| `discount_factor` | EUR, sans dimension, strictement positif, `D(0)=1` ajouté si absent. Des taux négatifs peuvent donner des facteurs supérieurs à 1. |
| `currency`, `time_basis` | `EUR`, `ACT/365F` ou `idealized_years` pour les grilles abstraites de démonstration. |
| `data_kind`, `provider` | `observed` ou `synthetic`, et provenance. Le mode SNAPSHOT exige `observed`. Pour une courbe simulée de test, utiliser des quotes et une courbe DEMO cohérentes. |
| `curve_kind`, `source_url`, `available_at` | Nature de la courbe, provenance et disponibilité effective de cette version, si attestée. |

`load_discount_curves` retourne un dictionnaire par date de valorisation. Le modèle interpole `log(D)`. Le domaine de la courbe doit couvrir la dernière échéance calibrée. Pour le repricing joint, Pricing transmet les facteurs trajectoire par trajectoire sur sa grille commune et dans le même ordre de chemins.

## API Python et dimensions

Les temps sont mesurés depuis la date de valorisation. Intensités, probabilités et recoveries sont des fractions décimales. Les paramètres de spread annotés `_bps` sont en points de base. La calibration retourne des courbes dans l’ordre `tuple(calibration.curves)`. Conserver cet ordre dans tous les exports bancaires.

| Fonction | Arguments et résultat |
|---|---|
| `calibrate_credit(quotes, discount, *, mode="SNAPSHOT", basket=None, method="strict", extrapolate=False)` | Quotes et `DiscountCurve` à date identique. Résultat `Calibration`, avec `curves`, `repricing`, `discount`, `warnings` et `probabilities(grid)`. |
| `basket_survival(curves, t)` | Survie sous indépendance, produit des survies individuelles aux temps `t`. |
| `fit_stochastic(curves, grid, kind, params=None, *, n_calibration=16000, seed=7301, calibration_innovations=None)` | `kind` vaut `lognormal` ou `cirpp`. Utilise un échantillon indépendant des chemins de valorisation. Si fourni, `calibration_innovations` a la forme `(n_calibration//2, len(grid)-1)`, avant antithétiques. |
| `simulate_credit(curves, grid, n_paths, *, credit_innovations=None, model=None, seed=9011)` | Avec `model`, attend la colonne Crédit déjà corrélée, forme `(n_paths, len(grid)-1)`. Avec intensités déterministes, omettre `credit_innovations`. Retourne `CreditScenarios`. |
| `validate_correlation(correlation)` | Vérifie une matrice symétrique définie positive 3×3, diagonale unitaire. Ordre taux EUR, FX, Crédit. Retourne une copie de la matrice. |
| `credit_stress(quotes, discount, *, mode="SNAPSHOT", spread_bump_bps=0, recovery=None, entity=None, tenor_years=None, basket=None, method="strict", extrapolate=False, spread_overrides_bps=None)` | `Calibration` de scénario. Choc additif ou substitutions absolues indexées par `(entity_id, tenor_years)`. |
| `reprice_joint_cds(maturity, entity, recovery, scenarios, path_discounts, *, reference_bps, premium_interval=.25, accrue_on_default=True, comparison_discounts=None)` | `path_discounts` et comparaison facultative de forme `(n_paths, len(grid))`. Même grille et mêmes chemins. Rend un dictionnaire de spread, écart et erreur Monte Carlo. |

| Sortie de `CreditScenarios` | Forme et sens |
|---|---|
| `grid`, `entities` | `(dates,)`, et tuple ordonné des banques. La grille commence à zéro et croît strictement. |
| `default_times`, `first_default_times` | `(paths,banks)`, `(paths,)` en années. `inf` signifie vivant à l’horizon, donc temps censuré dans la simulation. |
| `basket_alive`, `bank_alive(entity)` | `(paths,dates)`, booléens. Vivant au temps `t` si et seulement si `tau > t`. |
| `factor` | `(paths,dates)` en stochastique. `None` en déterministe. |
| `conditional_mean_survival`, `conditional_survival_se` | `(banks,dates)`. Moyenne conditionnelle intégrée sur les seuils et erreur type d’évaluation. |

Pricing génère les chocs communs et applique la matrice de Cholesky **une seule fois** au tenseur normal indépendant. Crédit reçoit uniquement la colonne déjà corrélée. La vérification de la matrice n’applique aucun choc. Les seuils exponentiels idiosyncratiques utilisent une seed propre. Les mêmes innovations et seuils peuvent être conservés entre scénarios pour une comparaison appariée.

## Exemple exécutable depuis la racine du projet

L’exemple branche un fichier de facteurs externe, trois références subordonnées et un facteur lognormal de scénario. Il active le prolongement plat après le dernier CDS de 3,09 ans pour illustrer l’API à sept ans. Le modèle stochastique et les corrélations sont des hypothèses de scénario.

```python
import numpy as np
import pandas as pd
from credit_engine import (
    COUPON_TIMES, calibrate_credit, fit_stochastic, load_discount_curves,
    simulate_credit, transaction_credit_data, validate_correlation,
)

day = pd.Timestamp("2026-05-20")
discount = load_discount_curves("data/integration/eur_taux_exemple.csv", "SNAPSHOT")[day]
quotes = transaction_credit_data()
quotes = quotes[quotes.valuation_date.eq(day) & quotes.seniority.eq("subordinated")]
calibration = calibrate_credit(quotes, discount, extrapolate=True)

grid = np.r_[0., COUPON_TIMES]
n_paths = 512
corr = validate_correlation([[1., -.15, .30], [-.15, 1., -.20], [.30, -.20, 1.]])
independent = np.random.default_rng(1101).standard_normal((n_paths, len(grid)-1, 3))
innovations = independent @ np.linalg.cholesky(corr).T
model = fit_stochastic(calibration.curves, grid, "lognormal", n_calibration=2048, seed=2202)
result = simulate_credit(calibration.curves, grid, n_paths,
                         model=model, credit_innovations=innovations[:, :, 2], seed=3303)
coupon_alive = result.basket_alive[:, 1:]
assert result.entities == tuple(calibration.curves)
assert result.default_times.shape == (n_paths, len(result.entities))
assert coupon_alive.shape == (n_paths, len(COUPON_TIMES))
assert np.array_equal(coupon_alive, result.first_default_times[:, None] > COUPON_TIMES)
print("Banques UPI", result.entities, "| Masque coupons", coupon_alive.shape)
```

## Réception des autres équipes

| Responsable | Entrées et contrôles au branchement, **PENDING EXTERNAL TEAMS** |
|---|---|
| Taux | Date et base de temps. Facteurs OIS EUR et conventions de collatéral. Trajectoires de taux et d’actualisation si nécessaires. Vérifier l’alignement des piliers et le repricing CDS joint sur la grille. |
| FX | Quotité EUR/USD (USD par EUR), mesure, calendrier des observations et sens des conditions. Valider l’ordre des facteurs et corrélations sur la même grille. |
| Pricing | Grille, ordre des banques, chemins, règles du payoff, coupon/rappel, conventions aux dates simultanées et panier contractuel. Vérifier formes, premier défaut, réutilisation des chemins, impacts des stress sur le prix de la note et compatibilité avec Longstaff–Schwartz. |
| Crédit | Vérifier clauses de défaut, exactitude des noms et contrats et devise/rang avant une calibration contractuelle. Transmettre intensités, survies, temps de défaut et avertissements d’extrapolation. |

