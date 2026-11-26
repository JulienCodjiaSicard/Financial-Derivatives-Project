"""Export des paramètres FX vers le moteur Monte Carlo commun (resultats_fx/export_mc.json).

Ce qui est livré au moteur (bloc "pour_pricer_note_ece") : la barrière effective.
- fx_spot : spot EUR/USD ;
- fx_vol_tenors, fx_atm_vols : dates de coupon et vols ATM de marché à ces dates (le moteur
  interpole la variance totale entre elles) ;
- fx_barrier : une barrière par date de coupon, K_eff(T_i), telle qu'à la vol ATM
  N(−d1(K_eff)) = P_smile(T_i). Unique, car N(−d1) croît strictement avec K ;
- usd_ois : format Curve(tenors, zeros) du moteur, maturités en années et taux zéro continus
  en décimal (le moteur les interpole linéairement). Les dates de coupon sont ajoutées aux
  nœuds de la courbe pour que le moteur retrouve exactement nos DF USD à ces dates.

- fx_rate_corr (si la config a une section correlation_taux_change) : ρ = corr(Z_r, Z_X) du
  sujet (section 4.3), X = EUR pour 1 USD ; Z_X = ρ Z_r + √(1 − ρ²) Z′. Détail des estimations
  dans le bloc "correlation_taux_change".

Changement à faire dans le moteur (il compare aujourd'hui à un K unique) :
    ok_fx = sim["fx"] < np.asarray(terms.fx_barrier).reshape(-1, 1)

La vol effective σ_eff n'est PAS livrée : quand le forward approche K, l'équation
N(−d1(σ)) = P_smile a deux racines et aucune n'est réaliste (17,8 % ou 0,6 % à 4 ans).
Elle n'est reportée, à titre d'information, que jusqu'à config.sigma_eff_max_annees.

Validité : simulation lognormale de l'EUR/USD sous la mesure risque-neutre EUR, avec les
mêmes courbes EUR et USD (celles du fichier de marché : OIS €STR et SOFR du sous-groupe taux au 13/11/2026). Le moteur simule des taux EUR stochastiques (Hull-White) : un
petit écart sur les probabilités est normal, plus d'un point signalerait un problème de
convention (bloc "controle_moteur").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from fx.config import Barriere, ConfigValo, MarcheFX
from fx.correlation import ResultatCorrelation
from fx.courbes import CourbeActualisation, CourbesFX
from fx.vol_effective import VolEffective

CHANGEMENT_MOTEUR = "ok_fx = sim['fx'] < np.asarray(terms.fx_barrier).reshape(-1, 1)"


def courbe_au_format_moteur(courbe: CourbeActualisation, temps_en_plus: Sequence[float] = ()) -> dict[str, list[float]]:
    """Curve(tenors, zeros) : nœuds de la courbe et temps demandés, taux zéro continus −ln(DF)/T."""
    temps = sorted({round(t, 12) for t in (*[t for t in courbe.temps if t > 0], *temps_en_plus) if t > 0})
    return {"tenors": temps, "zeros": [courbe.taux_zero(t) for t in temps]}


def construire_export(
    config: ConfigValo,
    marche: MarcheFX,
    courbes: CourbesFX,
    barriere: Barriere,
    vols: Sequence[VolEffective],
    avertissements: Sequence[str],
    correlation: ResultatCorrelation | None = None,
) -> dict[str, Any]:
    """Dictionnaire JSON de l'export (fonction pure)."""
    t_coupons = [v.t for v in vols]
    hors_horizon = [v.numero for v in vols if v.t > config.sigma_eff_max_annees]
    avert_sigma = (
        [f"σ_eff non reporté au-delà de {config.sigma_eff_max_annees:g} ans (coupons {hors_horizon[0]} à "
         f"{hors_horizon[-1]}) : deux racines, aucune réaliste ; ne pas l'utiliser dans le moteur."]
        if hors_horizon else []
    )
    export: dict[str, Any] = {
        "description": (
            "Composante EUR/USD de la note ECE : paramètres pour le moteur Monte Carlo. "
            "X = USD pour 1 EUR ; coupon payé en EUR si X_T < K ; mesure risque-neutre EUR."
        ),
        "date_valorisation": config.date_valorisation.isoformat(),
        "convention_temps": f"{config.base_calcul} depuis la date de valorisation",
        "spot": courbes.spot,
        "source_spot": marche.source_spot,
        "barriere_K": barriere.niveau,
        "date_pic_barriere": barriere.date_pic.isoformat(),
        "coupon_par_periode": config.coupon_par_periode,
        "nominal_eur": config.nominal_eur,
        "methode_smile": f"{config.methode_smile} (ordre {config.ordre_vanna_volga})",
        "pour_pricer_note_ece": {
            "fx_spot": courbes.spot,
            "fx_vol_tenors": t_coupons,
            "fx_atm_vols": [v.vol_atm for v in vols],
            "fx_barrier": [v.k_effective_vol_atm for v in vols],
            "usd_ois": courbe_au_format_moteur(courbes.usd, t_coupons),
            "indice_courbe_usd": marche.courbe_usd.indice if marche.courbe_usd is not None else "implicite (points de terme)",
            "changement_moteur": (
                "fx_barrier contient une barrière par date de coupon. Dans pricer_note_ece.py, remplacer la "
                f"comparaison au K unique par : {CHANGEMENT_MOTEUR}"
            ),
            "note": (
                "fx_atm_vols contient les vols ATM de marché aux dates de coupon ; fx_barrier la barrière "
                "effective qui, à ces vols, redonne la probabilité avec smile. usd_ois : taux zéro continus "
                "en décimal, maturités en années (format Curve(tenors, zeros)) ; nom de clé gardé pour le moteur, "
                "la courbe réellement utilisée est indiquée dans indice_courbe_usd."
            ),
        },
        "reference_eur_ois_sous_groupe_fx": {
            **courbe_au_format_moteur(courbes.eur, t_coupons),
            "indice": marche.courbe_eur.indice,
            "note": "Courbe EUR utilisée ici, au même format, pour comparaison avec eur_ois du moteur : "
                    "si elle diffère, le forward et donc les probabilités diffèrent.",
        },
        "controle_moteur": {
            "description": (
                "Faire tourner le moteur sur la seule condition de change (sans taux ni crédit ni rappel) avec "
                "fx_barrier : il doit retrouver ces probabilités. Écart toléré (taux EUR stochastiques dans le "
                "moteur, déterministes ici) : voir tolerance_points."
            ),
            "dates": t_coupons,
            "p_smile_attendues": [v.p_smile for v in vols],
            "tolerance_points": 100 * config.tolerance_controle_moteur,
        },
        "dates_coupon": [
            {
                "numero": v.numero,
                "date": v.date.isoformat(),
                "T": v.t,
                "forward": v.forward,
                "df_eur": v.df_eur,
                "df_usd": v.df_usd,
                "vol_atm": v.vol_atm,
                "p_smile": v.p_smile,
                "barriere_effective_a_vol_atm": v.k_effective_vol_atm,
                "sigma_eff_information": v.sigma_eff if v.t <= config.sigma_eff_max_annees else None,
            }
            for v in vols
        ],
        "hypotheses": [f"{h.nom} : {h.justification}" for h in config.hypotheses],
        "hypotheses_marche_utilisees": list(marche.hypotheses_utilisees),
        "avertissements": list(marche.avertissements) + list(avertissements) + avert_sigma,
    }
    if correlation is not None:
        r = correlation.retenue
        export["pour_pricer_note_ece"]["fx_rate_corr"] = r.rho
        export["pour_pricer_note_ece"]["note_fx_rate_corr"] = (
            "ρ = corr(Z_r, Z_X) avec X = 1/FX = EUR pour 1 USD (sujet, section 4.3) : "
            "Z_X = ρ Z_r + √(1 − ρ²) Z′. Pour un moteur qui simule FX = USD pour 1 EUR, prendre −ρ. "
            f"Estimé sur {r.libelle}, {r.fenetre_annees} an(s), variations sur {r.pas_jours} jour(s) "
            f"(IC 95 % : {r.ic95_bas:.2f} à {r.ic95_haut:.2f})."
        )
        export["correlation_taux_change"] = {
            "retenue": {"serie": r.serie, "fenetre_annees": r.fenetre_annees, "pas_jours": r.pas_jours,
                        "rho": r.rho, "ic95": [r.ic95_bas, r.ic95_haut], "nb_variations": r.nb_variations,
                        "debut": r.debut.isoformat(), "fin": r.fin.isoformat()},
            "justification": correlation.justification,
            "estimations": [
                {"serie": e.serie, "libelle": e.libelle, "fenetre_annees": e.fenetre_annees, "pas_jours": e.pas_jours,
                 "rho": e.rho, "ic95": [e.ic95_bas, e.ic95_haut], "nb_variations": e.nb_variations}
                for e in correlation.estimations
            ],
            "fenetres_non_couvertes": list(correlation.fenetres_non_couvertes),
        }
    return export


def ecrire_export(export: dict[str, Any], chemin: Path) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8")
