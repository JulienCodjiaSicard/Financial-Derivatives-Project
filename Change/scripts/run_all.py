"""Lance toute la chaîne EUR/USD et régénère resultats_fx/ en une commande.

Étapes : charge la config, calcule K, vérifie que les données de marché sont complètes
(code de sortie 1 sinon), construit les courbes EUR / USD et le smile, valorise les 14
coupons FX, calcule les sensibilités, la vol effective exportée vers le moteur Monte
Carlo et l'analyse historique de la barrière, puis écrit A_LIRE.md.

Fichiers écrits dans resultats_fx/ : coupons_fx.csv, coupons_fx_probabilites.svg,
sensibilites_fx.csv, export_mc.json, historique_barriere_fx.csv / .svg,
vol_historique_vs_implicite_fx.csv, correlation_taux_change.csv (si la config a une section
correlation_taux_change), A_LIRE.md.

Exécution : python scripts/run_all.py [chemin_config]
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from fx.config import (  # noqa: E402
    DonneesManquantes,
    calcul_barriere,
    charger_config,
    charger_contexte,
    echeancier_coupons,
    lire_fixings_bce,
)
from fx.correlation import calculer_correlations  # noqa: E402
from fx.correlation import ecrire_csv as ecrire_csv_correlation  # noqa: E402
from fx.courbes import construire_courbes  # noqa: E402
from fx.digitales import ecrire_csv, tracer_probabilites, valoriser_coupons  # noqa: E402
from fx.sensibilites import NOMS as NOMS_SENSIBILITES  # noqa: E402
from fx.sensibilites import calculer_sensibilites, scenario_bf  # noqa: E402
from fx.sensibilites import ecrire_csv as ecrire_csv_sensibilites  # noqa: E402
from fx.sensibilites import entetes as entetes_sensibilites  # noqa: E402
from fx.export_mc import construire_export, ecrire_export  # noqa: E402
from fx.historique import analyser, lire_historique_sdmx  # noqa: E402
from fx.historique import ecrire_csv as ecrire_csv_historique  # noqa: E402
from fx.historique import tracer as tracer_historique  # noqa: E402
from fx.rapport import EntreesRapport, generer_a_lire  # noqa: E402
from fx.smile import construire_surface  # noqa: E402
from fx.vol_effective import vols_effectives  # noqa: E402

CONFIG_DEFAUT = RACINE / "config" / "valo_2026-11-13.json"


def main(argv: list[str]) -> int:
    # Console Windows : forcer l'UTF-8 pour afficher €, Δ, etc. même en sortie redirigée.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    chemin_config = Path(argv[1]) if len(argv) > 1 else CONFIG_DEFAUT
    config = charger_config(chemin_config)
    fixings = lire_fixings_bce(config.chemin_fixings)
    k = calcul_barriere(fixings, config.debut_barriere, config.fin_barriere)

    print(f"Date de valorisation : {config.date_valorisation}")
    print(
        f"Barrière K = {k.niveau:.4f} (pic le {k.date_pic}, {k.nb_observations} fixings "
        f"du {k.debut} au {k.fin}, {config.source_barriere})"
    )
    print(f"Coupon par période : {config.coupon_par_periode:.2%} de {config.nominal_eur:,.0f} EUR")
    print("Échéancier :", ", ".join(f"{c.date} (T={c.t_annees:.4f})" for c in echeancier_coupons(config)))
    print("Hypothèses déclarées :")
    for h in config.hypotheses:
        print(f"  - {h.nom} = {h.valeur!r} : {h.justification}")

    try:
        contexte = charger_contexte(chemin_config)
    except DonneesManquantes as e:
        print(f"\nCHARGEMENT DU MARCHÉ IMPOSSIBLE\n{e}")
        return 1
    for a in contexte.marche.avertissements:
        print(f"AVERTISSEMENT : {a}")
    print("Hypothèses de marché utilisées :", "; ".join(contexte.marche.hypotheses_utilisees) or "aucune")

    # Étape 2 : courbes et forward aux dates de coupon.
    courbes = construire_courbes(contexte.marche, config)
    for c in (courbes.eur, courbes.usd):
        print(f"Courbe {c.devise} : {len(c.temps) - 1} nœuds jusqu'à {c.t_max:.2f} ans ; source : {c.source} ; "
              f"taux de swap interpolés : {', '.join(c.piliers_interpoles) or 'aucun'}")
    print(f"\n{'n°':>3} {'date':>10} {'T':>7} {'DF EUR':>8} {'DF USD':>8} {'r_EUR':>6} {'r_USD':>6} {'écart':>6} {'forward':>7}")
    for c in echeancier_coupons(config):
        t = c.t_annees
        print(
            f"{c.numero:>3} {c.date} {t:7.4f} {courbes.eur.df(t):8.5f} {courbes.usd.df(t):8.5f} "
            f"{100 * courbes.eur.taux_zero(t):5.2f}% {100 * courbes.usd.taux_zero(t):5.2f}% "
            f"{1e4 * courbes.ecart_taux(t):4.0f}bp {courbes.forward(t):7.4f}"
        )

    # Étape 4 : smile aux dates de coupon et vol au strike K selon la méthode.
    surface = construire_surface(contexte.marche, courbes, config)
    k = contexte.barriere.niveau
    print(f"\nSmile (vols en %) ; méthode retenue : {config.methode_smile}, ordre {config.ordre_vanna_volga} ; * = vols extrapolées")
    print(f"{'n°':>3} {'T':>7} {'conv':>7} {'K25P':>7} {'KATM':>7} {'K25C':>7} {'σ25P':>6} {'σATM':>6} {'σ25C':>6} "
          f"{'VV1(K)':>7} {'VV2(K)':>7} {'SABR(K)':>7}")
    for c in echeancier_coupons(config):
        t = c.t_annees
        sm = surface.smile(t)
        print(
            f"{c.numero:>3} {t:7.4f} {sm.convention:>7} {sm.strikes[0]:7.4f} {sm.strikes[1]:7.4f} {sm.strikes[2]:7.4f} "
            + " ".join(f"{100 * v:6.3f}" for v in sm.vols.comme_tuple())
            + f" {100 * float(sm.vol_vv1(k)):7.3f} {100 * float(sm.vol_vv2(k)):7.3f} {100 * float(sm.vol_sabr(k)):7.3f}"
            + (" *" if surface.est_extrapole(t) else "")
        )

    # Étape 5 : les 14 coupons digitaux.
    resultats = valoriser_coupons(config, courbes, surface, contexte.barriere)
    sortie = config.dossier_resultats
    ecrire_csv(resultats, sortie / "coupons_fx.csv")
    tracer_probabilites(resultats, k, sortie / "coupons_fx_probabilites.svg", surface.t_dernier_pilier)
    print(f"\nCoupons FX : {config.coupon_par_periode:.2%} × {config.nominal_eur:,.0f} EUR par date (hypothèse)")
    print(f"{'n°':>3} {'date':>10} {'P plate':>8} {'P σ(K)':>8} {'P smile':>8} {'valeur smile EUR':>17}")
    for r in resultats:
        print(f"{r.numero:>3} {r.date} {100 * r.p_plate:7.2f}% {100 * r.p_vol_k:7.2f}% {100 * r.p_smile:7.2f}% {r.valeur_smile:17,.0f}"
              + (" *" if r.vol_extrapolee else ""))
    print(f"{'total':>14} {sum(r.valeur_plate for r in resultats):>34,.0f} (plate) ; "
          f"{sum(r.valeur_vol_k for r in resultats):,.0f} (σ(K) injectée) ; {sum(r.valeur_smile for r in resultats):,.0f} (smile)")
    print(f"Écrit : {sortie / 'coupons_fx.csv'} et {sortie / 'coupons_fx_probabilites.svg'}")

    # Étape 6 : sensibilités (EUR, total des 14 coupons).
    sens = calculer_sensibilites(config, surface, k)
    ecrire_csv_sensibilites(config, sens, sortie / "sensibilites_fx.csv")
    libelles = entetes_sensibilites(config)
    print(f"\nSensibilités de la valeur totale ({float(sens.base.sum()):,.0f} EUR) :")
    for nom in NOMS_SENSIBILITES:
        print(f"  {libelles[nom]:<34} {sens.total(nom):>14,.0f}")
    plage = config.hypotheses_marche.bf25_plage_pct
    scenarios_bf: dict[float, float] = {}
    if plage and any(p.bf25_suppose for p in contexte.marche.piliers):
        for bf in plage:
            scenarios_bf[bf] = float(scenario_bf(config, contexte.marche, courbes, k, bf).sum())
            print(f"  scénario BF supposé = {bf:.2f} pt : {scenarios_bf[bf]:,.0f} EUR "
                  f"({scenarios_bf[bf] - float(sens.base.sum()):+,.0f})")
    print(f"Écrit : {sortie / 'sensibilites_fx.csv'}")

    # Étape 7 : corrélation taux/change, vol effective et export vers le moteur Monte Carlo commun.
    historique_long = lire_historique_sdmx(config.chemin_historique)
    correlation = None
    if config.correlation is not None:
        correlation = calculer_correlations(config.correlation, historique_long, config.date_valorisation)
        ecrire_csv_correlation(correlation, sortie / "correlation_taux_change.csv")
        print("\nCorrélation ρ = corr(Δ taux EUR, Δ ln X), X = EUR pour 1 USD :")
        print(f"{'série':<14} {'fenêtre':>7} {'pas':>4} {'n':>5} {'ρ':>7} {'IC 95 %':>17}")
        for e in correlation.estimations:
            print(f"{e.serie:<14} {e.fenetre_annees:>5} a {e.pas_jours:>3}j {e.nb_variations:>5} {e.rho:7.3f} "
                  f"[{e.ic95_bas:6.3f} ; {e.ic95_haut:6.3f}]" + ("  <- retenue" if e is correlation.retenue else ""))
        for f in correlation.fenetres_non_couvertes:
            print(f"  non couverte : {f}")
    vols_eff, avert_eff = vols_effectives(resultats, courbes, k)
    export = construire_export(config, contexte.marche, courbes, contexte.barriere, vols_eff, avert_eff, correlation)
    ecrire_export(export, sortie / "export_mc.json")
    print("\nLivré au moteur MC : barrière effective K_eff (à vol ATM, redonne P smile) ; σ_eff pour information seulement")
    print(f"{'n°':>3} {'T':>7} {'σ ATM':>7} {'P smile':>8} {'K_eff':>8} {'σ_eff (info)':>13}")
    for v in vols_eff:
        info = f"{100 * v.sigma_eff:11.2f} %" if v.t <= config.sigma_eff_max_annees else f"{'—':>13}"
        print(f"{v.numero:>3} {v.t:7.4f} {100 * v.vol_atm:6.3f}% {100 * v.p_smile:7.2f}% {v.k_effective_vol_atm:8.4f} {info}")
    for a in avert_eff:
        print(f"AVERTISSEMENT : {a}")
    print(f"Écrit : {sortie / 'export_mc.json'}")

    # Étape 8 : analyse historique de la barrière (fixings BCE jusqu'à la date de valorisation).
    analyse = analyser(
        historique_long, config.date_valorisation, courbes.spot, k, resultats,
        config.sous_periode_annees, config.vol_realisee_jours, config.jours_ouvres_par_an,
        {p.pilier: p.atm_pct / 100 for p in contexte.marche.piliers},
    )
    c1, c2 = ecrire_csv_historique(analyse, sortie)
    tracer_historique(analyse, sortie / "historique_barriere_fx.svg")
    sp = analyse.debut_sous_periode.year
    print(f"\nHistorique BCE {analyse.debut} → {analyse.fin} ({analyse.nb_fixings} fixings) ; "
          f"seuil de hausse K/S − 1 = {100 * analyse.seuil:.2f} %")
    print(f"{'horizon':>8} {'P emp. complet':>14} {'indép.':>6} {'P emp. ' + str(sp) + '+':>13} {'indép.':>6} {'P RN smile':>10}")
    for l in analyse.lignes:
        print(f"{l.mois:>6} m {100 * l.complet.p_empirique_coupon:13.1f}% {l.complet.nb_independantes:>6} "
              f"{100 * l.sous_periode.p_empirique_coupon:12.1f}% {l.sous_periode.nb_independantes:>6} {100 * l.p_risque_neutre_smile:9.1f}%")
    print("Vol réalisée vs implicite ATM : " + " ; ".join(
        f"{p} {100 * v:.2f} % vs {100 * analyse.vols_implicites[p]:.2f} %" for p, v in analyse.vols_realisees.items()
        if p in analyse.vols_implicites
    ) + f" ; {analyse.debut.year}-{analyse.fin.year} : {100 * analyse.vol_realisee_periode_complete:.2f} %")
    print(f"Écrit : {c1}, {c2} et {sortie / 'historique_barriere_fx.svg'}")

    # Étape 9 : A_LIRE.md (chiffres repris des objets calculés ci-dessus).
    spot_vols = contexte.marche.spot_reference_vols
    egal_spot_vols = tuple(
        d for d, x in historique_long if spot_vols is not None and abs(x - spot_vols) < 5e-5
        and d > config.date_valorisation
    )
    texte = generer_a_lire(EntreesRapport(
        contexte=contexte, courbes=courbes, surface=surface, resultats=resultats, sensibilites=sens,
        scenarios_bf=scenarios_bf, vols_effectives=vols_eff, avertissements_vol_eff=avert_eff,
        analyse=analyse, date_generation=date.today(), dates_fixing_egal_spot_vols=egal_spot_vols,
        correlation=correlation,
    ))
    (sortie / "A_LIRE.md").write_text(texte, encoding="utf-8")
    print(f"\nÉcrit : {sortie / 'A_LIRE.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
