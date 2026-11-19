# Audit du spread transactionnel Tradeweb

**Décision Option B.** Le spread de clôture Tradeweb sert d’équivalent par dans le pricer académique ECE à une maturité. La convention de conversion effectivement utilisée par Tradeweb reste indéterminée. Le résultat mesure une intensité implicite sous hypothèses de modèle. Le cache source, les exports et l’application distinguent la donnée de marché et le résultat de modèle.

## Ce que les rapports établissent

| Élément public | Ce qu’il établit |
|---|---|
| Rapport `dailyMarketDataReport` | Venue TW SEF LLC, date de référence et horodatage de génération. La génération ne prouve pas l’heure précise de mise à disposition publique. |
| `prcFrmt=SprdBsisPts`, `clsgPrc` | Le champ de clôture est exprimé en points de base. L’énumération distingue les spreads, les prix obligataires en %, les primes en devise ou en % du nominal, et les taux fixes. La règle de conversion économique et les paramètres de valorisation ne sont pas renseignés par ce code. |
| `opngPrc`, `lwstPrc`, `hgstPrc`, `tradCnt=1` | Les quatre niveaux égaux et le compteur unitaire soutiennent un rapprochement à un ticket. Les extrêmes de la publication peuvent inclure des cours d’offre ou de demande selon la règle. Ils ne sont pas une fourchette bid-ask indépendante. |
| UPI, symbole, `ccy=EUR`, `tenor` | Contrat et rang à recouper avec le ticket réglementaire. Le libellé P3Y ou P5Y est séparé de l’échéance effective. L’UPI n’est pas une preuve de RED ou de documentation ISDA exacte. |
| DTCC, lieu TWSF, NEWT/TRAD, même date et même UPI | Le candidat unique fournit l’échéance, le coupon et une intention de compensation. Les sources n’exposent pas de clé d’identification commune du deal. Les messages de correction et d’annulation sont contrôlés dans l’univers archivé. |

La normalisation ne renomme en `par_spread` que l’entrée destinée au solveur ECE. Elle conserve `source_quote_type=transaction_closing_spread`, `quote_quality=option_B_academic_equivalence` et `market_par_verified=False`. L’adaptateur vérifie unité, UPI, banque, rang, hypothèse de recouvrement, coupon fini et distinct du spread, ainsi que l’unicité de l’identifiant DTCC. L’import SNAPSHOT de vrais spreads par conserve son contrat d’entrée. [Rapports Tradeweb](https://reports.tradeweb.com/public/sbsefdownloads/) · [Spécification XML SEC](https://www.sec.gov/files/dmdr_technical_specification_20250313.pdf) · [Règle SEC 825](https://www.sec.gov/files/rules/final/2023/34-98845.pdf).

### Pourquoi 39,75 pb et 100 pb peuvent coexister

Pour BNP Paribas le 25 juin 2026, **100 pb** est le coupon annuel fixe du CDS. **39,75 pb** est le spread de clôture observé dans le rapport. Un coupon standard peut se négocier à un autre niveau économique parce qu’un paiement initial compense la différence. Le *par spread* est le taux courant qui égalise les jambes de protection et de prime sans upfront sous un modèle donné. Le *spread conventionnel* est une façon de représenter l’upfront d’un contrat standard en passant par un modèle de conversion, généralement à intensité plate. Sa valeur peut différer d’un par issu d’une courbe multi-piliers ou de conventions différentes. [SEC Regulation SBSR, Q13](https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/frequently-asked-questions-regulation-sbsr) · [Spécification du convertisseur ISDA](https://www.cdsmodel.com/assets/cds-model/docs/ISDA%20Standard%20CDS%20Contract%20Converter%20Specification%20-%20Sept%204%2C%202009.pdf) · [Conventions de cotation Strata](https://strata.opengamma.io/apidocs/com/opengamma/strata/product/credit/type/CdsQuoteConvention.html).

**Upfront DTCC.** Le montant public est non signé et les champs de payeur, receveur et date de paiement ne sont pas diffusés dans ces rapports. La qualification *clean* ou *dirty* n’y est pas établie. Pour BNP, le rapport fournit 124 406,17446 € et un notionnel affiché de `4,403,000+`. La division par la borne de censure ne fournirait pas un pourcentage du vrai notionnel. Le montant reste un champ de provenance. Aucun signe réel n’est reconstruit, y compris pour les trois contrats à notionnel affiché non plafonné. [Éléments CFTC 57–62](https://www.cftc.gov/media/7626/Part43_45TechnicalSpecification083022CLEAN/download) · [Règlement DTCC, §5.1.3](https://www.sec.gov/files/exhibit-hh-1-9-2024.pdf).

Une conversion complète demanderait la règle de conversion Tradeweb ou l’upfront contractuel signé et sa convention, le vrai notionnel, la date de règlement, les dates d’accrual et de protection, le step-in, les paiements standard, l’ACT/360, le calendrier, le recovery de convention, le rang et la restructuration, et la courbe EUR de référence au bon instant. Échéance, date de rapport, rang, coupon et devise sont recoupés. La présence d’une date effective T+1 dans le ticket ne prouve pas à elle seule toutes les clauses. La courbe du convertisseur et la restructuration demeurent inconnues. [Spécification standard du contrat CDS](https://www.cdsmodel.com/assets/cds-model/docs/Standard%20Corporate%20CDS%20Contract%20Specification%20-%20May%2012%2C%202009.pdf) · [Convertisseur OpenGamma Strata](https://strata.opengamma.io/apidocs/com/opengamma/strata/pricer/credit/CdsMarketQuoteConverter.html).

## Contrôle numérique indépendant

QuantLib 1.44 valorise une protection hypothétique avec échéance exacte, calendrier trimestriel CDS2015, primes ACT/360, échéances de paiement ajustées Following TARGET, step-in T+1 et cash settlement T+3 ouvrés. La dernière période est inclusive selon le jour-compte choisi. Les taux sont ceux de la courbe OIS reconstruite de la même journée. Le pricer ECE du projet utilise une grille de 0,25 année et sa propre convention de stub et de prime courue au défaut. Le contrôle conserve les mêmes inputs de spread et de recovery, compare le spread QuantLib en réutilisant d’abord l’intensité ECE, puis refait le fit en intensité constante. Les variantes ACT/365F, BCE AAA, BCE tous émetteurs et les recoveries 20 %, 30 %, 40 % et 50 % sont exportées. La BCE est échantillonnée aux dates civiles adjacentes à ses piliers pour QuantLib.

| Banque | Tenor restant | Spread observé | λ ECE / QL en % par an | PD ECE / QL | Écart de spread à λ ECE |
|---|---:|---:|---:|---:|---:|
| UniCredit | 3,0877 ans | 51,85 pb | 0,6460 / 0,6549 | 1,9749 / 2,0017 % | −0,701 pb |
| Société Générale | 3,0877 ans | 54,60 pb | 0,6803 / 0,6896 | 2,0786 / 2,1068 % | −0,739 pb |
| Commerzbank | 3,0877 ans | 63,75 pb | 0,7943 / 0,8052 | 2,4227 / 2,4555 % | −0,863 pb |
| BNP Paribas senior | 4,9890 ans | 39,75 pb | 0,6605 / 0,6696 | 3,2414 / 3,2854 % | −0,542 pb |

Les écarts de PD vont de **2,68 à 4,41 pb de probabilité**. Pour BNP, l’approximation \(s/(1-R)\) donne 0,6625 % d’intensité annuelle. Sous QuantLib et OIS, sa PD au pilier va de 2,4743 % à 3,9294 % lorsque le recouvrement passe de 20 à 50 %. À 40 %, la PD est 3,2854 % avec OIS, 3,2850 % avec BCE AAA et 3,2840 % avec BCE tous émetteurs. Ces mesures locales dépendent du portefeuille et des courbes étudiés. Les écarts de calendrier n’indiquent pas une erreur de calcul du pricer ECE.

Avec les hypothèses QuantLib et le coupon 100 pb, le modèle calcule le flux de règlement par unité de notionnel comme son upfront *clean* diminué du remboursement de prime courue du point de vue acheteur. Pour les trois tickets subordonnés à 2 M€ affichés sans signe « + », l’écart entre **la valeur absolue** de ce flux modélisé et le montant DTCC est respectivement **9,71 €, 9,15 € et 7,29 €**. Cette cohérence étaye l’interprétation conventionnelle sur ces trois exemples. Elle ne détermine ni le signe réel, ni les clauses de règlement, ni la provenance de la courbe du convertisseur. Aucune comparaison par nominal n’a été faite pour BNP.

Le résultat contractuel certifié demeure hors de portée des champs publics. La méthode est reproductible avec `pytest -k independent_isda` et la variable `ECE_VALIDATION_OUTPUT` décrite au README. [Comparaison complète](outputs/audit_final/cds_convention_comparison.csv) · [Revalidation des sources](outputs/audit_final/cds_source_revalidation.csv).

