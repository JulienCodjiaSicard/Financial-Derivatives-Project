# Note ECE : composante EUR/USD

Code du sous-groupe change. Il valorise la condition EUR/USD < K des 14 coupons semestriels de la note ECE Bank (Garman-Kohlhagen, smile Vanna-Volga, réplication des digitales), calcule les sensibilités, prépare les paramètres du moteur Monte Carlo commun et compare au comportement historique de l'EUR/USD.

Les résultats et leur lecture sont dans [`resultats_fx/A_LIRE.md`](resultats_fx/A_LIRE.md).

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
| `config/valo_2026-11-06.json` | Tous les paramètres : date de valorisation, fenêtre de la barrière, coupon, conventions, méthode de smile, tailles des chocs, hypothèses |
| `data/` | Fixings BCE (fenêtre de la barrière et historique depuis 1999), courbes €STR et SOFR, fichier de marché (spot, courbes, vols) |
| `fx/` | Le code, un module par étape : `config`, `courbes`, `gk`, `smile`, `digitales`, `sensibilites`, `vol_effective`, `export_mc`, `historique`, `rapport` |
| `scripts/run_all.py` | Lance toute la chaîne et écrit `resultats_fx/` |
| `tests/` | Tests `pytest` de chaque étape |
| `resultats_fx/` | Résultats : `A_LIRE.md`, `coupons_fx.csv`, `sensibilites_fx.csv`, `export_mc.json` (pour le moteur commun), analyse historique, graphiques |
| `pricing_fx.py` | Script initial, gardé pour référence (taux à 3 %, vol plate, 2 dates) ; ses sorties sont les fichiers `01_` à `04_` et `A_LIRE.txt` |

## Changer une hypothèse

Toutes les hypothèses sont dans la config, marquées `"hypothese": true` avec leur justification. Par exemple, pour un coupon de 3,5 % par semestre, mettre `"valeur": 0.035` dans `note.coupon_par_periode`, puis relancer `python scripts/run_all.py`.

Une donnée de marché ne doit jamais être inventée : si une donnée manque sans hypothèse déclarée, le chargement s'arrête et liste ce qui manque.

## Questions ouvertes

Coupon par an ou par semestre, K sur fixing BCE ou plus haut intrajournalier, date de valorisation commune, vols au-delà de 1 an, choix entre vol effective et barrière effective pour le moteur commun : voir la fin de `resultats_fx/A_LIRE.md`.
