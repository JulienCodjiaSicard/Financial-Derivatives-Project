"""Génération de resultats_fx/A_LIRE.md : sources, hypothèses, résultats, limites.

Le document est produit à chaque exécution de scripts/run_all.py à partir des objets
calculés : les chiffres qu'il contient sont toujours ceux des fichiers de resultats_fx/.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping, Sequence

from fx.config import ContexteValo
from fx.courbes import CourbesFX
from fx.digitales import ResultatCoupon
from fx.historique import AnalyseHistorique
from fx.sensibilites import Sensibilites, entetes
from fx.smile import SurfaceVol
from fx.vol_effective import VolEffective, racines_sigma


def nombre(x: float, decimales: int = 0) -> str:
    """Format français : espace pour les milliers, virgule décimale."""
    return f"{x:,.{decimales}f}".replace(",", " ").replace(".", ",")


def pct(x: float, decimales: int = 2) -> str:
    """x en décimal → '12,34 %'."""
    return f"{nombre(100 * x, decimales)} %"


def jj_mm_aaaa(d: date) -> str:
    return d.strftime("%d/%m/%Y")


@dataclass(frozen=True)
class EntreesRapport:
    contexte: ContexteValo
    courbes: CourbesFX
    surface: SurfaceVol
    resultats: Sequence[ResultatCoupon]
    sensibilites: Sensibilites
    scenarios_bf: Mapping[float, float]  # BF supposé (pt) → valeur totale (EUR)
    vols_effectives: Sequence[VolEffective]
    avertissements_vol_eff: Sequence[str]
    analyse: AnalyseHistorique
    date_generation: date
    # Dates (historique complet, au-delà de la valo) où le fixing BCE égale le spot de référence des vols
    dates_fixing_egal_spot_vols: tuple[date, ...] = ()


LIBELLES_SENSIBILITES = {
    "delta": "Spot EUR/USD +{spot} % (delta)",
    "gamma": "Gamma (variation du delta pour +{spot} %)",
    "vega": "Vols ATM +{vol} point (vega)",
    "vanna": "Vanna ({spot} % × {vol} point)",
    "volga": "Volga ({vol} point)",
    "rr25": "RR25 +{rr} point",
    "bf25": "BF25 +{bf} point",
    "ecart_taux": "Écart de taux USD − EUR +{bp} bp",
    "barriere_k": "Barrière K +{k}",
}


def generer_a_lire(e: EntreesRapport) -> str:
    """Contenu Markdown de A_LIRE.md."""
    ctx, cfg, marche = e.contexte, e.contexte.config, e.contexte.marche
    k = ctx.barriere
    res = e.resultats
    total = {
        "plate": sum(r.valeur_plate for r in res),
        "volk": sum(r.valeur_vol_k for r in res),
        "smile": sum(r.valeur_smile for r in res),
    }
    nb_extrap = sum(r.vol_extrapolee for r in res)
    c = cfg.chocs
    sens = e.sensibilites
    base = float(sens.base.sum())
    L: list[str] = []
    a = L.append

    a("# Composante EUR/USD de la note ECE Bank : résultats\n")
    a(f"Document généré le {jj_mm_aaaa(e.date_generation)} par `python scripts/run_all.py` "
      f"(config `config/valo_{cfg.date_valorisation.isoformat()}.json`). Ne pas modifier à la main : "
      "il est réécrit à chaque exécution.\n")
    a("Périmètre : la seule condition de change du coupon (EUR/USD < K à chaque date). "
      "Les conditions de taux (swap EUR 10 ans < 5 %), de crédit et le rappel anticipé sont traités "
      "par les autres sous-groupes et par le moteur Monte Carlo commun.\n")

    # --- En bref
    a("## En bref\n")
    a(f"- **Barrière K = {nombre(k.niveau, 4)}**, plus haut fixing BCE du {jj_mm_aaaa(k.debut)} au "
      f"{jj_mm_aaaa(k.fin)}, atteint le {jj_mm_aaaa(k.date_pic)}. Spot au {jj_mm_aaaa(cfg.date_valorisation)} : "
      f"{nombre(e.courbes.spot, 4)}, soit {pct(k.niveau / e.courbes.spot - 1)} sous la barrière.")
    a(f"- **Valeur actualisée des 14 coupons FX : {nombre(total['smile'])} EUR avec smile**, contre "
      f"{nombre(total['plate'])} EUR à vol ATM plate (coupon de {pct(cfg.coupon_par_periode, 1)} de "
      f"{nombre(cfg.nominal_eur)} EUR par date, hypothèse à confirmer ; avec 3,5 % par semestre, diviser par deux).")
    a(f"- Probabilité de paiement de la condition de change (mesure EUR, avec smile) : "
      f"{pct(res[0].p_smile, 1)} au premier coupon, {pct(res[1].p_smile, 1)} à 1 an, "
      f"{pct(res[-1].p_smile, 1)} à 7 ans. Le forward EUR/USD franchit K vers 4 ans, poussé par "
      f"l'écart de taux SOFR − €STR (environ {nombre(1e4 * e.courbes.ecart_taux(1.0))} bp à 1 an).")
    a(f"- Le smile retire {nombre(total['plate'] - total['smile'])} EUR "
      f"({pct(total['smile'] / total['plate'] - 1, 1)}) à la valeur à vol plate. "
      f"Injecter σ(K) dans N(−d1) ne corrige presque rien ({'+' if total['volk'] >= total['plate'] else ''}{nombre(total['volk'] - total['plate'], 0)} EUR) : "
      "seule la réplication tient compte de la pente du smile.")
    a(f"- **{nb_extrap} dates sur 14 reposent sur des vols extrapolées** (aucune vol cotée au-delà de 1 an). "
      f"Paramètre de smile le plus influent : le RR, {nombre(sens.total('rr25'))} EUR pour "
      f"+{nombre(100 * c.rr25, 1)} point.")
    a("")

    # --- Sources
    a("## Données et sources\n")
    a("| Donnée | Valeur | Source | Date |")
    a("|---|---|---|---|")
    a(f"| Fixings EUR/USD (barrière) | {k.nb_observations} fixings, K = {nombre(k.niveau, 4)} | BCE, série EXR.D.USD.EUR.SP00.A | "
      f"{jj_mm_aaaa(k.debut)} au {jj_mm_aaaa(k.fin)} |")
    a(f"| Spot | {nombre(marche.spot, 4)} | {marche.source_spot} | {jj_mm_aaaa(marche.date_marche)} |")
    for cb in (marche.courbe_eur, marche.courbe_usd):
        if cb is None:
            continue
        pts = ", ".join(f"{p.pilier} {nombre(p.valeur, 2)}" for p in cb.points)
        a(f"| Courbe {cb.devise} ({cb.indice}), taux de swap en % | {pts} | {cb.source.split(' (')[0]} | "
          f"{jj_mm_aaaa(cfg.date_valorisation)} |")
    vols = ", ".join(f"{p.pilier} {nombre(p.atm_pct, 2)} / {nombre(p.rr25_pct, 2)}" for p in marche.piliers)
    a(f"| Vols ATM / RR25 (points de vol) | {vols} | {marche.source_vols} | **inconnue** (voir questions ouvertes) |")
    a(f"| Historique long EUR/USD | {e.analyse.nb_fixings} fixings utilisés | Portail de données BCE "
      f"(`{cfg.chemin_historique.name}`) | {jj_mm_aaaa(e.analyse.debut)} au {jj_mm_aaaa(e.analyse.fin)} |")
    a("")
    a("Butterflies : aucun disponible. Vols au-delà de 1 an : aucune. Ces manques sont couverts par des "
      "hypothèses explicites (ci-dessous) ; le chargement échoue si une donnée manque sans hypothèse déclarée.\n")

    # --- Hypothèses
    a("## Hypothèses\n")
    a("Déclarées dans la config (`\"hypothese\": true`, avec justification) :\n")
    for h in cfg.hypotheses:
        a(f"- **{h.nom}** = `{h.valeur}` : {h.justification}")
    a("")
    a("Choix de méthode :\n")
    a("- Courbes : bootstrap des swaps OIS au pair (jambe fixe annuelle ACT/360), DF interpolés en log-linéaire, "
      "taux de swap des échéances annuelles non cotées (4, 6, 8, 9 ans) interpolés linéairement.")
    a(f"- Smile : piliers σ_25P/C = ATM + BF ∓ RR/2 ; interpolation en strike par {'Vanna-Volga' if cfg.methode_smile == 'vanna_volga' else 'SABR'} "
      f"d'ordre {cfg.ordre_vanna_volga} (Castagna-Mercurio), SABR β = {nombre(cfg.sabr_beta, 1)} en comparaison ; "
      "interpolation en temps sur la variance totale à delta constant.")
    a("- Conventions EUR/USD : delta spot jusqu'à 1 an, delta forward au-delà, non ajusté de la prime ; ATM delta-neutre. "
      "Après 1 an, le delta forward tenu constant est celui du pilier 1Y (0,25 / DF_EUR(1Y) ≈ "
      f"{nombre(e.surface.convention_et_delta(1.5)[1], 3)}), pour éviter un saut du smile à 1 an.")
    a("- Coupon par réplication : AoN(K) = K · ∂P/∂K − P(K), P pricé à σ(K), dérivée centrée de pas relatif 1e-4 "
      "améliorée par extrapolation de Richardson.")
    a("- Sensibilités : chocs centrés ; vols inchangées à delta constant quand le spot bouge ; "
      "écart de taux choqué via les taux zéro USD.")
    a("")

    # --- Résultats
    a("## Les 14 coupons FX\n")
    a("| n° | Date | T | Forward | σ ATM | σ(K) | P plate | P smile | Valeur plate (EUR) | Valeur smile (EUR) |")
    a("|---|---|---|---|---|---|---|---|---|---|")
    for r in res:
        etoile = "*" if r.vol_extrapolee else ""
        a(f"| {r.numero} | {jj_mm_aaaa(r.date)} | {nombre(r.t, 2)} | {nombre(r.forward, 4)} | "
          f"{pct(r.vol_atm)}{etoile} | {pct(r.vol_k)}{etoile} | {pct(r.p_plate)} | {pct(r.p_smile)} | "
          f"{nombre(r.valeur_plate)} | {nombre(r.valeur_smile)} |")
    a(f"| **Total** | | | | | | | | {nombre(total['plate'])} | **{nombre(total['smile'])}** |")
    a("\n\\* vols extrapolées (vol constante au-delà du dernier pilier coté).\n")

    a("## Sensibilités de la valeur totale\n")
    a(f"Valeur de base : {nombre(base)} EUR. Résultats en EUR, chocs centrés rapportés pour le choc +.\n")
    a("| Choc | Effet (EUR) | En % de la valeur |")
    a("|---|---|---|")
    fmt = {
        "spot": nombre(100 * c.spot_rel, 0), "vol": nombre(100 * c.vol_atm, 0), "rr": nombre(100 * c.rr25, 1),
        "bf": nombre(100 * c.bf25, 1), "bp": nombre(1e4 * c.ecart_taux, 0), "k": nombre(c.barriere_k, 2),
    }
    for nom, lib in LIBELLES_SENSIBILITES.items():
        v = sens.total(nom)
        a(f"| {lib.format(**fmt)} | {nombre(v)} | {pct(v / base, 1)} |")
    for bf, v in sorted(e.scenarios_bf.items()):
        a(f"| Scénario : BF supposé = {nombre(bf, 2)} point | {nombre(v - base)} | {pct(v / base - 1, 1)} |")
    a("\nDétail par date : `sensibilites_fx.csv` (colonnes : " + ", ".join(entetes(cfg).values()) + ").\n")

    veff = e.vols_effectives
    # Exemple des deux racines de σ_eff à la première date où le forward dépasse K
    ex = next((v for v in veff if v.forward > k.niveau), veff[-1])
    racines = racines_sigma(e.courbes.spot, k.niveau, ex.t, ex.df_usd, ex.df_eur, ex.p_smile)
    a("## Paramètres pour le moteur Monte Carlo commun\n")
    a("`export_mc.json` livre au moteur une **barrière effective par date** (bloc `pour_pricer_note_ece`) :\n")
    a("- `fx_spot`, `fx_vol_tenors` (les 14 dates de coupon) et `fx_atm_vols` : vols ATM de marché à ces dates ;")
    a(f"- `fx_barrier` : barrière K_eff(T_i) qui, à la vol ATM, redonne la probabilité avec smile, de "
      f"{nombre(veff[0].k_effective_vol_atm, 4)} au premier coupon à {nombre(veff[-1].k_effective_vol_atm, 4)} à 7 ans. "
      "Elle est unique, car la probabilité croît strictement avec K ;")
    a("- `usd_ois` au format `Curve(tenors, zeros)` du moteur : maturités en années, taux zéro continus en décimal "
      "(interpolés linéairement par le moteur). Les dates de coupon sont incluses, donc le moteur retrouve exactement "
      "nos facteurs d'actualisation USD à ces dates. La courbe €STR utilisée ici est jointe au même format, pour "
      "comparaison avec `eur_ois`.\n")
    a("**Changement d'une ligne dans `pricer_note_ece.py`**, à demander au sous-groupe moteur : comparer à la barrière "
      "de chaque date au lieu d'un K unique, `ok_fx = sim[\"fx\"] < np.asarray(terms.fx_barrier).reshape(-1, 1)`.\n")
    a(f"**Test de contrôle** (bloc `controle_moteur`) : faire tourner le moteur sur la seule condition de change ; il doit "
      f"retrouver les probabilités avec smile à {nombre(100 * cfg.tolerance_controle_moteur, 0)} point près (le moteur "
      "simule des taux EUR stochastiques, supposés déterministes ici ; un écart plus grand signalerait un problème de "
      "convention). Une imitation du moteur à partir du seul fichier d'export retrouve ces probabilités à 0,2 point près "
      "(test `test_moteur_simule_retrouve_les_probabilites_avec_smile`).\n")
    a("**Pourquoi pas une vol effective σ_eff.** L'équation N(−d1(σ)) = P_smile a deux racines dès que le forward "
      f"dépasse K, et aucune n'est réaliste (à {nombre(ex.t, 0)} ans : "
      f"{' ou '.join(pct(r, 1) for r in sorted(racines, reverse=True))}, pour une vol ATM de {pct(ex.vol_atm, 1)}). "
      "Quand F est proche de K, P ≈ N(−σ√T/2) : descendre nettement sous 50 % exige une vol extrême. "
      f"σ_eff n'est donc reporté qu'à titre d'information jusqu'à {nombre(cfg.sigma_eff_max_annees, 0)} ans "
      "(`sigma_eff_information`) et n'est pas livré au moteur.")
    if e.avertissements_vol_eff:
        a("\nAvertissements :\n")
        for w in e.avertissements_vol_eff:
            a(f"- {w}")
    a("")

    an = e.analyse
    a("## Vue de l'investisseur : analyse historique\n")
    a(f"Fréquence, sur les fixings BCE du {jj_mm_aaaa(an.debut)} au {jj_mm_aaaa(an.fin)}, des hausses de l'EUR/USD "
      f"restées sous l'écart spot-barrière actuel (+{pct(an.seuil)}), en fenêtres glissantes quotidiennes.\n")
    sp = an.debut_sous_periode.year
    a(f"| Horizon | Historique {an.debut.year}–{an.fin.year} | Périodes indépendantes | Depuis {sp} | Risque-neutre (smile) |")
    a("|---|---|---|---|---|")
    for l in an.lignes:
        if l.mois in (6, 12, 24, 36, 60, 84):
            a(f"| {l.mois} mois | {pct(l.complet.p_empirique_coupon, 1)} | {l.complet.nb_independantes} | "
              f"{pct(l.sous_periode.p_empirique_coupon, 1)} | {pct(l.p_risque_neutre_smile, 1)} |")
    longs = [l.complet.p_empirique_coupon for l in an.lignes if l.mois > 12]
    a(f"\nJusqu'à 1 an, historique et risque-neutre sont proches. Au-delà, l'historique reste entre "
      f"{nombre(100 * min(longs))} et {nombre(100 * max(longs))} % "
      "(pas de tendance de l'EUR/USD sur la période) quand le risque-neutre baisse avec le forward. "
      "Ce n'est pas une erreur de prix : la probabilité risque-neutre intègre l'écart de taux et le smile. "
      "Aux longs horizons, très peu de périodes indépendantes : les fréquences sont peu fiables.\n")
    a("| Vol | Réalisée | Implicite ATM |")
    a("|---|---|---|")
    for p, v in an.vols_realisees.items():
        imp = an.vols_implicites.get(p)
        a(f"| {p} | {pct(v)} | {pct(imp) if imp is not None else 'n.d.'} |")
    a(f"| {an.debut.year}–{an.fin.year} | {pct(an.vol_realisee_periode_complete)} | |")
    a("\nLa vol implicite dépasse la vol réalisée récente (prime de volatilité habituelle).\n")

    # --- Fichiers
    a("## Fichiers produits\n")
    for nom, desc in (
        ("coupons_fx.csv", "probabilités (plate, σ(K) injectée, smile) et valeurs des 14 coupons"),
        ("coupons_fx_probabilites.svg", "probabilité de paiement en fonction de la maturité"),
        ("sensibilites_fx.csv", "sensibilités par date et au total"),
        ("export_mc.json", "paramètres pour le moteur Monte Carlo commun (barrière effective par date, usd_ois) et test de contrôle"),
        ("historique_barriere_fx.csv / .svg", "analyse historique par horizon"),
        ("vol_historique_vs_implicite_fx.csv", "vol réalisée et vol implicite"),
        ("A_LIRE.md", "ce document"),
    ):
        a(f"- `{nom}` : {desc}.")
    a("\nLes fichiers numérotés `01_` à `04_` et `A_LIRE.txt` viennent du script initial `pricing_fx.py` "
      "(taux EUR et USD à 3 %, vol plate, 2 dates) et sont conservés pour mémoire.\n")

    # --- Limites
    a("## Limites\n")
    date_vols = f"Date de cotation des vols ({marche.source_vols}) inconnue"
    if marche.spot_reference_vols is not None:
        date_vols += (f" ; leur spot de référence ({nombre(marche.spot_reference_vols, 4)}) diffère du spot du "
                      f"{jj_mm_aaaa(cfg.date_valorisation)} ({nombre(marche.spot, 4)})")
        if e.dates_fixing_egal_spot_vols:
            date_vols += (" et égale le fixing BCE du " + ", ".join(jj_mm_aaaa(d) for d in e.dates_fixing_egal_spot_vols)
                          + " : les vols datent très probablement de ce jour")
        date_vols += (f", le spot ayant reculé de {nombre(100 * abs(marche.spot_reference_vols / marche.spot - 1), 1)} % "
                      "entre les deux dates. À garder tant qu'il n'y a pas de vols à la date de valorisation")
    date_vols += "."
    for lim in (
        f"**Vols au-delà de 1 an extrapolées** à vol constante (ATM, RR et BF du pilier 1Y) : {nb_extrap} dates sur 14. "
        f"**Le risk reversal long est la donnée qui manque le plus** (environ "
        f"{nombre(sens.total('rr25') / (100 * c.rr25) / 1e6, 2)} M EUR par point de RR, contre "
        f"{nombre(sens.total('bf25') / (100 * c.bf25) / 1e6, 2)} M EUR par point de butterfly) : "
        "à chercher en priorité (Bloomberg OVDV).",
        f"**Butterflies supposés** ({nombre(cfg.hypotheses_marche.bf25_pct or 0, 2)} point) ; effet limité au total "
        "(voir scénarios), plus marqué sur le premier coupon.",
        "BF coté traité comme un smile strangle alors que le marché cote un market strangle (écart faible ici, |RR| < 1 point).",
        "Approximation Vanna-Volga de Castagna-Mercurio (SABR donne moins de 0,02 point d'écart en K).",
        "Taux USD et EUR déterministes ; corrélation taux/change non modélisée ici (traitée, le cas échéant, par le moteur commun).",
        "Courbe unique par devise (pas de base cross-currency) ; swaps supposés démarrer à la date de valorisation (pas de T+2).",
        date_vols,
        "K à confirmer : fixing BCE retenu ; un plus haut intrajournalier donnerait un K plus élevé "
        f"(chaque +{nombre(c.barriere_k, 2)} sur K vaut {nombre(sens.total('barriere_k'))} EUR).",
        "Vol effective σ_eff mal posée pour ce produit au-delà d'environ 2 ans (deux racines, aucune réaliste) : "
        "non livrée au moteur, remplacée par la barrière effective.",
        "Analyse historique en fenêtres très chevauchantes : peu de périodes indépendantes au-delà de 3 ans.",
    ):
        a(f"- {lim}")
    a("")

    # --- Questions
    a("## Questions ouvertes\n")
    for q in (
        "Coupon : 7 % par an (3,5 % par semestre) ou 7 % par semestre ? (valeurs calculées avec "
        f"{pct(cfg.coupon_par_periode, 1)} par date)",
        "K : fixing BCE ou plus haut intrajournalier ?",
        "Date de valorisation commune à tout le groupe (ici le 06/11/2026, fin de la fenêtre de la barrière).",
        "Source des vols au-delà de 1 an, en priorité les risk reversals, puis les butterflies (Bloomberg OVDV si "
        "l'école y donne accès) ; vols à la date de valorisation plutôt qu'au 19/11.",
        "Sous-groupe moteur : accepter une barrière par date (changement d'une ligne ci-dessus), faire tourner le test "
        "de contrôle, et vérifier que `eur_ois` du moteur correspond à la courbe €STR utilisée ici.",
    ):
        a(f"- {q}")
    a("")

    for w in marche.avertissements:
        a(f"> Avertissement au chargement : {w}")
    return "\n".join(L).rstrip() + "\n"
