# Guide de soutenance · Crédit ECE ING5 2026

## Parcours à l’écran

Depuis la racine, lancer `.\.venv312\Scripts\python.exe -m streamlit run app.py`. Sélectionner **PUBLIC**, puis **Transactions CDS observées**, **2026-05-20**, **€STR OIS · DTCC/CFTC** et **Subordonné**. Les valeurs de référence sont déjà en cache. Laisser le multiplicateur de prolongement sur **1 ×** et la convention sur **7 % annuel** pour retrouver les chiffres ci-dessous.

| Onglet | Action à l’écran | Lecture à voix haute |
|---|---|---|
| Calibration CDS | Montrer **Repricing des spreads traités** puis **Équilibre des jambes du CDS** et **Type de spread, qualité et collecte CDS**. | 51,85, 54,60 et 63,75 pb. Les PV de protection et de prime coïncident par nominal unitaire. Le graphe taux présente l’échéance en années ACT/365F et le taux zéro équivalent continu. Le coupon contractuel du ticket est généralement de 100 pb, distinct de l’équivalent par utilisé pour le calcul. |
| Premier défaut & corrélation | Pointer la ligne au pilier exact, les trois prolongements et **Modèles stochastiques du panier retenu**. | 6,34 % au pilier, environ 13,80 % à 7 ans en central. Comparer les colonnes 0, 1, 2 et 3 défauts, le pas et l’erreur-type. En DEMO, le graphique vertical donne la survie en fonction du temps en années. |
| Risque, stress & backtest | Ouvrir **Chocs de −50 à +100 pb, recouvrement et prolongement du panier retenu** et descendre au graphique de l’historique. | À 7 ans, +25 pb donne 19,25 % de PD de premier défaut. Les points de l’historique sont des transactions, abscisse en dates, ordonnée en pb, couleur par banque et symbole par rang. |
| Condition de crédit du coupon | Repérer les 14 lignes, ouvrir **Survie et PD par banque**, cocher **Comparer avec un choc de spreads de +25 pb**, puis basculer vers **7 % par semestre**. | Le graphe donne les années en abscisse et la probabilité que toutes les banques soient vivantes en ordonnée. Au semestre 14, 86,21 % et 3,02 par 100 à 7 % annuel, ou 6,03 à 7 % par semestre. Le choc abaisse la courbe. Les deux boutons exportent les résultats. |

Pour le contrôle proche de cinq ans, sélectionner **2026-06-25** et **Senior** dans la même étude PUBLIC avec €STR OIS. BNP Paribas affiche 39,75 pb à 4,989 ans, une intensité de 0,6605 % par an et une survie de 96,76 %. Pour démontrer un bootstrap multi-maturités et le replay régulier, sélectionner **DEMO**. Dans ce mode les abscisses des graphes du backtest sont les dates d’observation et les ordonnées donnent la PD du panier, les intensités, les survies ou les résidus en pb.

## Déroulé 3 minutes

- **0:00–0:45.** Expliquer la condition Crédit et le choix de trois banques systémiques sur une seule date, dans le même rang et la même devise.
- **0:45–1:30.** Lire les trois spreads et montrer la balance prime–protection. Préciser la convention académique et le recouvrement.
- **1:30–2:15.** Expliquer le premier défaut et annoncer 6,34 % à 3,09 ans, puis le résultat central à sept ans avec sa fourchette.
- **2:15–3:00.** Afficher le coupon et ses 14 dates. Donner 3,02 par 100 au quatorzième semestre et montrer l’effet d’un stress de +25 pb.

## Déroulé 8 minutes

- **0:00–1:15.** Poser le besoin de Pricing et la règle d’état. Montrer Société Générale G-SIB et UniCredit et Commerzbank O-SII, puis les UPI et le rang.
- **1:15–3:00.** Décrire le rapprochement Tradeweb–DTCC, les jambes, l’accrual au défaut et l’option B. Reprendre séparément le contrôle BNP senior.
- **3:00–4:45.** Relier intensité, survie et PD, puis décrire les facteurs Cox lognormal et CIR++. Montrer l’ambiguïté du premier défaut lorsque les défauts se regroupent.
- **4:45–6:15.** Afficher les stresses et l’historique discontinu. Expliquer le manque de recouvrement en dates et l’absence d’options individuelles exploitables.
- **6:15–8:00.** Afficher le coupon, changer une convention, exporter le CSV et montrer le masque remis à Pricing.

## Déroulé 15 minutes

- **0:00–2:00.** Positionner le module dans le projet et présenter les règles contractuelles utiles à Crédit, la définition des entités et le choix du snapshot.
- **2:00–5:00.** Détailler la dette subordonnée, la source de l’actualisation, les flux CDS, le bootstrap et le contrôle indépendant. Expliquer l’écart conventionnel de QuantLib et le sens du contrôle BNP.
- **5:00–8:00.** Déduire les marginales à 1–7 ans et la survie sous indépendance. Montrer la sensibilité à l’intensité terminale.
- **8:00–10:30.** Montrer le facteur commun, les seuils exponentiels indépendants, le recalage, les distributions de défaut et la convergence en pas et en nombre de trajectoires.
- **10:30–12:30.** Explorer le replay réel, sa limite temporelle, la comparaison avec DEMO et les chocs de spread et de recouvrement.
- **12:30–15:00.** Montrer les 14 dates, les conventions de 7 %, le stress +25 pb et l’export utilisé par Pricing. Finir avec les données manquantes et la part extrapolée.

## Fiche de formules

- Intensité intégrée $H_i(t)=\int_0^t\lambda_i(u)\,du$, survie $S_i(t)=e^{-H_i(t)}$, PD cumulée $1-S_i(t)$.
- Entre $t_{j-1}$ et $t_j$, PD conditionnelle sachant la banque vivante au début $1-S_i(t_j)/S_i(t_{j-1})$.
- Jambe protection $(1-R)\int_0^T D(t)\lambda(t)S(t)\,dt$. Jambe prime $sA$, où $A=\sum_j\Delta_j D(t_j)S(t_j)+A_{\mathrm{accrual}}$. Spread équivalent par $s=\mathrm{PV}_{\mathrm{prot}}/A$.
- Benchmark rapide $\lambda\simeq s/(1-R)$ avec $s$ en fraction. La calibration du pricer met les deux jambes à égalité.
- Sous indépendance, $S_{\mathrm{panier}}(t)=\prod_i S_i(t)$. Le premier défaut est $\min_i \tau_i$.
- Sous Cox, $\tau_i=\inf\{t:H_i(t)\ge E_i\}$ avec des seuils $E_i\sim\mathrm{Exp}(1)$ indépendants. La dynamique commune fait dépendre les intégrales $H_i$.
- Cox lognormal $\lambda_i(t)=a_i(t)e^{\beta X_t}$ avec $dX_t=-\kappa X_t\,dt+\sigma\,dW_t$. CIR++ $\lambda_i(t)=Y_t+\phi_i(t)$ avec $dY_t=\kappa(\theta-Y_t)dt+\sigma\sqrt{Y_t}\,dW_t$. Le décalage est contrôlé pour préserver des intensités non négatives.
- À $t_j=j/2$, masque $\mathbf{1}_{\{\min_i\tau_i>t_j\}}$. Le benchmark de coupon annuel par 100 vaut $100\times0{,}07\times0{,}5\times\mathbb P(\min_i \tau_i>t_j)$. Le facteur 0,5 disparaît si le taux s’applique chaque semestre.
- Pour une fréquence simulée $\hat p$ sur $M$ trajectoires indépendantes, ordre de grandeur de l’erreur-type $\sqrt{\hat p(1-\hat p)/M}$.

## Questions à préparer

**Pourquoi ces trois banques ?** Elles ont une désignation officielle antérieure au snapshot et partagent devise, date, rang et maturité. Les autres candidats sont répertoriés dans le registre.

**Un ticket suffit-il pour prévoir la PD à 7 ans ?** Il identifie un niveau d’intensité dans l’hypothèse constante avant son échéance. À sept ans, les trois prolongements donnent 10,14 %, 13,80 % et 17,30 % sous indépendance.

**Pourquoi un résidu quasi nul et une réserve sur le prix ?** L’équation du pricer est résolue et vérifiée par une autre intégration. La conversion exacte du spread de clôture par Tradeweb, l’upfront signé et le notionnel économique ne sont pas documentés.

**Le modèle stochastique est-il calibré ?** Les survies marginales servent de cibles. Les paramètres de mouvement et de dépendance sont testés par scénario faute de séries et d’options individuelles suffisantes.

**Que change le chargement systémique ?** À marginales fixées, les défauts peuvent se regrouper. Les scénarios lognormaux donnent de 0,69 % à 0,81 % de probabilité de deux défauts à sept ans. Les différences observées de premier défaut sont petites devant l’erreur-type de simulation.

**Que montre le replay ?** La cohérence interne des recalibrages à chaque observation documentée. Avec un seul jour de panier complet, on ne peut pas mesurer la performance prédictive de marché.

**Comment traiter un défaut exactement à la date du coupon ?** La condition est stricte. Le booléen vaut faux à partir du défaut et pour tous les semestres suivants.

**Le produit exige-t-il un seuil de PD ?** Le payoff de groupe, page 3 de Projet.pdf, décrit la réalisation du défaut. Le taux de 7 % est illustratif, avec convention annuelle ou semestrielle choisie dans l’onglet.
