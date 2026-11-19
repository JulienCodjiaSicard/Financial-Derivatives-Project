# ECE 2026 · Moteur de risque de crédit bancaire

Le module calibre une courbe d’intensité sur les CDS EUR de chaque banque et fournit au groupe Pricing les dates de défaut, les états de survie et le facteur systémique. Le moteur est validé sur des données synthétiques et sur des observations publiques rapprochées, à une maturité par banque et date. Voir le [rapport d’audit des 18 phases](AUDIT.md) et la [synthèse de soutenance](SYNTHESE.md).

**Validation finale — STATUT 2.** « CREDIT FINALIZED WITH MODEL CONVENTION LIMITATIONS ». Le code, les jeux de données et les calculs autonomes sont vérifiés. Le spread Tradeweb utilise l’option B, comme équivalent par académique. Voir l’[audit du spread](AUDIT_SPREAD_TRADEWEB.md), le [rapport de validation](AUDIT.md) et la [notice d’intégration](INTEGRATION_PRICING.md) avec son exemple Python testé.

## Lancer le projet

Depuis le dossier contenant ce README, avec Python 3.12 sous Windows, macOS ou Linux.

~~~powershell
python -m venv .venv
# PowerShell
.venv\Scripts\Activate.ps1
# macOS ou Linux : source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q -rs
python credit_engine.py
python app.py --export-figures
streamlit run app.py
~~~

Le rapport de démonstration s’écrit dans outputs/. Ajouter --quick pour recalibrer les CDS, exécuter les stress et le backtest sans les benchmarks stochastiques. Ajouter --output chemin pour écrire ailleurs. Le dashboard tourne localement. QuantLib sert à l’audit externe CDS et au calendrier TARGET de la reconstruction OIS. Il est installé par les dépendances.

| Élément | Rôle |
|---|---|
| credit_engine.py | Import, pricer CDS, bootstrap, survie, panier, scénarios, risques, backtest et commande de démonstration |
| app.py | Trois onglets Streamlit reliés au moteur |
| test_credit.py | Contrôles analytiques, données, modèles stochastiques, intégration et application |
| data/ | Historique synthétique, gabarits d’import et exemples d’entités candidates |
| outputs/ | CSV de validation, diagnostics, benchmarks et graphiques de démonstration |

Pour régénérer les preuves quantitatives de l’audit en PowerShell, depuis ce dossier.

~~~powershell
New-Item -ItemType Directory -Force outputs/audit_reproduit | Out-Null
$env:ECE_VALIDATION_OUTPUT = (Resolve-Path outputs/audit_reproduit).Path
python -m pytest -q -rs
Remove-Item Env:ECE_VALIDATION_OUTPUT
~~~

Sans cette variable, les tests ne produisent aucun fichier de preuve durable. Les contrôles CDS QuantLib, les quatre courbes OIS, le panier Monte Carlo et la notice d’intégration tournent hors ligne. Les revalidations HTTP ponctuelles sont documentées par leurs empreintes et s’effectuent via les commandes de collecte de cette page.

## Calibration sur données publiques réelles

Le mode **PUBLIC** du tableau de bord s’ouvre sans réseau avec les données livrées. L’étude « Transactions CDS observées » présente 21 spreads de clôture Tradeweb TW SBSEF sur six banques et 18 dates. L’UPI, le rang de dette et l’échéance réelle sont rapprochés de DTCC/SEC. Le spread traité sert d’équivalent par dans le moteur ECE. Le recovery suit une hypothèse de place. Une transaction par nom, date et rang identifie un seul segment d’intensité. Dans la deuxième étude, les courbes EUR sont observées et les entrées CDS sont explicitement synthétiques.

Le moteur privilégie les facteurs EUR reconstruits à partir de transactions €STR OIS CFTC sur quatre dates (20 mai, 25 juin, 27 octobre et 6 novembre 2026). Les courbes zéro-coupon BCE AAA et tous émetteurs sont des proxies souverains disponibles sur 140 dates. La courbe EIOPA sans VA est une référence mensuelle au 31 octobre et s’affiche à sa date propre. **OBSERVED REGULATORY RFR CURVE — NOT DIRECT €STR OIS MARKET CURVE**.

| Données | Contenu |
|---|---|
| `data/market/tradeweb.csv` | Clôture observée EUR en pb, UPI, dates, horodatage de génération, URL et empreintes des deux sources, enregistrement DTCC apparié. |
| `data/market/dtcc.csv` | Registre de 735 tickets EUR senior observés avec le motif d’exclusion du bootstrap direct. |
| `data/market/ois_trades.csv`, `data/market/dtcc_ois.csv` | Transactions €STR OIS filtrées et courbes reconstruites, avec dispersion, effectif par pilier et erreur de repricing. |
| `data/market/estr.csv`, `data/market/mmsr.csv` | Fixings €STR de la BCE et taux moyen OIS MMSR par trimestre. |
| `data/proxies/` | Courbes souveraines BCE et référence de base EIOPA. |
| `data/demo/` | Historique et courbe synthétiques originaux. |
| `outputs/public/` | Repricing des observations, intensités, survies, premier défaut, sensibilités et historique des snapshots. |

Commandes hors ligne, depuis la racine du projet.

~~~powershell
python credit_engine.py --transaction-study
python credit_engine.py --public-study --date 2026-11-06
streamlit run app.py
~~~

Actualisation ponctuelle des sources autorisées.

~~~powershell
python credit_engine.py --refresh-public tradeweb_cds --date 2026-06-25
python credit_engine.py --refresh-public dtcc_cds --date 2026-11-06
python credit_engine.py --refresh-public estr --start 2026-11-01 --end 2026-11-17
python credit_engine.py --refresh-public dtcc_ois --date 2026-11-06
python credit_engine.py --refresh-public ecb_aaa --start 2026-11-01 --end 2026-11-17
python credit_engine.py --refresh-public ecb_all --start 2026-11-01 --end 2026-11-17
~~~

Pour mettre à jour un snapshot récent, actualiser le €STR sur la semaine précédant l’archive OIS. Le bouton Streamlit le fait lors d’une actualisation des swaps €STR. Les requêtes BCE sont limitées à 371 jours, chaque commande DTCC ou Tradeweb en ligne à une archive par source. Les versions déjà en cache sont conservées et les nouvelles dates s’ajoutent après contrôle du contenu. Un 404 indique qu’un rapport n’est pas disponible pour la date. Les archives sans instruments éligibles et les dates où manque un pilier OIS restent visibles comme statut, sans ajustement du jour d’observation. La liste d’UPI est limitée aux contrats vérifiés. Ajouter une référence demande de confirmer l’entité et le rang dans les deux sources. L’EIOPA et MMSR restent des références téléchargées pour cette livraison.

Le panier indépendant subordonné du 20 mai est illustratif. Les différentes séniorités ne sont pas agrégées. Les projections à cinq et sept ans portent une indication d’extrapolation lorsque la dernière maturité observée est plus courte. Les 18 dates forment une étude rétrospective discontinue. L’appréciation du panier de la note et le choix d’une courbe institutionnelle officielle relèvent de la term sheet et de l’équipe Taux. Le mode SNAPSHOT et la fonction `load_discount_curves` acceptent toujours les facteurs EUR fournis séparément à la date de chaque CDS. [Recherche, droits, rapprochement et résultats](RECHERCHE_SOURCES.md).



## Périmètre et conventions de la note

Le panier intervient dans la condition de non-défaut. À une observation t, le moteur fournit l’état \(\mathbf{1}_{\{\tau_{\mathrm{first}}>t\}}\). Le groupe Taux remet la courbe EUR et ses trajectoires. Le groupe FX produit les trajectoires de change. Pricing associe leurs dates et chocs aux nôtres, applique les clauses de la note et décide de l’exercice bermudéen.

L’énoncé initial précise un investissement de 50 M€ dans une note à horizon 7 ans et exige un backtest de six mois. La proposition Projet.pdf prend un coupon annualisé \(C\) multiplié par 0,5, deux conditions FX et crédit, un nominal protégé et des rappels de 2 à 6,5 ans. La mission rapporte 7 % « tous les six mois », un swap EUR 10 ans sous 5 %, une fenêtre FX du 06/05 au 06/11/2026 et une ambiguïté sur la protection du nominal. La photo du tableau contient un schéma avec 100 M€ et une grille d’option qui atteint 7 ans. Le nominal de la note, l’assiette et le taux du coupon, la condition de taux, l’ordre coupon/rappel, les conséquences d’un défaut et les dates exactes de remboursement relèvent de la term sheet validée. À 7 ans, la proposition écrite traite l’échéance finale comme un remboursement, distinct des exercices anticipés.

## Données, provenance et import

| Mode | Entrée |
|---|---|
| DEMO | 28 observations hebdomadaires ou de clôture du 6 mai au 6 novembre 2026. Trois banques fictives, cinq tenors, 15 quotes par date. Dernière date reprise de l’archive synthétique. Les bid-ask sont simulés. |
| SNAPSHOT | Deux CSV ou XLSX datés fournis par le groupe. Le dashboard expose la date choisie, la dernière observation disponible et la source déclarée. |
| PUBLIC | Clôtures de transactions CDS et courbes publiques supervisées en cache. Actualisation sur demande. |
| MARKET | DataFrame de records d’un fournisseur autorisé transmis par l’appelant, via l’API Python. |

Les courbes single-name avec historique et identification des contrats sont des produits institutionnels. La grille complète par entité, devise, type de cotation et date reste une option via un export autorisé, complété si possible par la courbe EUR du groupe Taux aux mêmes dates. Le cache public décrit plus haut couvre une seule maturité par entité et date. [S&P Global Single Name Pricing](https://www.spglobal.com/market-intelligence/en/solutions/products/single-name-pricing-data) · [LSEG CDS Single Name](https://www.lseg.com/en/data-catalogue/fixed-income/pricing/cds-single-name-brokers) · [Bloomberg BVAL](https://professional.bloomberg.com/products/data/enterprise-catalog/pricing/evaluated-pricing/) · [Tableau public BNP Paribas](https://derivate.bnpparibas.com/service/ueber-uns/bonitat-und-credit-default-swaps/)

Le [fichier des candidats](data/candidate_banks.csv) propose BNP Paribas S.A., Deutsche Bank AG et Banco Santander, S.A., à titre de point de départ. Ils figurent comme groupes G-SIB dans la liste FSB publiée le 27 novembre 2025, sur données fin 2024. Leurs entités juridiques et LEI sont documentés par l’EBA. Une G-SIB est une désignation globale. Le périmètre BCE des établissements importants est plus large. Pour chaque candidat, vérifier l’identifiant RED ou équivalent, l’entité de référence, la dette de référence, la séniorité, la restructuration, la documentation du contrat et la devise du CDS. Un LEI seul ne fixe pas le contrat. Un iTraxx ou un CDS USD constitue une autre exposition et demanderait un traitement distinct. [Liste FSB](https://www.fsb.org/2025/11/2025-list-of-global-systemically-important-banks-g-sibs/) · [Divulgation EBA et LEI](https://www.eba.europa.eu/risk-and-data-analysis/risk-analysis/risk-monitoring/global-systemically-important-institutions-g-siis) · [Entités supervisées BCE](https://www.bankingsupervision.europa.eu/ecb/pub/pdf/ssm.listofsupervisedentities202606.pl.pdf)

### Schéma CDS

Utiliser le [gabarit CDS](data/cds_template.csv). Un enregistrement par date, entité et maturité. CSV UTF-8, séparateur virgule ou point-virgule et point décimal, ou première feuille XLSX.

| Champs | Signification |
|---|---|
| valuation_date, collected_at | Date d’observation ISO et horodatage de collecte UTC. La collecte peut être postérieure pour un export d’archives. |
| entity_id, bank_name | Identifiant stable confirmé par Pricing (p. ex. code RED vérifié) et nom juridique. |
| maturity_date ou tenor_years | Échéance ISO de préférence et/ou horizon numérique. Si les deux sont remplis, la date prime sur le tenor libellé. |
| spread, unit, quote_type | Nombre, unité bps / decimal / percent et type par_spread. 60 bps = 0,006 en décimal = 0,60 % en unité percent. |
| bid, ask | Facultatifs, dans la même unité et de part et d’autre du mid. |
| currency, seniority, restructuring | EUR et identifiants textuels explicites de séniorité et de restructuration. Une série distincte par contrat. |
| reference_entity_id, documentation, reference_obligation, source_url | Métadonnées facultatives recommandées. Identifiant RED ou équivalent, version documentaire, obligation de référence et lien autorisé. Référence et version documentaire renseignées doivent rester cohérentes par entité. |
| available_at | Horodatage UTC auquel cette version était effectivement disponible. Il peut précéder la date d’extraction d’un historique. |
| recovery, provider, data_kind | Recovery fractionnaire, fournisseur et observed ou synthetic. |
| time_basis | ACT/365F avec dates ou idealized_years pour une grille abstraite. |

Les spreads « quoted » associés à une convention de conversion et les montants upfront sont refusés à l’import. Faire convertir les cotations d’origine en spreads par par un outil de marché prenant en charge le coupon standard, les flux et les dates du contrat. Conserver la cotation d’origine et sa provenance séparément lors de l’export.

### Schéma actualisation

Le [gabarit EUR](data/eur_template.csv) utilise valuation_date, pillar_date ou time_years, discount_factor, currency, time_basis, data_kind et provider. source_url, available_at et curve_kind sont facultatifs. Une courbe, un fournisseur et une base de temps par date. Si le pilier zéro manque, il est ajouté avec \(D(0,0)=1\). Les facteurs sont strictement positifs, y compris en présence de taux négatifs. L’interpolation est linéaire en \(\log D\). Une date réelle utilise le nombre de jours / 365. Le dernier pilier doit couvrir tous les CDS. La courbe EUR s’utilise dans son domaine de couverture. L’extrapolation crédit à intensité terminale plate est un choix explicite et signalé. Un résultat à 7 ans issu d’une courbe calibrée jusqu’à 5 ans porte le statut extrapolated_flat. Entre piliers, la survie est interpolée par intensité constante. Les résultats conservent le fournisseur EUR et les métadonnées CDS déclarées.

### Export autorisé et validation réelle

1. Avec l’accès établissement, ouvrir le produit single-name CDS du fournisseur. Sur Bloomberg, vérifier la courbe du nom dans le Terminal et l’instrument dans CDSW. Sur LSEG, demander l’export des références, des historiques et du champ par approprié à l’offre souscrite. Le desk ou le support fournisseur confirme le champ par et les droits de redistribution.
2. Demander une ligne par date, entité juridique, contrat et maturité disponible, idéalement 1Y/2Y/3Y/5Y/7Y. Fixer l’heure d’observation, la devise EUR, la séniorité, la restructuration, la version documentaire et le recovery. Exporter mid et fourchettes dans la même unité. Utiliser les dates effectives d’échéance dans maturity_date.
3. Si la quote d’origine est upfront ou quoted/conventional spread, conserver ses métadonnées. Faire produire un équivalent par justifié avec courbe, coupon standard et convention de place documentés. Vérifier l’alignement des calendriers entre la conversion et le pricer académique et rapprocher le résultat d’une valorisation de place.
4. Recevoir les facteurs EUR du groupe Taux à la même date et base, jusqu’à la dernière maturité importée. Demander la convention de collateral EUR et l’horodatage des courbes du replay. Fournir available_at lorsque l’archive atteste la disponibilité de cette version à l’époque.
5. Charger les deux fichiers en mode SNAPSHOT ou lancer la CLI. Examiner les entités, les piliers couverts, les résidus et les dates manquantes. Transmettre les diagnostics en respectant les droits du fournisseur.

~~~powershell
python credit_engine.py --quotes cds_autorises.csv --discount eur_taux.xlsx --date 2026-11-06 --bank RED_A --bank RED_B --output run_snapshot
~~~

Remplacer la date et les identifiants par les valeurs validées. Donner --backtest-start et --backtest-end pour le replay. L’option --require-point-in-time exige les horodatages de disponibilité. Une courbe partielle limite l’horizon commun. L’option --extrapolate active un prolongement crédit plat dans la couverture EUR, signalé dans les diagnostics. La CLI écrit dans un dossier distinct de la démonstration.

## Mathématiques et conventions de valorisation

Pour la banque \(i\), \(H_i(t)=\int_0^t\lambda_i(u)\,du\), \(S_i(t)=e^{-H_i(t)}\) et \(f_i(t)=\lambda_i(t)S_i(t)\). La PD cumulée est \(1-S_i(t)\). La PD entre \(s\) et \(t\), conditionnelle à la survie en \(s\), vaut \(1-e^{-(H_i(t)-H_i(s))}\). Une intensité s’exprime par an. Les probabilités implicites aux CDS sont risque-neutres et incorporent les primes de risque ainsi que les effets de liquidité.

Pour un CDS sans upfront, l’équilibre au spread \(s\) est \(P=s(A_{\mathrm{payé}}+A_{\mathrm{couru}})\). Le nominal unitaire donne

\[
P=(1-R)\int_0^T D(0,u)\lambda(u)S(u)\,du,\qquad
A_{\mathrm{payé}}=\sum_j \Delta_j D(0,T_j)S(T_j).
\]

Sur \([T_{j-1},T_j]\), la jambe courue intègre \((u-T_{j-1})D(0,u)\lambda(u)S(u)\). Le code coupe aux nœuds des taux, du crédit et du coupon. Sur chaque sous-intervalle, il calcule analytiquement les moments d’ordre 0 et 1 de \(e^{-(f+\lambda)u}\). Une série de Taylor évite la perte de précision quand \(f+\lambda\) approche zéro. Le PV acheteur est \(N[P-sA-U]\), avec \(U\) une fraction du nominal payée à l’origine si elle est déjà connue. Le spread fair vaut \(P/A\), et un bp de prime représente \(10^{-4}A\) par unité de nominal.

Le pricer académique utilise des échéances trimestrielles en années depuis zéro, un éventuel stub final, la protection et le couru réglés au défaut, et aucun couru initial. Il couvre une valeur à l’origine sans décomposition clean/dirty ni conversion upfront. Le calendrier IMM, les ajustements calendaires, les accruals ACT/360 réels, step-in, remise du contrat, accrual rebate, settlement et spécificités ISDA 2014 ne sont pas implémentés. Une quote par issue d’une convention de place peut donc donner une intensité différente de celle d’un moteur ISDA. [ISDA CDS Standard Model](https://www.cdsmodel.com/) · [Strata IsdaCdsTradePricer](https://strata.opengamma.io/apidocs/com/opengamma/strata/pricer/credit/IsdaCdsTradePricer.html)

Le bootstrap fige les segments précédents, résout le PV du nouveau CDS par Brent sur \(\lambda\geq0\), puis recalcule chaque spread. En mode strict, une racine inadmissible lève une erreur avec la banque et le tenor. La tolérance au bord zéro suit l’échelle de la cotation. Le repli constrained divise les résidus par la largeur bid-ask avant de les minimiser au carré. Une largeur manquante ou nulle prend la médiane des largeurs exploitables de la banque. Sans fourchettes, le poids est uniforme. Un plancher de max(0,1 pb, 10 % de la médiane) évite qu’une valeur minuscule capte tout l’ajustement. Le dénominateur appliqué, le résidu signé, l’écart rapporté au bid-ask et le statut sont exportés. La cible de 0,01 pb sert de repère numérique synthétique et se lit avec la liquidité et les conventions de chaque quote. \(\lambda\simeq s/(1-R)\) reste un contrôle d’ordre de grandeur.

## Premier défaut et facteur commun

L’événement surveillé est \(\tau_{\mathrm{first}}=\min_i \tau_i\). Sous indépendance, \(S_{\mathrm{basket}}(t)=\prod_i S_i(t)\). Pour chaque trajectoire et chaque banque, on tire un seuil de loi exponentielle unité. La date de défaut est le premier franchissement du seuil par le cumul d’intensité, avec inversion de la pente dans l’intervalle du franchissement. Le défaut est absorbant. Dans les modèles stochastiques, les seuils restent indépendants conditionnellement à la trajectoire du facteur commun. Le modèle décrit une dépendance systémique, sans contagion ni saut provoqué par le défaut d’une autre banque.

| Modèle | Ajustement marginal | Usage et écueils |
|---|---|---|
| Déterministe | Bootstrap exact sur spreads réalisables. Inversion analytique du cumul. | Benchmark de prix CDS et de panier indépendant. Aucun choc crédit de marché. |
| Cox lognormal | \(dY=-\kappa Ydt+\sigma dW\). \(\lambda_{ij}=h_{ij}\exp(\beta Y_j)\) sur le pas j. | Modèle avancé recommandé sous paramètres de scénario. Base non négative flexible, OU simulé exactement aux nœuds. L’intégration Cox est discrétisée. |
| CIR++ | \(dy=\kappa(\theta-y)dt+\sigma\sqrt{y}dW\). \(\lambda_{ij}=a_{ij}+y_j\). | Disponible sur les scénarios faisables. Le décalage continu et celui de grille doivent être admissibles. La plage de volatilités est plus contrainte. |

À chaque pas on impose la moyenne de \(\exp(-\sum_{k\le j}\lambda_{ik}\Delta_k)\) égale à \(S_i(t_{j+1})\). Le lognormal résout cette équation en \(h_{ij}\) par Brent. L’ajustement CIR++ discret factorise le décalage de manière analytique, \(a_{ij}=\log(\overline{w_i e^{-y_j\Delta_j}}/S_i(t_{j+1}))/\Delta_j\), où \(w_i\) est le poids de survie acquis au pas précédent. Le facteur est évalué au bord gauche par choix de prévisibilité. La simulation de pricing utilise un échantillon distinct de l’échantillon antithétique de recalage. Une normalisation instantanée du multiplicateur ne suffit pas à recaler la survie cumulée.

En temps continu, le CIR++ impose \(a_i(t)=\lambda_i^{\mathrm{CDS}}(t)-f_{\mathrm{CIR}}(t)\). Pour \(E_y[e^{-\int y}]=A(t)e^{-B(t)y_0}\), \(f_{\mathrm{CIR}}=\kappa\theta B+y_0(1-\kappa B-\sigma^2 B^2/2)\). On contrôle la valeur maximale de cette expression entre les nœuds, y compris son sommet éventuel en \(B\). La condition de Feller \(2\kappa\theta\ge\sigma^2\) est imposée pour le schéma de démonstration. Le facteur est simulé par Euler implicite sur la dérive de \(\sqrt y\), avec racine positive et pas fin. Une transition exacte issue de la loi du \(\chi^2\) non centrée est possible. L’inversion quantile consommerait les normales fournies via un transformé uniforme, avec un coût supérieur et une autre relation entre innovations et chocs du facteur. Les paramètres et les corrélations sont des scénarios, car les CDS individuels fixent des marges. [Cox, Ingersoll et Ross (1985)](https://pages.stern.nyu.edu/~dbackus/BCZ/discrete_time/CIR_Econometrica_85.pdf)

## API du groupe Pricing

~~~python
import numpy as np
from credit_engine import (
    calibrate_credit, fit_stochastic, load_discount_curves,
    reprice_joint_cds, simulate_credit, validate_correlation,
)

curves_by_day = load_discount_curves("snapshot_eur.csv", "SNAPSHOT")
day = next(iter(curves_by_day))  # Un fichier EUR mono-date pour cet exemple.
cal = calibrate_credit("snapshot_cds.csv", curves_by_day[day],
                       basket=["RED_ENTITE_A", "RED_ENTITE_B"])
# Préparés par Pricing sous la mesure risque-neutre EUR.
# times : années depuis date de valorisation, premier point zéro.
# correlated_z : (n_paths, len(times)-1, 3) ordonné taux EUR, FX, crédit.
# df_paths : facteurs EUR (n_paths, len(times)), D(0,0)=1.
fit = fit_stochastic(cal.curves, times, "lognormal")
credit = simulate_credit(cal.curves, times, correlated_z.shape[0], model=fit,
                         credit_innovations=correlated_z[:, :, 2], seed=9011)
credit_coupon_condition = credit.basket_alive[:, coupon_indices]
first_default_year = credit.first_default_times
risk_state = credit.factor
~~~

Le code attend des normales centrées réduites par pas, sans facteur \(\sqrt{\Delta t}\). Corréler les innovations du schéma discret chez Pricing. Si un moteur entend imposer une corrélation instantanée brownienne, il doit tenir compte des noyaux d’intégration des transitions exactes OU et Hull-White. L’égalité entre corrélations d’incréments standardisés et corrélations instantanées dépend du schéma et du pas. Fixer également le sens du FX, par exemple le changement de signe entre USD par EUR et son inverse. Une corrélation historique de variations de spreads est un point de départ de scénario sous la mesure historique. Elle ne calibre pas la corrélation des défauts sous la mesure risque-neutre.

- validate_correlation vérifie la matrice symétrique 3×3, à diagonale unitaire et définie positive. Pricing applique une seule fois la factorisation aux chocs de marché.
- credit.default_times est de forme (n_paths, n_banks) dans l’ordre credit.entities. La valeur inf signifie aucun défaut observé à l’horizon. first_default_times est de forme (n_paths,). basket_alive et bank_alive(entity) ont la forme (n_paths, len(times)). Pour la régression LSM de Pricing, factor fournit l’état systémique sur la même grille.
- cal.curves[entity].hazard_rate(t), survival_probability(t), default_probability(t, start=0), default_density(t) et integrated_hazard(t) utilisent les temps en années. cal.probabilities(times) produit une table pour toutes les banques.
- Pour simuler l’indépendance, appeler simulate_credit(cal.curves, times, n_paths) sans modèle ni innovations. Pour CIR++, appeler fit_stochastic(..., "cirpp", params). Refaire le recalage après toute modification de grille, de courbes ou de paramètres. credit_stress fournit les courbes à repricer par Pricing pour son CS01.
- Pour vérifier une quote dans le modèle joint, appeler reprice_joint_cds(T, entity, recovery, credit, df_paths, reference_bps=spread). La fonction utilise les facteurs EUR issus des mêmes trajectoires, les protections et le couru conditionnels au facteur crédit, et rapporte l’écart au prix de référence. Reproduire \(S(t)\) ne suffit pas à imposer \(E[D(0,t)\mathbf 1_{\{\tau>t\}}]\). Les écarts observés avec le proxy Taux de test figurent dans outputs/joint_cds.csv.
- comparison_discounts accepte une seconde matrice alignée ligne par ligne sur les mêmes trajectoires. Les erreurs standard du spread et de l’écart apparié utilisent la méthode delta appliquée au ratio des jambes moyennes. Elles sont conditionnelles au recalage et supposent des trajectoires d’évaluation indépendantes entre lignes. Antithétiques ou quasi-Monte Carlo côté Pricing demandent un estimateur adapté.
- calibration_innovations sert aux tests de grille couplés. Fournir les normales de la moitié positive, de forme (n_calibration // 2, len(times)-1), dans un échantillon séparé du pricing. Le moteur ajoute leurs opposées. Le branchement standard utilise la graine interne.
- Avant validation réelle, vérifier dates et provenance, références du panier, calendrier, ordre des facteurs, signe du change, corrélations empiriques et moyenne des facteurs EUR. Repricer les CDS du panier avec les trajectoires de Pricing et statuer sur les écarts avant mise en production du prix de la note.

Exemple exécutable avec les fichiers fournis et un générateur fictif du groupe Pricing.

~~~python
import numpy as np
from credit_engine import demo_data, calibrate_credit, fit_stochastic, simulate_credit

quotes, eur = demo_data()
day = max(eur)
cal = calibrate_credit(quotes.query("valuation_date == @day"), eur[day], mode="DEMO")
t = np.linspace(0, 7, 85)
corr = np.array([[1., -.25, .35], [-.25, 1., -.40], [.35, -.40, 1.]])
common_z = np.random.default_rng(52).standard_normal((2000, 84, 3)) @ np.linalg.cholesky(corr).T
fit = fit_stochastic(cal.curves, t, "lognormal")
result = simulate_credit(cal.curves, t, len(common_z), model=fit, credit_innovations=common_z[:, :, 2])
print(result.basket_alive[:, 6::6].shape)  # (2000, 14), aux dates de coupon
~~~

## Stress, historique et dashboard

L’onglet **Calibration CDS** montre le spread saisi puis recalculé, les résidus, les intensités et la survie. **Premier défaut & corrélation** compare l’indépendance à un scénario Cox lognormal et à CIR++ lorsque le décalage est réalisable. Les intervalles Wilson à 95 % quantifient l’erreur Monte Carlo binaire, et la table donne l’erreur marginale constatée. **Risque, stress & backtest** permet de cibler une banque, un tenor ou le panier, de modifier le recovery ou une quote et de relancer le même moteur.

Les stress et éditions manuelles recalibrent les courbes. Les diagnostics de scénario préservent source_spread_bps, source_recovery et source_data_kind. Pour comparer deux simulations stressées, recalibrer le modèle stochastique et réutiliser les innovations d’évaluation et les graines de seuils.

Le replay garde le panier fixe, utilise des courbes du jour et journalise les dates sautées. La démonstration et le dashboard retirent séparément les tenors intérieurs proches de 3 et 5 ans, à ±0,5 an, pour tester l’interpolation à la même date. L’API conserve le retrait 5 ans par défaut. Avec available_at, une version arrivée après la fin du jour UTC est écartée. Le mode strict exige des horodatages complets pour les CDS et les facteurs EUR. Sans eux, les diagnostics indiquent valuation_date_only et l’absence de preuve sur les versions rétroactives. La fenêtre synthétique du 6 mai au 6 novembre 2026 sert à la démonstration. Le groupe fixe la fenêtre réelle indépendamment de celle de la barrière FX. Le replay mesure stabilité, repricing et interpolation en maturité sur six mois.

## Comment présenter notre module Crédit au professeur

1. Expliquer que le CDS rémunère un vendeur de protection contre la perte \(1-R\). On cherche l’intensité risque-neutre qui met à l’équilibre primes et protection.
2. Montrer les courbes et leurs résidus. Chaque banque a ses propres cotations. À chaque tenor, le bootstrap ajoute un segment et conserve les précédents.
3. Demander « le coupon est-il encore autorisé par le crédit ? ». Il faut la survie de toutes les banques. À 7 ans, les trois banques fictives ont une PD de premier défaut d’environ 25,38 % sous indépendance.
4. Afficher le Cox lognormal et CIR++. Expliquer que les courbes de survie de chaque banque restent les cibles, puis relever le rôle du facteur commun et des chocs de Pricing. Illustrer le rejet CIR++ avec \(\theta=y_0=2\,\%\).
5. Choquer le panier de +50 pb puis revenir au scénario central. Ouvrir le backtest et le test de tenor écarté. Indiquer que le résultat est une démonstration synthétique. Le test QuantLib porte sur des conventions alignées et le test joint illustre l’effet de la corrélation sur le repricing.

Les prochaines données nécessaires à la validation réelle sont un panier contractuel identifié, les quotes par EUR de chaque entité, les courbes certifiées du groupe Taux, les dates confirmées et des trajectoires jointes sous la même mesure. Pour contraindre la dynamique ou la dépendance, il faudrait des instruments supplémentaires ou une étude des paramètres historiques accompagnée d’une hypothèse explicite de passage à la mesure de pricing.
