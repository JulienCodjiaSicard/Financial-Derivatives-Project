# Note ECE : composante EUR/USD

Code du sous-groupe change. Il valorise la condition EUR/USD < K des 14 coupons semestriels de la note ECE Bank (Garman-Kohlhagen, smile Vanna-Volga, réplication des digitales), calcule les sensibilités, prépare les paramètres du moteur Monte Carlo commun et compare au comportement historique de l'EUR/USD.

Les résultats et leur lecture sont dans [`resultats_fx/A_LIRE.md`](resultats_fx/A_LIRE.md).

## Cohérence avec le sous-groupe taux

La valorisation par défaut (`config/valo_2026-11-13.json`) utilise **les courbes du sous-groupe taux**, à **sa date de valorisation, le 13/11/2026** :

- EUR : swaps Euribor 6M en courbe unique ; USD : par yields Treasury, utilisés comme proxy du SOFR (le groupe n'a pas accès aux courbes OIS) ;
- les facteurs d'actualisation sont lus dans `data/groupe_taux/curve_eur.csv` et `curve_usd.csv` (colonne `P_loglin`) : **copie à l'identique** des sorties du sous-groupe taux (`output/` de leur dossier, avec `discount_coupon_dates.csv` et `hw_params.json`). On retrouve exactement leurs `P_eur` et `P_usd` de `discount_coupon_dates.csv` (test `tests/test_groupe_taux.py`) ;
- le dossier `Change/` fonctionne donc **seul**, sans le dossier du sous-groupe taux à côté ;
- si le sous-groupe taux relance `code/curves.py`, recopier ses quatre fichiers de `output/` (`curve_eur.csv`, `curve_usd.csv`, `discount_coupon_dates.csv`, `hw_params.json`) dans `data/groupe_taux/`, puis relancer `python scripts/run_all.py` et `python -m pytest`. Quand leur dossier est présent à côté de `Change/`, un test signale toute copie périmée.

La config du 06/11/2026 (`config/valo_2026-11-06.json`, courbes €STR / SOFR relevées sur BlueGamma) est gardée pour comparaison : `python scripts/run_all.py config/valo_2026-11-06.json` (attention, ce script réécrit `resultats_fx/`).

## Lancer le projet

Python 3.10 ou plus récent. Sous Windows (PowerShell), depuis ce dossier :

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
python scripts/run_all.py      # régénère tout resultats_fx/ (quelques secondes)
python -m pytest               # lance les tests
```

Sous macOS ou Linux, remplacer la deuxième ligne par `source .venv/bin/activate`.

## Organisation

| Dossier ou fichier | Contenu |
|---|---|
| `config/valo_2026-11-13.json` | Config par défaut : tous les paramètres (date de valorisation, fenêtre de la barrière, coupon, conventions, méthode de smile, tailles des chocs, hypothèses) |
| `config/valo_2026-11-06.json` | Ancienne config (courbes €STR / SOFR au 06/11/2026), gardée pour comparaison |
| `data/` | Fixings BCE (fenêtre de la barrière jusqu'au 06/11 ou au 13/11, et historique depuis 1999), courbes €STR et SOFR du 06/11, fichiers de marché (spot, courbes, vols) |
| `fx/` | Le code, un module par étape : `config`, `courbes`, `gk`, `smile`, `digitales`, `sensibilites`, `vol_effective`, `export_mc`, `historique`, `rapport` |
| `scripts/run_all.py` | Lance toute la chaîne et écrit `resultats_fx/` |
| `tests/` | Tests `pytest` de chaque étape |
| `resultats_fx/` | Résultats : `A_LIRE.md`, `coupons_fx.csv`, `sensibilites_fx.csv`, `export_mc.json` (pour le moteur commun), analyse historique, graphiques |

## Pour le sous-groupe Monte Carlo

Le moteur commun n'a **aucun code à lancer chez nous** : il lit le fichier [`resultats_fx/export_mc.json`](resultats_fx/export_mc.json), écrit par `python scripts/run_all.py` (module `fx/export_mc.py`). Il est réécrit à chaque exécution, il faut donc reprendre la dernière version après toute relance.

À utiliser, bloc `pour_pricer_note_ece` (noms de clés du moteur) :

| Clé | Contenu |
|---|---|
| `fx_spot` | Spot EUR/USD (USD pour 1 EUR) à la date de valorisation |
| `fx_vol_tenors` | Les 14 dates de coupon en années (ACT/365F) |
| `fx_atm_vols` | Vol ATM de marché à chaque date (décimal), à utiliser comme vol du change dans la simulation |
| `fx_barrier` | **Barrière effective, une par date** : avec la vol ATM, elle redonne la probabilité calculée avec le smile. À utiliser à la place du K unique |
| `usd_ois` | Courbe USD au format `Curve(tenors, zeros)` (taux zéro continus, années). Nom de clé gardé pour le moteur ; la courbe réelle est indiquée dans `indice_courbe_usd` (Treasury, proxy SOFR) |

Une seule modification dans `pricer_note_ece.py` : remplacer la comparaison au K unique par

```python
ok_fx = sim['fx'] < np.asarray(terms.fx_barrier).reshape(-1, 1)
```

Vérifications à faire côté moteur :

- `reference_eur_ois_sous_groupe_fx` : la courbe EUR utilisée ici, à comparer au `eur_ois` du moteur (elles doivent être identiques) ;
- `controle_moteur` : faire tourner le moteur sur la seule condition de change (sans taux, crédit ni rappel) avec `fx_barrier` ; il doit retrouver les probabilités `p_smile_attendues` à `tolerance_points` près (écart toléré car le moteur a des taux EUR stochastiques).

Le détail (pourquoi une barrière effective plutôt qu'une vol effective) est dans `resultats_fx/A_LIRE.md`.

## Changer une hypothèse

Toutes les hypothèses sont dans la config, marquées `"hypothese": true` avec leur justification. Par exemple, pour un coupon de 3,5 % par semestre, mettre `"valeur": 0.035` dans `note.coupon_par_periode`, puis relancer `python scripts/run_all.py`.

Une donnée de marché ne doit jamais être inventée : si une donnée manque sans hypothèse déclarée, le chargement s'arrête et liste ce qui manque.

## Questions ouvertes

Coupon par an ou par semestre, K sur fixing BCE ou plus haut intrajournalier, date de valorisation commune, vols au-delà de 1 an, choix entre vol effective et barrière effective pour le moteur commun : voir la fin de `resultats_fx/A_LIRE.md`.
