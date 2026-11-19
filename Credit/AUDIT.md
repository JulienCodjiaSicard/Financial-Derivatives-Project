# Audit final du module Crédit · ECE 2026

**STATUT 2 — CREDIT FINALIZED WITH MODEL CONVENTION LIMITATIONS.** Le module Crédit autonome est gelé. **Option B** pour les spreads Tradeweb. La convention de marché de conversion en par, les clauses exactes des contrats et l’intégration avec les autres équipes restent à confirmer. [Audit du spread](AUDIT_SPREAD_TRADEWEB.md) · [Notice d’intégration](INTEGRATION_PRICING.md).

## Validation finale du livrable

### 1 · Périmètre et baseline

L’archive obligatoire `ECE_2026_Module_Credit_Donnees_Publiques.zip` a été extraite dans un dossier à part. Empreinte SHA-256 `5ca4e390dfd0e47ba6035e3023cc70dd2d57640dee0ce457da8647228c7c830b`. Inspection des trois fichiers Python, des quatre documents d’origine, de `requirements.txt`, du cache Tradeweb, du registre DTCC et des manifests, des transactions OIS, des courbes BCE et EIOPA, des snapshots et des exports DEMO/PUBLIC. Baseline réexécutée avant modification. 55 tests réussis, aucun échec ni skip en 82,11 s. Les trois ZIP antérieurs sont préservés.

### 2 · Observations et décision de marché

Les **21 observations, six banques, 18 dates et trois rangs de dette** ont été recoupés à nouveau à partir des archives disponibles. Les 140 archives SEC déjà collectées ont été utilisées pour rechercher des messages liés. Aucun CORR, EROR ou autre changement lié à ces 21 identifiants n’a été relevé dans cette couverture. Quatre fichiers crédit ont été retéléchargés (XML et ZIP du 20 mai et du 25 juin), plus les quatre archives OIS. Les huit réponses HTTP 200 ont exactement les empreintes du cache. Les dates et niveaux n’ont pas changé. Un UPI est une clé produit recoupée avec le nom et le rang, sans certification du RED ni de la documentation complète. [Contrôles des 21 lignes](outputs/audit_final/cds_source_revalidation.csv) · [Empreintes et contrôles OIS bruts](outputs/audit_final/source_hash_revalidation.json).

Le code `SprdBsisPts` identifie un format de spread, et `clsgPrc` le champ de clôture. Le coupon annuel fixe est distinct. DTCC diffuse un montant upfront non signé et les clauses de paiement sont absentes. Le notionnel affiché pour BNP porte le marqueur de plafonnement. Le comparatif QuantLib utilise un contrat hypothétique aligné en maturité. À intensité ECE fixée, les écarts de spread vont de −0,54 à −0,86 pb. Après recalage, les écarts de PD vont de +2,68 à +4,41 pb de probabilité. Pour les trois tickets non plafonnés du 20 mai, les montants absolus modélisés sont à moins de 10 € du montant DTCC sur 2 M€ affichés, sans utiliser cette comparaison pour attribuer un payeur réel. [Méthode, données manquantes et tableau QuantLib](AUDIT_SPREAD_TRADEWEB.md).

### 3 · Les quatre reconstructions €STR

| Date | Transactions / piliers hors pont | Piliers à un seul ticket | Fixing €STR antérieur | Facteur à 7 ans | Max. erreur QuantLib (pb) | Dispersion max. entre trades du même pilier |
|---|---:|---:|---:|---:|---:|---:|
| 20/05/2026 | 37 / 11 | 3 | 1,929 % | 0,820425 | 2,81 × 10⁻¹⁰ | 7,65 pb |
| 25/06/2026 | 55 / 10 | 1 | 2,181 % | 0,835614 | 2,87 × 10⁻¹⁰ | 2,293 pb |
| 27/10/2026 | 50 / 12 | 1 | 2,441 % | 0,802880 | 4,26 × 10⁻¹⁰ | 4,55 pb |
| 06/11/2026 | 45 / 12 | 2 | 2,442 % | 0,810661 | 3,04 × 10⁻¹⁰ | 3,30 pb |

Le contrôle de 187 lignes brutes confirme l’index €STR composé, les deux jambes en EUR, le spot TARGET T+2, le jour-compte ACT/360 publié, l’absence de package et d’upfront et l’intention de compensation `I`. Sur 67 lignes, l’indicateur de termes non standard est vide. Les critères observables de dates, périodicité, taux et flux admissibles sont vérifiés indépendamment de ce champ. La courbe ajuste des **médianes** d’exécutions intrajournalières, avec des exécutions sur une plage de plusieurs heures. La dispersion, la présence de piliers isolés et l’absence de clôture synchronisée avec le CDS imposent une limite de représentativité.

La reconstruction sur une courbe projette le composé par le ratio des facteurs aux bornes d’accrual et actualise chaque paiement. Le pont jusqu’au spot utilise le fixing overnight antérieur, en ACT/360 simple. Les périodicités observées sont normalisées, le calendrier est TARGET Modified Following, et le lag des deux jambes est **supposé** à un jour ouvré. L’interpolation porte sur \(\log D\). Une validation QuantLib indépendante construit des swaps à index overnight et recalcule les jambes à chaque maturité. Les facteurs sont positifs, \(D(0)=1\), décroissants dans les quatre strips et couvrent sept ans. À titre de sensibilité, faire varier le lag commun entre zéro et deux jours déplace le taux zéro à 7 ans de moins de 0,001 pb sur ces dates. Ce contrôle reste conditionnel à une seule courbe et à la convention de projection commune. Il n’est pas une estimation du délai de paiement réel.

Les taux zéro 7Y reconstruits sont de 2,8276 %, 2,5655 %, 3,1364 % et 2,9986 %. Les deux zéros souverains BCE du même jour sont comparés séparément pour chaque date dans le tableau d’audit. EIOPA est une référence réglementaire mensuelle au 31 octobre et aucune des quatre courbes n’a cette date. Le classeur EIOPA reste consultable avec sa convention et sa disponibilité. [Audit par courbe](outputs/audit_final/ois_curve_validation.csv) · [Audit par pilier](outputs/audit_final/ois_pillar_validation.csv) · [Sources et conventions](RECHERCHE_SOURCES.md).

### 4 · Calibrations, premier défaut et scénarios

**Étude A.** DEMO identifie cinq segments à partir des maturités synthétiques de chaque banque. Le premier défaut indépendant à sept ans est 25,38 %. Le replay 28 dates sur six mois et les tenors écartés testent le moteur sur données synthétiques.

**Étude B.** PUBLIC, équivalence académique à un seul tenor par banque et par date. Le 25 juin, 39,75 pb pour BNP Paribas senior à l’échéance du 20 juin 2031 donnent 0,6605 % d’intensité annuelle, 96,7586 % de survie et 3,2414 % de PD. Avec BCE AAA, la survie est 96,7591 %. Avec OIS, +25 pb abaisse la survie à 94,7741 %. Un recovery de 55 % à spread inchangé donne 95,7017 %. Le 20 mai, les intensités subordonnées UniCredit/Société Générale/Commerzbank donnent respectivement 98,0251 %, 97,9214 % et 97,5773 % de survie au pilier commun. Leur produit donne 6,3379 % de PD de premier défaut sous indépendance. Le panier est illustratif.

**Étude C.** À cinq ans, les scénarios de premier défaut valent 8,22 %, 10,06 % et 11,87 %. À sept ans, ils valent 10,14 %, 13,80 % et 17,30 % avec 0,5×, 1× et 1,5× l’intensité terminale de chacune. Après le pilier, \(S(t)=S(T)\exp(-m\lambda(T)(t-T))\). Cela conserve positivité, continuité et monotonie. Le contrôle analytique par somme des intensités est complété par 40 000 chemins indépendants. Le scénario central diffère de 1,70 erreur type Monte Carlo à sept ans. Le plafond de projection dans cette étude est sept ans. Le prolongement est une expérience de survie et le calcul n’ajoute aucun instrument ou prix de marché. [Contrôles analytiques](outputs/audit_final/basket_tail_revalidation.csv) · [Validation Monte Carlo](outputs/audit_final/basket_public_mc.json).

CIR++ et Cox lognormal sont conservés et testés. Leurs paramètres dynamiques et les corrélations restent des scénarios. Les 18 dates PUBLIC sont un historique de snapshots, sans second tenor ou validation hors échantillon. Les PD sont risque-neutres au sens du modèle, avec des paramètres supposés.

### 5 · Corrections, preuves et intégration

| Risque ou faiblesse | Correction et preuve |
|---|---|
| Le champ moteur `par_spread` pouvait masquer la nature transactionnelle. | Conservation du type source, statut Option B, `market_par_verified=False` et statut du plafond. Test des incohérences de coupon, UPI, rang et unité. |
| Une entrée de cache invalide pouvait être acceptée dans l’adaptateur PUBLIC. | Contrôles d’identité, de recouvrement et d’unicité. Cohortes de séniorité contrôlées et scindées. Le parseur écarte des termes non standard explicites et les coupons non finis. |
| Le bootstrap OIS ne contrôlait pas tous les doublons sur des tables déjà filtrées et le lag était implicite. | Rejet dupliqués et maturités incohérentes. Lag paramétrable pour l’audit et défaut inchangé à 1 jour. QuantLib recalcule les quatre courbes et les trois hypothèses de lag. |
| Les sorties modèles et stress demandaient des libellés plus directs. | Colonnes et badge Option B, recouvrement et PD, date et qualité du spread, marque SCENARIO et bornes de prolongement. Visuel, trois onglets et téléchargement CSV vérifiés dans Edge. |
| Le branchement externe devait être vérifiable sans les autres équipes. | Contrat d’API avec signatures, données de courbe, forme des tableaux, exemple réellement exécuté et test de repricing joint. L’intégration avec les vrais fichiers est **PENDING EXTERNAL TEAMS**. |

**60 tests réussis, aucun échec ni skip, 84,84 s.** Suite d’origine, cinq nouveaux contrôles ciblés et enrichissement du test Streamlit. Les neuf CSV PUBLIC ont été régénérés. Les valeurs des colonnes précédentes sont identiques. Le calcul DEMO complet, les stress et les benchmarks ont reproduit les 16 CSV analysés, hors colonnes de temps d’exécution. Les 16 fichiers de données d’origine ont les mêmes empreintes. [Comparaison des sorties](outputs/audit_final/baseline_comparison.json) · [Journal final](outputs/audit_final/final_validation.json) · [Notice Taux/Pricing](INTEGRATION_PRICING.md).

## Historique de la version données publiques


## Calibration sur données publiques réelles

La baseline finalisée a été extraite et ses 47 tests reproduits avant modification (47 réussis, 34,53 s). Archives antérieures conservées. La recherche a vérifié 140 archives de transactions DTCC/SEC, 139 rapports de clôture Tradeweb, les courbes BCE, l’overnight €STR, MMSR et le classeur mensuel EIOPA. Le [rapport des sources](RECHERCHE_SOURCES.md) décrit les licences, instruments écartés et preuves. Aucun prix ICE ou Markit n’entre dans un calcul ou fichier livré.

**Contrôles de données.** Exactitude des dates d’exécution et de maturité, unicité des clés, séparation EUR/USD, choix du nom juridique, UPI, séniorité, rejet des packages, des messages corrigés/erronés et des ambiguïtés coupon/upfront. Le champ `SprdBsisPts` du rapport réglementaire Tradeweb est la source du spread observé. Le code DTCC sert au rapprochement et à la validation du calendrier. La restructuration et les délais de publication réels des deux flux ne sont pas attestés. Le cache comporte dates de collecte et empreintes des réponses. Les APIs ont été appelées sur les données effectivement livrées. Les cas 403, 429 prolongé, timeout et HTML inattendu sont testés hors réseau. La source CFTC au 17 novembre a été atteinte et le strip incomplet a été rejeté.

**Contrôles financiers.** 21 observations rapprochées, une maturité par banque/date/rang. Tous les facteurs des courbes retenues sont positifs, toutes les intensités reproduites sont positives, toutes les survies diminuent, toutes les probabilités restent admissibles. Les 184 repricings (scénarios compris) satisfont l’équilibre des jambes avec un résidu maximal de 1,53 × 10⁻¹² pb. Les trajectoires et paramètres CIR++/lognormal préexistants restent des scénarios. L’OIS provient de transactions €STR exécutées et un lag de paiement commun d’un jour ouvré est supposé. La méthode, les spreads d’entrée et leur dispersion sont livrés avec les facteurs. Les sensibilités à la courbe, au recovery et à +25 pb sont réexportables.

**Fenêtre historique.** Du 6 mai au 21 juillet, 18 dates crédit exploitables. Pas de forward fill, pas de second tenor pour une validation hors calibration, pas d’historique quotidien homogène des CDS avec preuve point-in-time. Le replay de cette livraison est une étude de snapshots datés. Les résultats de six mois sur données synthétiques restent dans les fichiers de validation d’origine.

**Exécution de la version données publiques.** 55 tests réussis, 0 échec, 0 ignoré (76,93 s). Validation dans Edge des trois onglets PUBLIC, des hypothèses de prolongement, du rejeu des observations, de la sensibilité taux et du téléchargement CSV. [Journal et commandes](outputs/public/validation.json).

**Reproductibilité.** Quand la collecte est indisponible, le cache demeure lisible par les commandes d’étude, sans accès réseau. Le moteur accepte toujours les fichiers externes autorisés. Les diagnostics de base et les conventions du portefeuille calibré peuvent être transmis à Pricing indépendamment de ces fournisseurs.



## Historique de l’audit initial

## 01 · Baseline auditée

L’archive initiale ECE_2026_Module_Credit.zip reste disponible dans le dossier de livraison initial. SHA-256 f698ac4b508b1f73dbd62444382dc5da6cf9c512d04945b435b8b16462363427. Ses 31 fichiers ont été extraits dans un dossier de travail distinct. Les trois fichiers Python de départ ont les mêmes empreintes que dans l’archive. La commande python -m pytest -q -rs a reproduit les **40 tests** annoncés, y compris QuantLib et Streamlit, en 21,65 s. L’environnement d’audit rapporté par l’utilisateur avait 38 tests exécutés et 2 skips liés à ces dépendances. Ici, ils étaient installés.

Le run de l’archive a reproduit **16 tableaux sur 16**, avec un écart numérique nul sur les valeurs publiées, hors champs de temps. Les deux cas QuantLib alignés concordent à l’identique. Le résidu CDS maximal initial était de \(1,9611\times10^{-12}\) pb, la PD de panier à 7 ans de 25,3829 %, le replay de 28 dates. Les spreads joints de référence ont été reproduits autour de 82,350 / 80,788 / 79,249 pb pour \(\rho=-0,55/0/+0,55\). L’expérience finale utilise un protocole apparié différent. Les prix déterministes et les scénarios historiques comparables sont contrôlés dans [baseline_reproduction.json](outputs/baseline_reproduction.json).

**Éléments conservés**. Intégration analytique du CDS, survie par intensité déterministe par morceaux, bootstrap strict, modèles CIR++ et lognormal, seuils exponentiels du basket, attente d’innovations externes, pricer joint, stress, moteur du backtest, trois onglets Streamlit et trois fichiers Python. Le cours principal slides 75–80 et les notes Niels pages 19–25 confirment la logique de modèle à intensité et d’équilibrage des jambes. Les quatre PDF et les photos de la mission initiale ont été accessibles. Le document collectif retrouvé dans Drive reproduit les consignes et retranscrit un payoff proposé par le professeur. [Document collectif Projet Dérivés Financiers](https://docs.google.com/document/d/1Yg0hNqUo4R8GrCcK2zDNHaP9awqGlFp94GeHCh_12S0/edit)

## 02 · Matrice des exigences et des divergences

| Point | Sources et formulations | Responsabilité et décision attendue |
|---|---|---|
| Nominal | Énoncé p. 2–3 et Projet.pdf p. 2, 50 M€ investis dans la note sur 100 M€ collectés. Photo payoff, notation ±100 M€. | Pricing confirme le nominal de la note et l’assiette des flux. Le moteur fournit des probabilités et dates sans nominal de note. |
| Coupon | Document collectif, 7 % tous les six mois. Projet.pdf p. 2–3, coupon annualisé \(C\) multiplié par 0,5. | Confirmer si 7 % est annuel ou par période et la base de calcul. |
| Barrière de taux | Document collectif, swap EUR 10 ans strictement sous 5 %. Projet.pdf présente le taux dans le contexte, avec un payoff conditionné par FX et crédit. | Confirmer le maintien de la condition, l’indice et les fixings. Taux et Pricing. |
| Barrière FX | Document collectif, EUR/USD strictement sous le maximum du 06/05 au 06/11/2026. Projet.pdf, maximum des six mois précédents. | Confirmer série, sens de cotation, calendrier et dates de fixing. FX et Pricing. |
| Crédit | Document collectif et Projet.pdf, absence de défaut des banques concernées. Le tableau se réfère au crédit bancaire EUR. | Crédit fournit \(\tau_i\), le minimum et l’état absorbant. Faire valider événements et périmètre d’entités. |
| Nominal après défaut | Document collectif, remboursement à 7 ans mentionné sous condition d’absence de défaut. Projet.pdf p. 2–3, remboursement intégral même en cas de défaut bancaire. | Obtenir la règle contractuelle, y compris perte, recovery et date après défaut. Aucun choix encodé côté Crédit. |
| Banques | Formulation collective et énoncé sur les banques systémiques ou de la zone EUR, sans liste juridique. Projet.pdf envisage iTraxx ou single-name pour les données. | Arbitrer zone et panier, identifier RED ou équivalent et documentation. L’indice reste un proxy distinct. |
| Rappels | Document collectif, chaque semestre à partir de la deuxième année jusqu’à maturité. Photo, grille atteignant 7 ans. Projet.pdf, exercices de 2 à 6,5 ans. | Confirmer dates exactes, exercice anticipé et remboursement final à 7 ans. Pricing. |
| Ordre d’événements | Les notes n’établissent pas une convention commune pour défaut, observation, coupon et exercice sur une même date. | Pricing confirme l’ordre et le traitement des égalités. Le moteur code seulement l’état \(\tau>t\). |
| Horizon et backtest | Énoncé p. 2–4, horizon 7 ans et validation sur les six derniers mois. Fenêtre calendaire FX dans le document collectif. | Fixer date d’évaluation et fenêtre CDS. Les dates FX ne fixent pas automatiquement le replay Crédit. |

Le document Projet.pdf constitue une proposition de modélisation. Le document collectif attribue le payoff retranscrit au professeur et le présente comme une proposition de travail. La term sheet finale doit résoudre les écarts.

## 03–04 · Disponibilité et branchement des données

| Donnée | Statut | Éléments vérifiés |
|---|---|---|
| Références bancaires institutionnelles | **PARTIEL** | Trois groupes G-SIB zone euro et leurs LEI. Aucun RED/document de référence confirmé pour le produit. [Candidats et sources](data/candidate_banks.csv) |
| CDS par EUR single-name et historique du panier | **NON DISPONIBLE** | Repères publics et statistiques identifiés. Aucun fichier réunissant cotation par, contrat, date et courbe EUR compatible. |
| Courbes EUR du groupe Taux | **NON DISPONIBLE** | Le moteur utilise les facteurs datés fournis. Convention log-linéaire de \(D\), base ACT/365F ou temps idéalisé. La courbe originale et la courbe perturbée ont été testées sur données synthétiques. |
| Pricing réel | **NON DISPONIBLE** | Le document collectif accessible contient les consignes et la répartition. Aucun fichier Taux/FX/Pricing identifiable dans le dossier du cours, les recherches Drive ou les huit dépôts accessibles listés par le connecteur. |
| Démonstration | **COMPLET** | Trois banques fictives, 15 quotes par date, 28 dates et courbes EUR synthétiques. Aucun appel à un fournisseur de marché. |

La [liste FSB du 27 novembre 2025](https://www.fsb.org/uploads/P271125.pdf), la [divulgation EBA](https://www.eba.europa.eu/risk-and-data-analysis/risk-analysis/risk-monitoring/global-systemically-important-institutions-g-siis) et les périmètres de supervision distinguent groupe systémique, entité légale et contrat de CDS. Le LEI sert au rapprochement. L’entité de référence, la documentation, la dette livrable, la séniorité et la clause de restructuration sont à vérifier instrument par instrument.

- La page publique de BNP Paribas affiche des repères **5Y**. Les lignes utiles ne constituent pas une grille de spreads par identifiés, datés et cohérents avec la courbe EUR du projet. [Repères BNP Paribas](https://derivate.bnpparibas.com/service/ueber-uns/bonitat-und-credit-default-swaps/)
- Le graphique 17b de l’ESRB décrit des **largeurs bid-ask upfront** de CDS 5Y seniors MMR sur des groupes cibles. Elles ne fournissent pas un spread par de milieu de fourchette. La BCE publie des agrégats de dispersion. Son portail de séries a renvoyé HTTP 503 sur la page interrogée. [ESRB, graphique 17](https://www.esrb.europa.eu/pub/pdf/reports/esrb.report250411_creditdefaultswaps.en.pdf#page=42) · [BCE, annexe statistique, graphique 9](https://www.ecb.europa.eu/press/fie/pdf/ecb.fieApril2026StatisticalAnnex.en.pdf)
- DTCC documente des transactions réglementaires et des agrégats d’activité. Ils ne constituent pas un historique synchronisé de quotes par comparables pour le panier. Les produits institutionnels offrent des séries single-name plus proches du besoin, sous réserve des droits d’accès et de redistribution. [DTCC](https://www.dtcc.com/market-index-data) · [LSEG](https://www.lseg.com/en/data-catalogue/fixed-income/pricing/cds-single-name-brokers) · [S&P Global](https://www.spglobal.com/market-intelligence/en/solutions/products/single-name-pricing-data) · [Bloomberg](https://professional.bloomberg.com/products/data/enterprise-catalog/pricing/evaluated-pricing/)

La CLI et le dashboard lisent les gabarits datés CSV/XLSX. Le test d’import couvre un panier limité à 5 ans, la provenance et le passage à 7 ans uniquement via extrapolation explicite. Les quotes d’entrée upfront ou quoted/conventional spread sont refusées et doivent être converties au préalable avec une configuration de place vérifiée. Le statut de disponibilité concerne la **calibration**. Les observations publiques partielles ont été examinées sans fabriquer de ligne calibrable.

Pour effectuer la première validation réelle, obtenir le panier exact avec référence contractuelle, les quotes par EUR cohérentes à 1/2/3/5/7 ans aux tenors disponibles, la courbe de facteurs EUR des mêmes dates, l’historique et l’horodatage de la version disponible. Le [protocole d’export du README](README.md#export-autorisé-et-validation-réelle) donne les champs et commandes.


## 05–08 · Pricer, calibration et probabilités

**Périmètre financier.** CDS spot-start, protection à compter de l’origine, coupons jusqu’à maturité avec stub final, facteurs log-linéaires, prime courue et protection réglées au défaut. Le prix acheteur par nominal unitaire est \(P-sA-U\), avec \(A\) annuité risquée et \(U\) upfront à \(t=0\) seulement si déjà connu. Le calendrier natif utilise une horloge en années et des coupons trimestriels. Pour le contrôle externe, un trimestre de 91 jours est exprimé en ACT/365F et le dernier stub utilise ses jours réels. Restent hors de cette convention le calendrier ISDA avec IMM, la protection step-in, les flux de settlement/couru initial, les calendriers ouvrés et la conversion native upfront/quoted spread. [ISDA CDS Standard Model](https://www.cdsmodel.com/)

**Intégration analytique conservée.** La grille d’intégration est l’union des nœuds de taux, d’intensité et de prime. Chaque segment utilise les moments exponentiels d’ordre 0 et 1. Dans la limite sans actualisation, avec intensité constante, couru et protection au défaut, le spread est exactement \((1-R)\lambda\). La comparaison avec une quadrature indépendante SciPy utilise des ruptures hors dates de coupon et une courbe avec taux négatifs. Les résultats analytiques et les legs numériques concordent aux seuils du test.

**QuantLib 1.44.** Le cas historique a été reproduit. Le benchmark élargi couvre quatre configurations et deux conventions de prime courue. Courbes plates, pentes variables, forwards négatifs et taux de recouvrement distincts. Les comparaisons portent sur des flux équivalents, sans rebate initial. QuantLib utilise des intégrations de 2 jours puis 1 jour. Les PV satisfont une borne fondée sur la variation maximale du facteur d’actualisation et le décalage maximal de prime courue sur un pas. Les écarts de spread absolus diminuent dans les huit cas.

| Cas synthétique | Maturité | Recovery | Écart maximal sans / avec couru, pas 1 jour |
|---|---:|---:|---:|
| Faible intensité, courbe plate | 1,00 an | 5 % | 0,00013 / 0,00013 pb |
| Intensité et taux par morceaux, stub | 2,35 ans | 40 % | 0,00499 / 0,00849 pb |
| Forte intensité, taux par morceaux | 7,00 ans | 65 % | 0,02566 / 0,10204 pb |
| Forwards inversés | 1,62 an | 20 % | 0,00368 / 0,01413 pb |

Le plus grand écart avec couru passe de 0,20292 pb à 0,10204 pb lorsque le pas est divisé par deux. Le CDS correspondant a un spread analytique de 444,07 pb. Les comparaisons confirment le pricer dans les conventions alignées et quantifient l’effet de la discrétisation quotidienne de référence. [Cas historique](outputs/quantlib_comparison.json) · [Matrice QuantLib](outputs/quantlib_matrix.json)

**Bootstrap.** Un contrôle à l’échelle de la quote et une tolérance Brent plus fine préservent les intensités positives proches de zéro. Le test récupère des intensités de \(10^{-15}\) à \(10^{-12}\) et des cas de recovery à 99,9 %. Le moteur vérifie le repricing final. L’ajustement contraint utilise l’échelle du bid-ask par instrument, avec imputation médiane et plancher documentés. Un test de courbe incohérente montre que le fit pondéré préserve mieux la quote étroite et signale les résidus. Les cases de couverture distinguent piliers de calibration, points intermédiaires et queue plate extrapolée.

Sur le snapshot synthétique du 6 novembre, les **15 quotes** sont reproduites avec un écart maximal de **\(8,38\times10^{-13}\) pb**. Les variations de précision sont négligeables pour l’économie du cas. Les quinze tableaux partagés, avec l’ancien sous-ensemble 5Y du replay, passent le contrôle de non-régression. Les [diagnostics](outputs/repricing.csv) et les [probabilités semestrielles](outputs/probabilities_semiannual.csv) conservent l’unité, le statut et la source.

| Banque fictive | CDS 7Y | Intensités annuelles 0–1 / 1–2 / 2–3 / 3–5 / 5–7 | Survie 7Y |
|---|---:|---|---:|
| A | 80,876 pb | 0,750 % / 1,000 % / 1,250 % / 1,500 % / 1,800 % | 90,846 % |
| B | 100,911 pb | 0,938 % / 1,250 % / 1,563 % / 1,875 % / 2,250 % | 88,692 % |
| C | 64,795 pb | 0,600 % / 0,800 % / 1,000 % / 1,200 % / 1,440 % | 92,608 % |

Les invariants vérifient survie à zéro, bornes, monotonie, PD cumulée et conditionnelle. Les tests de simulation vérifient absorption, minimum par trajectoire, valeurs infinies pour défaut non observé à l’horizon et cohérence aux dates semestrielles. Le produit des survies donne **25,3829 %** de PD de premier défaut à 7 ans. Les seuils simulés sont tirés indépendamment du facteur commun. Quand \(\beta=0\), le Cox lognormal retrouve la simulation indépendante aux mêmes seuils. À marge fixée, la dépendance peut déplacer le risque de premier défaut et le risque de défauts multiples dans des sens différents.

## 09 · Validation des dynamiques

Les paramètres du facteur sont des scénarios sous la mesure de pricing. Il n’existe pas ici d’historique single-name permettant d’estimer leur stabilité ni d’instruments identifiant leur valeur risque-neutre. Le recalage vise les survies CDS via \(E[\exp(-\int\lambda)]\). Les jeux antithétiques de recalage et i.i.d. de valorisation sont indépendants. Le calibrage des seules moyennes d’intensité ne donnerait pas cette identité.

| Aspect | Déterministe | Cox lognormal | CIR++ |
|---|---|---|---|
| Marges | Exactes dans le modèle | Recalées sur la grille | Recalées sur la grille |
| Positivité | Intensité admissible au bootstrap | Exponentielle positive et base non négative | Feller pour le schéma et décalage admissible, vérifiés en continu et en discret |
| Dépendance | Indépendance du benchmark | Facteur OU commun, transition exacte aux nœuds | Facteur CIR commun, Euler implicite sur \(\sqrt y\) |
| Limites de grille | Inversion analytique des seuils | Intensité constante au bord gauche dans chaque pas | Même intégration en temps, dynamique CIR discrétisée |
| Prix 7Y avec DFs corrélés | Référence déterministe | Effet plus marqué sur le scénario de stress testé | Effet plus faible sur le scénario retenu |
| Intégration | Aucun choc requis | Colonne de normales déjà corrélées | Même interface, contrôle de faisabilité avant simulation |

À 50 000 trajectoires i.i.d. sur la grille mensuelle synthétique centrale, le premier défaut à 7 ans vaut 25,346 % en indépendance MC, 25,230 % en lognormal et 25,374 % en CIR++. Les erreurs standard binaires sont proches de 0,195 point de pourcentage. Les écarts des scénarios centraux à l’indépendance, calculés avec les mêmes seuils, sont petits au regard de leurs propres erreurs appariées. Avec \(\sigma_{\mathrm{OU}}=0,85\), le déplacement apparié est de **−0,574 pp**, erreur standard **0,122 pp**. Le CIR de base trop élevé avec \(\theta=y_0=2\,\%\) est rejeté.

Jusqu’à 100 000 trajectoires, les écarts marginaux observés atteignent 2,33 pb de probabilité en lognormal et 0,48 pb en CIR++. Le maximum normalisé par les erreurs estimées de pricing et de recalage combinées reste sous 2,5 dans les runs centraux. Le diagnostic par nœud est indicatif et ne corrige pas la multiplicité des comparaisons. Les grilles de 4, 12 et 24 pas annuels sont comparées avec innovations d’évaluation couplées. Le test CIR compare aussi la survie du facteur sans décalage à sa référence affine. Le lognormal est retenu comme scénario avancé par défaut pour sa base plus flexible. Voir [premier défaut](outputs/basket.csv), [marges](outputs/stochastic_marginals.csv), [scénarios](outputs/dependence_stress.csv) et [grilles](outputs/grid_convergence.csv).

## 10 · Pourquoi le spread change en modèle joint

Pour un facteur crédit donné, la densité de défaut est \(\lambda(t)S(t)\). La protection moyenne intègre \(E[D_t\lambda(t)S(t)]\), la prime programmée contient \(E[D_{t_j}S(t_j)]\), et le couru utilise aussi le produit actualisé avec la densité. Le recalage de \(E[S(t)]\) contrôle une marge. L’écart de prix sous corrélation passe par les covariances et par la différence entre annuités, puisqu’un spread est un ratio des moyennes.

**Protocole synthétique.** Quote 7Y de la banque A, 80,876 pb. Les innovations de crédit restent identiques entre les scénarios de taux. Le proxy de taux préserve \(E[D_t]\) aux nœuds en théorie, via correction gaussienne. Les corrélations empiriques des innovations s’écartent de leurs cibles de moins de 0,001 sur les comparaisons 40 000 trajectoires mensuelles. L’écart de DF moyen maximal vaut environ \(7,1\times10^{-4}\). Les échantillons de 10k et 40k sont emboîtés, et les grilles de 12 et 24 pas par an partagent les innovations de pricing **et** celles de recalage antithétique. L’étude emploie 24 000 trajectoires de recalage par modèle. Le proxy teste deux facteurs pertinents pour le CDS. Les tests d’interface contrôlent en plus l’ordre des trois facteurs et l’absence de double Cholesky.

| Modèle synthétique | Actualisation | Spread, pb | SE du niveau, pb | Écart apparié de spread ± SE, pb |
|---|---|---:|---:|---:|
| Lognormal, \(\sigma=0,85\) | Déterministe | 81,124 | 0,182 | Référence |
| Lognormal | Stochastique indépendante | 81,129 | 0,183 | +0,004 ± 0,012 vs déterministe |
| Lognormal | Corr. −0,55 | 82,809 | 0,191 | +1,680 ± 0,012 vs indépendante |
| Lognormal | Corr. +0,55 | 79,482 | 0,175 | −1,646 ± 0,011 vs indépendante |
| CIR++ | Déterministe | 80,899 | 0,025 | Référence |
| CIR++ | Stochastique indépendante | 80,896 | 0,025 | −0,003 ± 0,003 vs déterministe |
| CIR++ | Corr. −0,55 | 81,134 | 0,027 | +0,238 ± 0,002 vs indépendante |
| CIR++ | Corr. +0,55 | 80,662 | 0,024 | −0,234 ± 0,002 vs indépendante |

Résultats à 40 000 trajectoires, grille mensuelle. Les SE conditionnelles du ratio utilisent \((P_k-\widehat s A_k)/\overline A\) en décimal, converti en pb. Pour l’écart apparié, les influences sont soustraites trajectoire par trajectoire avant le calcul de la SE. Les IC 95 % figurent dans [joint_cds.csv](outputs/joint_cds.csv).

- L’actualisation stochastique indépendante est compatible avec le contrôle déterministe dans la précision de l’étude. Le lognormal à escompte déterministe se trouve à +0,248 pb du bootstrap, pour une SE conditionnelle de 0,182 pb. Ce contrôle agrège résidu de recalage et évaluation finie. Il ne démontre pas un biais de prix.
- Le passage de 10k à 40k divise environ par deux l’erreur appariée. Par exemple, la SE lognormale pour \(\rho=-0,55\) passe de 0,0225 à 0,0115 pb. À 40k, les différences de spread entre les deux grilles couplées atteignent 0,0076 pb en lognormal et 0,0023 pb en CIR++. Ces différences de grille incluent l’effet de la discrétisation du facteur et du recalage, dont le bruit commun a été réduit. Elles restent faibles face aux effets appariés de corrélation.
- Les signes opposés et l’écart aux contrôles indiquent un effet de covariance du modèle joint sur ce scénario, cohérent avec les poids de discount au défaut. Ils ne constituent pas une règle universelle, un paramètre calibré de marché ou une estimation du prix du produit ECE.

**Recalage conjoint étudié.** Avec les véritables \(D\) et innovations de Pricing sous la même mesure, une correction minimale consisterait à calibrer la base positive par maturité sur \(E[P-s_{\mathrm{par}}A]=0\) sur un échantillon, puis à vérifier les prix sur un autre. Le contrôle doit inclure référentiel contractuel, convergence, positivité et déformation des survies initiales. Le choix entre cible de prix conjointe et conservation des marges se décide avec Pricing. L’audit livre le diagnostic plutôt qu’une transformation qui imposerait artificiellement la quote. Le recalage conjoint de marché est en attente.

Les normales discrètes sont des innovations standardisées par pas. Si Pricing vise une corrélation brownienne instantanée et simule deux OU exactement, la corrélation discrète contient le facteur \(\int K_1K_2/\sqrt{(\int K_1^2)(\int K_2^2)}\), avec les noyaux de transition de chaque facteur. Le signe du change et la convention de mesure doivent être explicités. La validation de Cholesky de la matrice et le choix du générateur restent côté Pricing.

## 11–13 · Intégration, stress et replay

**Interface testée — intégration réelle en attente.** Accorder la date, la base de temps et l’ordre EUR/FX/crédit. Le groupe Taux remet des facteurs EUR positifs, datés et couvrant les quotes. FX confirme le sens de cotation utilisé. Pricing fournit une grille depuis zéro et \(Z_{\mathrm{crédit}}\) de forme (trajectoires, pas), déjà corrélé. Avec scénarios stochastiques, il peut également remettre ses DFs (trajectoires, dates) pour le repricing joint. En retour, le module fournit des dates (trajectoires, banques), le minimum (trajectoires,), le masque de survie (trajectoires, dates) et le facteur. Une [simulation autonome et la procédure de branchement](README.md#api-du-groupe-pricing) sont livrées. Les fonctions publiques antérieures restent utilisables.

| Stress synthétique | PD 7Y | Écart, points de pourcentage |
|---|---:|---:|
| +1 pb parallèle | 25,6448 % | +0,2619 |
| +10 pb parallèle | 27,9612 % | +2,5783 |
| +50 pb parallèle | 37,4147 % | +12,0319 |
| −10 pb parallèle | 22,7124 % | −2,6705 |
| +10 pb banque A | 26,2523 % | +0,8694 |
| +10 pb A, 5Y seul | 25,3298 % | −0,0531 |
| Recovery 20 / 30 / 40 / 50 %, spreads fixés | 19,6807 / 22,1731 / 25,3829 / 29,6672 % | −5,7022 / −3,2098 / 0 / +4,2843 |

Le choc 5Y déplace vers le début les pertes implicites du CDS 7Y à spread constant, ce qui fait baisser l’intensité du segment suivant dans ce cas. Les sorties stressées et l’éditeur du dashboard conservent les quotes sources et étiquettent le scénario. Le CS01 du produit reste une dérivée du pricer de Pricing. [Stress du panier](outputs/stress_basket.csv) · [Détail banque/segment](outputs/stress_banks.csv)

**Replay synthétique.** 28 dates du 6 mai au 6 novembre 2026. Le risque du panier varie de 20,57 % à 29,99 %. Les expériences d’interpolation 3Y et 5Y retirent chacune 84 quotes, avec erreurs absolues moyennes de 3,25 et 3,36 pb, et maxima de 4,70 et 4,93 pb. Les versions disponibles après la date UTC sont refusées. La version stricte impose un horodatage pour les quotes et tous les piliers EUR. Le replay valide le statut du panier par date et journalise les lacunes. L’historique synthétique montre la stabilité et l’interpolation. Le risque de révision sur archives réelles, la fenêtre effective et l’interprétation en fréquence de défaut restent à traiter avant une validation empirique. [Replay](outputs/backtest_daily.csv) · [Statut](outputs/backtest_status.csv) · [Holdouts](outputs/backtest_holdout.csv)

## 14–17 · Tests, interface, performances et soutenance

- **47 tests réussis, 0 échec, 0 skip**. Python 3.12.14, NumPy 2.5.5, pandas 3.1.1, SciPy 1.18.1, openpyxl 3.1.5, Streamlit 1.67.0, Plotly 7.1.0, pytest 9.2.0 et QuantLib 1.44. La suite inclut sept contrôles ciblés supplémentaires. Des validations avec vraies données et Pricing restent hors du total. [Preuves d’exécution](outputs/validation_run.json)
- **Dashboard**. Les trois onglets ont été vérifiés dans Edge sous Streamlit local. Sélecteur CIR++, téléchargement du repricing, historique, édition/recalibration et import SNAPSHOT pour horizon partiel avec activation explicite du prolongement. Les tests Streamlit couvrent aussi banque, date, stress de spread, recovery et erreur CIR++ inadmissible. Les six graphiques hors ligne ont été chargés dans Edge. Les fichiers d’interface fictifs sont des fixtures de test et restent hors du livrable.
- **Performance observée sur le poste d’audit**. Import 0,10 s. Bootstrap 0,12 s. Stress 1,42 s. Replay et deux holdouts 14,08 s. Validation des modèles 12,92 s. Étude jointe complète 14,97 s. Run des tableaux 43,64 s. Génération HTML mesurée sur un run de la même version de l’exporteur à environ 5,5 s, démarrage Python inclus. Les essais d’interface et de calcul ont aussi été exécutés en parallèle, et les temps varient avec la contention.
- À 100 000 trajectoires, la simulation déterministe prend 0,03 s. Le recalage et la simulation prennent 0,41 s puis 0,98 s en lognormal, et 0,08 s puis 0,90 s en CIR++. Un tableau facteur de 100 000 × 85 float64 représente environ 68 Mo. L’état de chaque banque est matérialisé à la demande. La hausse du temps de validation globale correspond à l’étude jointe sur deux grilles et aux holdouts ajoutés. La baseline et la version finale restent du même ordre en temps pour la calibration et les simulations usuelles. [Chronométrages](outputs/diagnostics.json) · [Performance et modèles](outputs/performance_and_models.csv)
- **Reproduction et soutenance**. Installer les [dépendances](requirements.txt), installer QuantLib==1.44 pour le contrôle optionnel, exécuter python -m pytest -q -rs, python credit_engine.py, python app.py --export-figures et streamlit run app.py. Pour exporter les preuves QuantLib à la suite du test, définir ECE_VALIDATION_OUTPUT sur le dossier outputs. Le [README](README.md) détaille les commandes. La [synthèse](SYNTHESE.md) fournit le déroulé et les réponses aux questions techniques.

## 18 · Journal des modifications et bilan

| Gravité | Constat | Correction ou justification | Validation et statut |
|---|---|---|---|
| Élevée | Les scénarios issus d’un import pouvaient conserver le marquage observé après modification d’un spread. | Marquage scenario avec valeurs d’origine et réutilisation du même moteur pour l’édition UI. | Test de provenance et rejet à la réimportation. Corrigé. |
| Élevée | Le même jour de valorisation pouvait masquer la publication ultérieure d’une version. | Horodatage facultatif pris en charge. Rejet des versions tardives et preuve exigible en mode strict. | Tests de version révisée, export tardif et métadonnées absentes. Corrigé côté moteur. |
| Élevée | Le repricing initial confondait variation des chocs et variation de corrélation. | Paire de chemins commune, décomposition par actualisation et contrôles de grille/calibration. | 32 combinaisons niveau–modèle–grille–volume, IC appariés. Corrigé en simulation. |
| Moyenne | Le seuil absolu pouvait ramener des intensités positives proches de zéro à zéro. | Tolérance de bord à l’échelle de la quote, Brent et contrôle du résidu. | Round-trip faible intensité et recovery 99,9 %. Corrigé. |
| Moyenne | Le repli traitait uniformément les quotes, y compris lorsque les bid-ask différaient. | Échelle de résidu issue du bid-ask avec imputation et plancher. | Une quote étroite est mieux préservée dans le test contradictoire. Corrigé. |
| Moyenne | Le repricer joint validait une maturité à l’epsilon près et pouvait encore lire au-delà du dernier pas. | Réutilisation explicite de la maturité bornée. | Tests d’epsilon et de dépassement incompatible. Corrigé. |
| Élevée | Pas de spreads par autorisés ni de courbes EUR ou trajectoires du projet. | Gabarits et import vérifiés, sources vérifiées, contrat et diagnostic détaillés. | Validation synthétique. Partenaire et données en attente. |
| Moyenne | Calendrier et type de quote académiques. | Périmètre explicite, contrôles QuantLib élargis, conversion préalable si nécessaire. | Vérifié pour conventions alignées. Validation de place en attente. |

| Phase | Statut | Preuve ou dépendance |
|---|---|---|
| 01 Baseline | **DONE** | 40 tests. 16 tableaux initiaux reproduits. |
| 02 Consignes | **DONE** | Matrice des divergences. Les arbitrages contractuels restent ouverts. |
| 03 Données CDS | **PARTIAL** | Candidats et fournisseurs documentés. Import testé. Export du panier absent. |
| 04 Courbe EUR | **PARTIAL** | Courbe externe et perturbation vérifiées sur fixtures. Courbe du groupe Taux absente. |
| 05 Pricer | **DONE** | Analytique, quadrature et matrice QuantLib dans le périmètre annoncé. |
| 06 Bootstrap | **DONE** | Cas cohérents, incompatibles, quasi nuls et repli pondéré. |
| 07 Probabilités | **DONE** | Invariants et dates produit vérifiés. |
| 08 Premier défaut | **DONE** | Indépendance analytique, simulation, absorption et scénario de facteur commun. |
| 09 CIR++ et lognormal | **DONE** | Positivité, limites CIR, validation hors calibration et sensibilité sur scénario. |
| 10 Corrélation | **DONE** | Contrôle synthétique apparié, niveaux et convergence. Validation conjointe de marché en attente. |
| 11 Intégration | **PARTIAL** | Formats et interface testés. Pricing réel indisponible. |
| 12 Stress | **DONE** | Chocs, recovery et provenance contrôlés. |
| 13 Backtest | **PARTIAL** | Replay synthétique complet et contrôle point-in-time testé. Historique réel absent. |
| 14 Tests | **DONE** | Suite complète exécutée, skips explicités. |
| 15 Dashboard | **DONE** | AppTest et essai en navigateur local. |
| 16 Performance | **DONE** | Étapes chronométrées, 100 000 trajectoires testées. |
| 17 Soutenance | **DONE** | Synthèse, démo et réponses livrées. |
| 18 Audit final | **DONE** | Défauts hiérarchisés, preuves et statut C. |

