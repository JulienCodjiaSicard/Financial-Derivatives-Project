# Le crédit dans la note ECE 2026

**Synthèse pour la soutenance.** Le groupe Crédit calibre des intensités de défaut à partir de CDS EUR et fournit au groupe Pricing le premier défaut du panier et les états de survie à ses dates d’observation. Le moteur est validé sur données synthétiques et a été calibré sur des spreads de transaction publics à une maturité par banque et date.

## 1 · Du spread CDS à l’état du panier

Un CDS échange une prime contre une protection versée en cas d’événement de crédit lié à une entité juridique. Pour un nominal unitaire et un recouvrement \(R\), la protection verse \(1-R\). Le spread par égalise la valeur de la protection et celle des primes, y compris la prime courue au défaut. Le moteur calcule les deux jambes avec les facteurs EUR et un calendrier trimestriel académique.

L’intensité \(\lambda(t)\) est un taux instantané de défaut, exprimé par an. Sur un court intervalle, une entité encore en vie fait défaut avec une probabilité approximative \(\lambda(t)\Delta t\). La survie jusqu’à \(t\) vaut \(S(t)=\exp(-\int_0^t\lambda(s)\,ds)\). La probabilité de défaut cumulée vaut \(1-S(t)\). Exemples à expliquer à l’oral. Une intensité annuelle constante de 1 % implique une probabilité à un an de \(1-e^{-0,01}\approx0,995\,\%\), et non une probabilité de 1 % à toute échéance.

Le bootstrap calibre chaque banque indépendamment. La première cotation détermine la première intensité. La deuxième ajoute un segment en conservant le premier, et ainsi de suite. Les résidus mesurent la qualité de la reproduction instrument par instrument. Un prix impossible avec une intensité non négative déclenche une erreur ou un repli contraint explicitement choisi. La qualité du fit dépend aussi du recovery, des références et des conventions de cotation.

La note surveille le premier événement du panier, \(\tau_{\mathrm{first}}=\min_i\tau_i\). Sous indépendance, toutes les banques restent en vie avec probabilité \(\prod_i S_i(t)\). En simulation, un seuil exponentiel est tiré pour chaque banque. Le défaut survient lorsque son cumul d’intensité franchit ce seuil. Le statut reste absorbant. Pricing reçoit le premier défaut et le masque de survie sur sa propre grille. Il fixe l’ordre des événements du contrat et les conséquences sur coupon, principal et rappel.

## 2 · Trois modèles, trois usages

| Modèle | Ce que nous démontrons | Usage |
|---|---|---|
| Déterministe | Pricer CDS vérifié, bootstrap par banque, survie analytique et panier indépendant. | Benchmark et scénario de référence. |
| Cox lognormal | Intensité positive via un facteur gaussien à retour à la moyenne. Recalage des survies à chaque date, puis évaluation sur un échantillon séparé. | Scénario corrélé recommandé pour sa flexibilité. |
| CIR++ | Facteur CIR positif et correction déterministe. La correction est contrôlée en continu et sur la grille. | Comparaison de robustesse dans son domaine admissible. Un décalage négatif provoque un rejet explicite. |

Les seuils bancaires sont indépendants conditionnellement au facteur commun. Les banques deviennent dépendantes via ce facteur. Une dépendance plus forte peut regrouper des défauts dans les mêmes trajectoires et réduire la probabilité d’au moins un défaut, à survies individuelles comparables. Les paramètres dynamiques et les corrélations utilisés dans la démonstration sont hypothétiques.

Une survie marginale reproduite n’impose pas le bon prix lorsque le taux est stochastique. La prime dépend de \(E[D(0,t)\mathbf{1}_{\{\tau>t\}}]\), et la protection actualise les paiements au défaut. Dans certains états, les prix des deux jambes changent. La validation compare trois actualisations sur les mêmes chemins de crédit pour mesurer le changement de valorisation. Le facteur EUR synthétique teste l’interface et la mécanique de covariance.

## Calibration sur données publiques réelles

Nous avons rapproché 21 clôtures de transactions CDS Tradeweb des tickets DTCC, sur 18 dates et six banques. Notre jeu contient principalement des contrats subordonnés à trois ans, ainsi que quatre observations proches de cinq ans dans trois rangs séparés. Le seul CDS senior rapproché, BNP Paribas, cote **39,75 pb** le 25 juin 2026 et arrive à échéance le 20 juin 2031. Avec les transactions €STR OIS de cette date et un recovery supposé de 40 %, le moteur donne **0,6605 % d’intensité annuelle** et **96,7586 % de survie** à 4,99 ans.

Le 20 mai 2026, UniCredit, Société Générale et Commerzbank ont trois contrats comparables au rang subordonné. Les trois spreads observés (51,85, 54,60 et 63,75 pb) donnent une probabilité risque-neutre de premier défaut sous indépendance de **6,34 % à 3,09 ans**. À sept ans, les scénarios d’intensité terminale donnent **10,14 %, 13,80 % ou 17,30 %**. Le choix du multiplicateur de queue conditionne le résultat à sept ans. Une observation par nom identifie une intensité et le modèle prolonge la courbe au-delà du pilier. Le panier est illustratif, le recovery est supposé et la restructuration exacte n’est pas publiée. Le spread est une clôture de transaction utilisée comme équivalent par dans le pricer ECE. L’interprétation ne s’étend pas à un mid exécutable.

Les courbes BCE souveraines permettent une comparaison à date commune. Sur BNP Paribas, le choix de la courbe déplace la survie de moins de 0,002 point de pourcentage entre les trois courbes étudiées. Un choc +25 pb la fait passer à 94,77 % avec la courbe €STR. Les snapshots publics sont discontinus, et la démonstration de backtest sur six mois reste synthétique. [Méthode et sources](RECHERCHE_SOURCES.md).

**Option B.** Le spread Tradeweb est un spread de clôture en pb. Le modèle ECE l’utilise comme équivalent par académique. QuantLib teste d’autres conventions sur les mêmes maturités. L’ampleur de l’écart de spread à intensité ECE fixe est de 0,54 à 0,86 pb. La modélisation ne certifie pas l’upfront signé. [Audit de convention](AUDIT_SPREAD_TRADEWEB.md).

## 3 · Résultats à présenter

Les résultats suivants utilisent **trois banques fictives**, des spreads et une courbe EUR synthétiques. Les prix sont exprimés en points de base, les risques en probabilités risque-neutres.

| Contrôle | Résultat synthétique |
|---|---:|
| 15 quotes CDS recalculées | Résidu maximal \(8,4\times10^{-13}\) pb |
| PD de premier défaut à 7 ans, indépendance | 25,38 % |
| Après choc parallèle de +1 / +10 / +50 pb | 25,64 % / 27,96 % / 37,41 % |
| Après baisse de −10 pb | 22,71 % |
| Recovery supposé de 20 % / 40 % / 50 % | 19,68 % / 25,38 % / 29,67 % à spreads fixés |
| Replay 6 mai–6 novembre 2026 | 28 dates sur 28 |
| Tenors écartés 3Y et 5Y | 84 tests chacun. Erreurs absolues moyennes 3,25 pb et 3,36 pb |

Le cas 5Y de la banque A illustre une subtilité. À quote 7Y inchangée, le choc sur le seul 5Y augmente le risque jusqu’à 5 ans et réduit le segment suivant. La PD du panier à 7 ans baisse d’environ 0,053 point de pourcentage dans ce scénario. Le moteur fournit les courbes recalibrées. Le CS01 du prix de la note relève du groupe Pricing.

Les comparaisons QuantLib portent sur des conventions alignées avec le moteur académique. L’intégration quotidienne de référence converge vers le calcul analytique lorsque le pas est réduit. Le calendrier ISDA standard complet, la conversion upfront et l’évaluation de marché de la note relèvent d’un autre niveau de validation. Les conventions et les cas testés sont exposés dans le [rapport d’audit](AUDIT.md).

## 4 · Démonstration en huit minutes

1. Expliquer les deux jambes, le spread par et le recouvrement. En DEMO, montrer comment plusieurs maturités identifient plusieurs segments.
2. Expliquer survie, premier défaut, seuils exponentiels, CIR++ et Cox lognormal. Séparer les intensités marginales des hypothèses de dépendance.
3. Passer en PUBLIC. Montrer le coupon de 100 pb et le spread de 39,75 pb pour BNP, l’échéance de 4,99 ans et la courbe OIS. Expliquer l’option B.
4. Montrer le panier illustratif subordonné du 20 mai et les trois prolongements à sept ans. Passer d’OIS à BCE AAA et afficher un stress de spread ou de recouvrement.
5. Finir avec les limites du replay et l’exemple d’interface Taux/FX/Pricing. Le branchement des vrais fichiers reste **PENDING EXTERNAL TEAMS**. [Notice d’intégration](INTEGRATION_PRICING.md).


## Questions que le professeur peut poser

| Question | Réponse courte |
|---|---|
| Pourquoi les CDS plutôt que Merton ? | Les CDS donnent des prix de protection observables et des contraintes par échéance. Merton donne une lecture structurelle à partir de la valeur et de la volatilité des actifs, avec d’autres entrées à estimer. |
| Pourquoi 40 % ? | Hypothèse centrale des données synthétiques. Un spread seul identifie mal fréquence et sévérité. Les stress de recovery montrent l’impact de cette hypothèse à quote fixée. |
| Intensité ou probabilité de défaut ? | L’intensité est un taux par unité de temps conditionnel à la survie. La PD s’obtient en intégrant ce taux puis en appliquant la survie exponentielle. |
| Probabilité historique ou risque-neutre ? | Les CDS donnent une mesure implicite pour valoriser des flux. Elle dépend aussi des primes de risque et de liquidité. Un historique de faillites vise une fréquence sous la mesure physique. |
| Pourquoi plusieurs maturités ? | Une seule quote impose une seule contrainte de prix. Plusieurs tenors aident à identifier la forme de la courbe, sous l’hypothèse d’intensité constante par morceaux. |
| Un résidu quasi nul suffit-il ? | Il valide le calcul sur les prix utilisés. Il laisse ouvertes la qualité des quotes, les conventions, le recovery, la dynamique et le risque de modèle. |
| Comment créez-vous la dépendance ? | Les intensités répondent au même facteur. Les seuils restent tirés séparément. La corrélation des innovations provient de Pricing et n’est pas appliquée une seconde fois. |
| Pourquoi CIR++ peut-il échouer ? | Si le forward du CIR non décalé dépasse l’intensité cible, reproduire la survie exige un décalage négatif. Le facteur peut approcher zéro, ce qui menace la positivité de l’intensité totale. |
| Comment avez-vous calibré les paramètres dynamiques ? | Les trajectoires imposent des scénarios paramétriques. Le recalage ajuste la composante déterministe aux survies CDS. Les données nécessaires pour identifier la dynamique risque-neutre manquent. |
| Pourquoi la corrélation taux–crédit change-t-elle le spread ? | Les facteurs d’actualisation et les flux conditionnels peuvent co-varier. La moyenne du produit change même si les moyennes marginales sont conservées. |
| Que prouvent six mois de replay ? | La stabilité des courbes, la qualité du repricing et l’interpolation par tenor sur la période. Une PD cumulée sur sept ans demande un horizon et une méthodologie de validation différents. |
| Que transmettez-vous à Pricing ? | Des courbes et diagnostics, les dates de défaut, les états de survie et les facteurs stochastiques alignés sur ses scénarios. Pricing applique le payoff et l’exercice aux dates validées. |
| Quelle différence entre clôture et mid ? | La clôture Tradeweb est une donnée rétrospective liée à une activité rapportée. Un mid est le milieu d’une fourchette observée au même instant pour le même contrat. Le fichier ne donne pas de fourchette exécutable. |
| Pourquoi 39,75 pb avec un coupon de 100 pb ? | Les 100 pb rémunèrent contractuellement le vendeur à chaque période. Le spread de transaction représente un coût économique distinct. Un paiement initial équilibre l’écart à coupon fixé. |
| Pourquoi l’upfront DTCC ne suffit-il pas ? | Le paiement est non signé. Pour BNP, le notionnel est plafonné. Le payeur, la date de cash settlement et la définition clean/dirty ne sont pas établis. |
| Pourquoi 20 % et 40 % ? | Hypothèses de conversion utilisées pour subordonné et senior, respectivement. La sévérité effective au défaut reste inconnue et fait l’objet d’un stress. |
| Trois CDS 3Y donnent-ils une courbe 7Y ? | Ils identifient une intensité par banque jusqu’à la maturité de chaque contrat. Au-delà, varier l’intensité terminale mesure la dispersion de projections, sans observation de prix supplémentaire. |
| D’où vient l’OIS ? | Médianes de transactions spot EUR sur €STR composé. Bootstrap de facteurs avec ACT/360, TARGET et lag supposé. Les quatre courbes sont recalculées avec un pricer indépendant. |
| Pourquoi la BCE AAA est-elle un proxy ? | La courbe provient de prix d’obligations souveraines sélectionnées par notation. Un OIS provient de swaps contre €STR composé. Les sources reflètent des instruments et des primes différents. |
| Que faudrait-il pour une vraie grille multi-maturités ? | Plusieurs spreads ou upfront signés par banque et par date, même rang, clauses compatibles, conventions de conversion et actualisation connues. |
