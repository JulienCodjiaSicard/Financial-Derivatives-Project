"""Local Streamlit dashboard for the ECE credit engine."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from credit_engine import (
    BASE, COUPON_TIMES, WINDOW, Calibration, FactorParams, backtest_credit,
    basket_diagnostics, basket_survival, calibrate_credit, credit_stress,
    demo_data, fit_stochastic, load_discount_curves, load_quotes, scenario_normals,
    simulate_credit, validate_credit, PUBLIC_CURVE_LABELS, PUBLIC_STUDY_DATE, TRANSACTION_BASE_DATE,
    discount_sensitivity, public_discount_data, refresh_dtcc_cds, refresh_dtcc_ois,
    refresh_ecb, refresh_tradeweb_cds, transaction_credit_data, credit_coupon,
    SYSTEMIC_SNAPSHOT_DATE, SYSTEMIC_BANKS_FILE,
)

INK = "#142D37"
COLORS = ["#087E7D", "#AF5729", "#5A5FAA", "#4D7842", "#AB4162", "#0377A5", "#82642C"]
LABELS = {"deterministic": "Indépendance MC", "lognormal": "Cox lognormal", "cirpp": "CIR++"}


def chart(fig: go.Figure, xlabel: str, ylabel: str, key: str, *, percent: bool = False) -> None:
    fig.update_layout(
        font=dict(family="Arial, sans-serif", color=INK, size=13),
        paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF", height=340,
        margin=dict(l=22, r=20, t=18, b=28), hovermode="x unified",
        legend=dict(orientation="h", y=1.10, x=0, title_text=""),
    )
    fig.update_xaxes(title=xlabel, gridcolor="#EDF1F0", zeroline=False)
    fig.update_yaxes(title=ylabel, gridcolor="#E3EBE9", zerolinecolor="#DCE5E2",
                     tickformat=".1%" if percent else None)
    st.plotly_chart(fig, key=key, width="stretch", config={"displaylogo": False})


def format_table(df: pd.DataFrame, key: str, percentage_cols: tuple[str, ...] = ()) -> None:
    config = {column: st.column_config.NumberColumn(format="percent") for column in percentage_cols}
    st.dataframe(df, hide_index=True, width="stretch", column_config=config, key=key)


def distinguish_quotes(fig: go.Figure) -> None:
    """Keep market markers visible when repricing reconstructs the same points."""
    for trace in fig.data:
        if trace.name.endswith("Cotation"):
            trace.update(mode="markers", marker=dict(symbol="circle-open", size=10, line=dict(width=2)))
        else:
            trace.update(mode="lines", line=dict(dash="solid", width=2))


class Upload(BytesIO):
    """Cache-friendly bytes with an original filename for CSV/XLSX dispatch."""

    def __init__(self, filename: str, content: bytes):
        super().__init__(content)
        self.name = filename


@st.cache_data(show_spinner=False)
def read_data(mode: str, quotes_file: tuple[str, bytes] | None = None,
              curve_file: tuple[str, bytes] | None = None):
    if mode == "DEMO":
        return demo_data()
    if quotes_file is None or curve_file is None:
        raise ValueError("Chargez les deux fichiers datés.")
    return load_quotes(Upload(*quotes_file), mode), load_discount_curves(Upload(*curve_file), mode)


@st.cache_data(show_spinner=False)
def calibrate_snapshot(q: pd.DataFrame, df, mode: str, basket: list[str],
                       method: str, extrapolate: bool) -> Calibration:
    return calibrate_credit(q, df, mode=mode, basket=basket, method=method, extrapolate=extrapolate)


@st.cache_data(show_spinner=False, max_entries=16)
def run_scenario(curves, grid: np.ndarray, paths: int, kind: str, params: FactorParams):
    model = None if kind == "deterministic" else fit_stochastic(curves, grid, kind, params)
    z = None if model is None else scenario_normals(grid, paths, 7911)
    result = simulate_credit(curves, grid, paths, model=model, credit_innovations=z)
    summary = basket_diagnostics(result)
    error = 0 if model is None else np.max(np.abs(result.conditional_mean_survival - model.targets))
    return summary, error, result.min_intensity, result.max_intensity


@st.cache_data(show_spinner=False, max_entries=8)
def run_history(q: pd.DataFrame, discounts, mode: str, basket: list[str],
                start: str, end: str, method: str, extrapolate: bool, point_in_time: bool):
    return backtest_credit(q, discounts, mode=mode, basket=basket, start=start, end=end,
                           method=method, extrapolate=extrapolate, holdout_extra_tenors=(3,),
                           require_point_in_time=point_in_time)


def calibration_view(cal: Calibration, horizon: float) -> None:
    st.subheader("Cotation, modèle et qualité d’ajustement")
    names = st.multiselect("Banques affichées", list(cal.curves), default=list(cal.curves), key="visible_banks")
    if not names:
        st.info("Sélectionnez au moins une banque pour afficher ses courbes.")
        return
    row = cal.repricing[cal.repricing["entity_id"].isin(names)]
    long = row.melt(id_vars=["entity_id", "tenor_years"], value_vars=["spread_bps", "model_bps"],
                    var_name="Série", value_name="Spread (pb)")
    long["Série"] = long["Série"].map({"spread_bps": "Cotation", "model_bps": "Modèle"})
    fig = px.line(long, x="tenor_years", y="Spread (pb)", color="entity_id", line_dash="Série",
                  markers=True, color_discrete_sequence=COLORS)
    distinguish_quotes(fig)
    chart(fig, "Maturité (ans)", "Spread CDS (pb)", "repricing")
    with st.expander("Résidus, bid-ask et provenance"):
        columns = ["entity_id", "maturity_date", "tenor_years", "spread_bps", "model_bps",
                   "residual_bps", "bid_bps", "ask_bps", "within_bid_ask", "residual_over_bid_ask",
                   "fit", "fit_residual_scale_bps", "provider", "collected_at", "available_at",
                   "reference_entity_id", "documentation", "source_url", "discount_provider"]
        format_table(row.reindex(columns=columns).dropna(axis=1, how="all"), "repricing_table")
        format_table(validate_credit(cal), "invariants")
        st.download_button("Télécharger le repricing CSV", row.to_csv(index=False).encode("utf-8-sig"),
                           "repricing.csv", "text/csv", key="download_fit")
    left, right = st.columns(2)
    with left:
        st.subheader("Intensités calibrées")
        fig = go.Figure()
        for i, bank in enumerate(names):
            c = cal.curves[bank]
            tails = c.extrapolate and horizon > c.edges[-1]
            edge = np.r_[c.edges, horizon] if tails else c.edges
            hazard = c.hazard_rate(edge)
            color = COLORS[i % len(COLORS)]
            fig.add_scatter(x=c.edges, y=c.hazard_rate(c.edges), name=bank,
                            line=dict(shape="hv", color=color))
            if tails:
                fig.add_scatter(x=edge[-2:], y=hazard[-2:], name=f"{bank} extrapolé",
                                line=dict(color=color, dash="dot"))
        chart(fig, "Temps (ans)", "Intensité annuelle", "hazards", percent=True)
    with right:
        st.subheader("Survie risque-neutre")
        fig = go.Figure()
        for i, bank in enumerate(names):
            c = cal.curves[bank]
            time = np.union1d(c.edges, np.linspace(0, c.edges[-1], 81))
            color = COLORS[i % len(COLORS)]
            fig.add_scatter(x=time, y=c.survival_probability(time), name=bank, line_color=color)
            if c.extrapolate and horizon > c.edges[-1]:
                tail = np.linspace(c.edges[-1], horizon, 25)
                fig.add_scatter(x=tail, y=c.survival_probability(tail), name=f"{bank} extrapolé",
                                line=dict(color=color, dash="dot"))
        chart(fig, "Temps (ans)", "Probabilité de survie", "survival", percent=True)
    with st.expander("Probabilités aux dates du produit"):
        time = np.r_[0, COUPON_TIMES[COUPON_TIMES <= horizon + 1e-12]]
        format_table(cal.probabilities(time).query("entity_id in @names"), "probability_table",
                     ("survival", "cumulative_pd", "period_pd_given_alive"))
    for warning in cal.warnings:
        st.warning(warning)


def basket_view(cal: Calibration, horizon: float) -> None:
    st.subheader("Premier défaut du panier")
    st.caption("Le panier survit si toutes les banques survivent. Les bandes à 95 % mesurent "
               "l’incertitude Monte Carlo sur les trajectoires simulées.")
    toggles = st.columns([2, 1])
    with toggles[0]:
        models = st.multiselect("Modèles stochastiques à comparer", ["lognormal", "cirpp"],
                                default=["lognormal"], format_func=LABELS.get)
    with toggles[1]:
        paths = st.selectbox("Trajectoires", [5000, 10000, 50000, 100000], index=1)
    params = {"deterministic": FactorParams()}
    if "lognormal" in models:
        with st.expander("Paramètres du Cox lognormal"):
            c = st.columns(3)
            k = c[0].number_input("κ OU (1/an)", .05, 5., .65, .05)
            s = c[1].number_input("σ OU (1/√an)", 0., 1.5, .45, .05)
            b = c[2].number_input("β systémique", 0., 3., 1., .1)
            params["lognormal"] = FactorParams(kappa=k, sigma=s, beta=b)
    if "cirpp" in models:
        with st.expander("Paramètres du CIR++"):
            c = st.columns(4)
            k = c[0].number_input("κ CIR (1/an)", .05, 5., .65, .05)
            s = c[1].number_input("σ CIR", 0., .2, .035, .005, format="%.3f")
            theta = c[2].number_input("θ CIR (1/an)", .0001, .03, .003, .0005, format="%.4f")
            y0 = c[3].number_input("y₀ CIR (1/an)", 0., .03, .002, .0005, format="%.4f")
            params["cirpp"] = FactorParams(kappa=k, sigma=s, theta=theta, y0=y0)
    grid = np.unique(np.r_[np.arange(0, horizon, 1 / 12), horizon])
    reference = basket_survival(cal.curves, grid)
    fig = go.Figure()
    fig.add_scatter(x=grid, y=reference, name="Indépendance analytique", line=dict(color=INK, width=3))
    for name, curve in cal.curves.items():
        fig.add_scatter(x=grid, y=curve.survival_probability(grid), name=name, visible="legendonly",
                        line=dict(dash="dash"))
    results = [{"Modèle": "Indépendance analytique", "PD premier défaut": 1 - reference[-1]}]
    with st.spinner("Recalage des survies et simulation du panier…"):
        for i, (kind, p) in enumerate(params.items()):
            try:
                summary, error, lo, high = run_scenario(cal.curves, grid, paths, kind, p)
            except ValueError as exc:
                st.warning(f"{LABELS[kind]} · {exc}")
                continue
            fill_colors = ("rgba(8,126,125,.10)", "rgba(175,87,41,.12)", "rgba(90,95,170,.12)")
            fig.add_scatter(x=grid, y=1-summary["pd_ci95_high"], line=dict(width=0),
                            hoverinfo="skip", showlegend=False)
            fig.add_scatter(x=grid, y=1-summary["pd_ci95_low"], line=dict(width=0),
                            fill="tonexty", fillcolor=fill_colors[i], hoverinfo="skip", showlegend=False)
            fig.add_scatter(x=grid, y=summary["basket_survival"], name=LABELS[kind],
                            line=dict(color=COLORS[i], width=2))
            last = summary.iloc[-1]
            results.append({"Modèle": LABELS[kind], "PD premier défaut": last["first_default_pd"],
                            "IC 95 % bas": last["pd_ci95_low"], "IC 95 % haut": last["pd_ci95_high"],
                            "Écart marginal max (pb de probabilité)": round(error*10000, 3),
                            "Intensité min": lo, "Intensité max": high})
    chart(fig, "Temps (ans)", "Probabilité de survie", "basket_survival", percent=True)
    st.markdown(f"**Risque au terme de l’horizon affiché ({horizon:g} ans)**")
    format_table(pd.DataFrame(results), "basket_risk",
                 ("PD premier défaut", "IC 95 % bas", "IC 95 % haut", "Intensité min", "Intensité max"))
    st.caption("Dépendance de scénario. Les CDS fournis identifient les marges, et les paramètres "
               "systémiques sont saisis séparément. Graines fixes. Le groupe Pricing fournit ses "
               "propres innovations déjà corrélées et ses facteurs d’actualisation pour le contrôle joint.")


def risk_view(cal: Calibration, snapshot: pd.DataFrame, q: pd.DataFrame, discounts, mode: str,
              basket: list[str], method: str, extrapolate: bool, horizon: float) -> None:
    st.subheader("Recalibrer après un choc")
    options = st.columns([1, 1, 1, 1])
    bump = options[0].selectbox("Choc CDS (pb)", [0, 1, 10, 50, -10], index=2)
    entity = options[1].selectbox("Banque visée", [None, *basket], format_func=lambda x: x or "Toutes")
    tenors = snapshot.loc[snapshot["entity_id"].isin(basket if entity is None else [entity]), "tenor_years"]
    tenor = options[2].selectbox("Maturité visée (ans)", [None, *sorted(tenors.unique())],
                                 format_func=lambda x: "Toutes" if x is None else f"{x:.4g}")
    rec = options[3].selectbox("Recovery", [None, .2, .3, .4, .5],
                               format_func=lambda x: "Cotation" if x is None else f"{x:.0%}")
    if rec is not None and tenor is not None:
        st.caption("Le recovery est modifié sur la courbe entière des banques visées.")
    base_pd = float(1 - basket_survival(cal.curves, horizon))
    try:
        shock = credit_stress(snapshot, cal.discount, mode=mode, basket=basket, method=method,
                              extrapolate=extrapolate, spread_bump_bps=bump, entity=entity,
                              tenor_years=tenor, recovery=rec)
        rows = []
        for name, curve in shock.curves.items():
            before = cal.curves[name]
            rows.append({"Banque": name, "Intensité initiale": before.hazard_rate(horizon),
                         "Intensité stressée": curve.hazard_rate(horizon),
                         "Survie initiale": before.survival_probability(horizon),
                         "Survie stressée": curve.survival_probability(horizon),
                         "Variation PD (pp)": 100*(curve.default_probability(horizon)
                                                     - before.default_probability(horizon))})
        stressed_pd = float(1 - basket_survival(shock.curves, horizon))
        st.metric(f"PD panier stressée à {horizon:g} ans", f"{stressed_pd:.2%}", f"{100*(stressed_pd-base_pd):+.2f} pp",
                  delta_color="inverse")
        format_table(pd.DataFrame(rows), "stress_table", ("Intensité initiale", "Intensité stressée",
                                                          "Survie initiale", "Survie stressée"))
        for warning in shock.warnings:
            st.warning(warning)
    except ValueError as exc:
        st.warning(f"Recalibration du scénario impossible · {exc}")

    with st.expander("Modifier une ou plusieurs cotations"):
        editable = cal.repricing[["entity_id", "tenor_years", "spread_bps"]].rename(
            columns={"spread_bps": "spread_scenario_bps"})
        edited = st.data_editor(editable, disabled=["entity_id", "tenor_years"], hide_index=True,
                                key="edit_quotes", width="stretch",
                                column_config={"spread_scenario_bps": st.column_config.NumberColumn(min_value=0)})
        if st.button("Recalibrer les cotations modifiées", key="apply_quotes"):
            bumped = ~np.isclose(cal.repricing["spread_bps"], edited["spread_scenario_bps"], atol=0, rtol=0)
            overrides = {(bank, tenor): value for bank, tenor, value in
                         edited.loc[bumped].itertuples(index=False, name=None)}
            try:
                updated = credit_stress(snapshot, cal.discount, mode=mode, basket=basket, method=method,
                                        extrapolate=extrapolate, spread_overrides_bps=overrides)
                value = 1 - basket_survival(updated.curves, horizon)
                st.success(f"Scénario édité · PD panier {value:.2%} ({100*(value-base_pd):+.2f} pp)")
                format_table(updated.repricing[["entity_id", "tenor_years", "spread_bps", "model_bps",
                                                "residual_bps", "hazard_annual"]], "edited_fit")
            except ValueError as exc:
                st.error(str(exc))

    st.subheader("Backtest de stabilité")
    st.caption("Recalcul sur les cotations et la courbe EUR de chaque date. Les tests écartent "
               "séparément le 3 ans et le 5 ans quand ils sont disponibles à l’intérieur de la courbe.")
    end = q["valuation_date"].max()
    windows = st.columns(2)
    first = windows[0].date_input("Début", value=pd.Timestamp(WINDOW[0]).date() if mode == "DEMO"
                                  else (end - pd.DateOffset(months=6)).date())
    last = windows[1].date_input("Fin", value=end.date())
    point_in_time = False
    if mode == "SNAPSHOT":
        point_in_time = st.checkbox("Exiger les horodatages point-in-time",
                                    help="Capturer la version disponible avant la fin de chaque jour UTC.")
    if last < first:
        st.warning("La fin de la fenêtre doit être postérieure au début.")
        return
    try:
        with st.spinner("Recalibration sur les dates de la fenêtre…"):
            history = run_history(q, discounts, mode, basket, str(first), str(last),
                                  method, extrapolate, point_in_time)
    except ValueError as exc:
        st.info(str(exc))
        return
    st.caption(f"{history.status['status'].eq('calibrated').sum()} / {len(history.status)} dates calibrées")
    dated_only = history.status["availability_evidence"].eq("valuation_date_only").sum()
    if dated_only:
        st.caption(f"{dated_only} dates avec contrôle par date de valorisation uniquement. "
                   "Disponibilité de la version à l’époque non attestée.")
    if not history.daily.empty:
        fig = px.line(history.daily, x="valuation_date", y="first_default_pd", markers=True,
                      color_discrete_sequence=COLORS)
        chart(fig, "Date d’observation", "PD à 7 ans du premier défaut", "history_basket", percent=True)
        left, right = st.columns(2)
        with left:
            fig = px.line(history.bank, x="valuation_date", y="hazard_annual", color="entity_id",
                          markers=True, color_discrete_sequence=COLORS)
            chart(fig, "Date d’observation", "Intensité à 7 ans", "history_hazard", percent=True)
        with right:
            fig = px.line(history.bank, x="valuation_date", y="survival", color="entity_id",
                          markers=True, color_discrete_sequence=COLORS)
            chart(fig, "Date d’observation", "Survie à 7 ans", "history_survival", percent=True)
        fig = px.line(history.daily, x="valuation_date", y="max_abs_residual_bps", markers=True,
                      color_discrete_sequence=COLORS)
        chart(fig, "Date d’observation", "Résidu maximal (pb)", "history_residual")
    with st.expander("Dates, repricing et contrôle hors calibration"):
        format_table(history.status, "history_status")
        if not history.repricing.empty:
            format_table(history.repricing[["valuation_date", "entity_id", "tenor_years",
                                             "spread_bps", "model_bps", "residual_bps"]],
                         "history_repricing")
        format_table(history.holdout, "history_holdout")


@st.cache_data(show_spinner=False)
def _cached_public_curve(source: str):
    return public_discount_data(source)


def public_view() -> None:
    root = BASE / "data"
    sources = {key: label for key, label in PUBLIC_CURVE_LABELS.items()
               if (root / ("market" if key == "dtcc_ois" else "proxies") / f"{key}.csv").is_file()}
    with st.sidebar:
        st.caption("Le cache local est disponible hors ligne.")
        refresh = st.selectbox("Source à actualiser", ["tradeweb_cds", "dtcc_ois", "ecb_aaa", "ecb_all", "estr", "dtcc_cds"],
                               format_func={"tradeweb_cds": "Tradeweb + DTCC · CDS",
                                   "dtcc_ois": "DTCC/CFTC · swaps €STR", "dtcc_cds": "DTCC/SEC · audit",
                                   "ecb_aaa": PUBLIC_CURVE_LABELS["ecb_aaa"],
                                   "ecb_all": PUBLIC_CURVE_LABELS["ecb_all"], "estr": "BCE · €STR overnight"}.get)
        now = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
        last = now - pd.offsets.BDay()
        endpoint = st.date_input("Date de fin ou archive à actualiser", last.date(), max_value=now.date())
        if st.button("Actualiser la source publique"):
            try:
                with st.spinner("Collecte et contrôle de la réponse…"):
                    if refresh == "dtcc_ois":
                        refresh_ecb("estr", pd.Timestamp(endpoint)-pd.Timedelta(days=7), endpoint)
                        result = refresh_dtcc_ois(endpoint)
                    elif refresh == "dtcc_cds":
                        result = refresh_dtcc_cds(endpoint)
                    elif refresh == "tradeweb_cds":
                        result = refresh_tradeweb_cds(endpoint)
                    else:
                        result = refresh_ecb(refresh, pd.Timestamp(endpoint)-pd.Timedelta(days=14), endpoint)
                _cached_public_curve.clear()
                if result.empty:
                    st.info("Archive accessible. Aucun instrument éligible pour cette date.")
                else:
                    st.success("Contrôles terminés. Le cache est disponible.")
            except (OSError, ValueError, ImportError) as exc:
                st.warning(f"Actualisation indisponible. Le dernier cache valide reste accessible. {exc}")
    if not sources:
        st.error("Les courbes publiques sont absentes du cache.")
        return
    try:
        cache = {key: _cached_public_curve(key) for key in sources}
        observed = transaction_credit_data()
        demo_quotes, demo_discounts = demo_data()
    except (OSError, ValueError) as exc:
        st.error(f"Cache public illisible · {exc}")
        return
    with st.sidebar:
        view = st.radio("Étude", ["Transactions CDS observées", "Courbes EUR et sensibilité"])
        use_transactions = view.startswith("Transactions")
        available_dates = set.union(*(set(curves) for _, curves in cache.values()))
        supported = sorted(available_dates & set(observed["valuation_date"]), reverse=True)
        dates = supported if use_transactions else sorted(available_dates, reverse=True)
        if not dates:
            st.info("Aucune date commune en cache pour cette étude.")
            return
        default_date = pd.Timestamp(TRANSACTION_BASE_DATE if use_transactions else PUBLIC_STUDY_DATE)
        day = st.selectbox("Date de valorisation", dates, index=dates.index(default_date) if default_date in dates else 0,
                           format_func=lambda v: str(v.date()), key=f"public_date_{use_transactions}")
        matching = {name: curves[day] for name, (_, curves) in cache.items() if day in curves}
        curve_key = st.selectbox("Courbe EUR", list(matching), format_func=sources.get)
        current = matching[curve_key]
        st.caption(f"Dernière courbe en cache · {max(cache[curve_key][1]).date()}")
        st.caption(f"Dernier CDS rapproché · {observed['valuation_date'].max().date()}")
        if (now-max(cache[curve_key][1])).days > (45 if curve_key == "eiopa" else 7):
            st.warning("Cache ancien pour cette source.")
        if use_transactions:
            q = observed[observed["valuation_date"].eq(day)]
            tier = st.selectbox("Séniorité", list(q["seniority"].unique()),
                                format_func={"senior": "Senior", "senior_non_preferred": "Senior non préféré (SNRLAC)",
                                             "subordinated": "Subordonné"}.get)
            q = q[q["seniority"].eq(tier)]
        else:
            q = demo_quotes[demo_quotes["valuation_date"].eq(day)]
    rate_frame = cache[curve_key][0]
    pillars = rate_frame.loc[pd.to_datetime(rate_frame["valuation_date"]).eq(day)].sort_values("time_years")
    if use_transactions:
        st.info("PUBLIC · Spreads de clôture Tradeweb/SEC rapprochés des transactions DTCC.")
        st.warning("OPTION B · Équivalent par du pricer ECE. Le spread par de marché et la conversion "
                   "effectivement utilisée par Tradeweb restent indéterminés. Recouvrement supposé.")
    else:
        st.info("OBSERVED · Courbe de taux publique. Les sensibilités crédit de cette étude utilisent des CDS synthétiques.")
    if curve_key.startswith("ecb"):
        st.warning("Proxy d’actualisation EUR · Courbe zéro-coupon souveraine BCE.")
    elif curve_key == "eiopa":
        st.warning("OBSERVED REGULATORY RFR CURVE — NOT DIRECT €STR OIS MARKET CURVE")
    else:
        st.caption("€STR OIS reconstruit à partir des taux de transactions. Médiane par échéance et convention de paiement supposée.")
    st.caption(f"Observation du {day.date()} · {current.provider} · Collecte {pillars.iloc[0]['collected_at']}")
    if current.source_url:
        st.markdown(f"[Source primaire de la courbe]({current.source_url})")
    cds_tab, basket_tab, risk_tab, coupon_tab = st.tabs(["Calibration CDS", "Premier défaut & corrélation", "Risque, stress & backtest", "Condition de crédit du coupon"])
    official = pd.read_csv(root / SYSTEMIC_BANKS_FILE) if use_transactions else pd.DataFrame()
    is_systemic = (use_transactions and curve_key == "dtcc_ois"
                   and day == pd.Timestamp(SYSTEMIC_SNAPSHOT_DATE)
                   and set(q["entity_id"]) == set(official.loc[official["decision"].eq("selected"), "upi"]))
    cases = {curve_key: current, **{name: curve for name, curve in matching.items() if name != curve_key}}
    coupon_cal = None
    if not use_transactions and day in demo_discounts:
        cases["demo"] = demo_discounts[day]
    fits = basket = pd.DataFrame()
    if not q.empty:
        try:
            mode = "SNAPSHOT" if use_transactions else "DEMO"
            fits, basket = discount_sensitivity(q, cases, mode=mode)
            if use_transactions:
                coupon_cal = calibrate_snapshot(q, current, mode, list(q["entity_id"].unique()), "strict", False)
        except ValueError as exc:
            st.error(f"Calibration indisponible · {exc}")
    with cds_tab:
        st.subheader("Courbe d’actualisation EUR")
        zeros = -np.log(current.dfs[1:])/current.times[1:]
        figure = go.Figure(go.Scatter(x=current.times[1:], y=zeros, mode="lines+markers", name=current.provider))
        chart(figure, "Années ACT/365F", "Taux zéro équivalent continu", "pub_curve", percent=True)
        with st.expander("Piliers et provenance"):
            format_table(pillars, "pub_pillars")
        if not fits.empty:
            current_fit = fits[fits["discount_case"].eq(curve_key) & fits["scenario"].eq("baseline")]
            cols = ["bank_name", "tenor_years", "spread_bps", "model_bps", "residual_bps",
                    "hazard_annual", "survival"]
            if use_transactions:
                cols[1:1] = ["seniority", "maturity_date"]
                cols += ["recovery", "cumulative_pd"]
                st.subheader("Repricing des spreads traités")
                st.caption("Une seule maturité observée par banque. Les intensités sont constantes jusqu’au pilier.")
                relevant = official.loc[official["bank_name"].isin(current_fit["bank_name"]),
                                        ["bank_name", "systemic_status", "authority", "known_from", "classification_url"]]
                if not relevant.empty:
                    format_table(relevant, "pub_systemic")
                with st.expander("Équilibre des jambes du CDS"):
                    legs = current_fit[["bank_name", "spread_bps", "risky_annuity", "protection_leg"]].copy()
                    legs["jambe_prime"] = legs["spread_bps"]*1e-4*legs["risky_annuity"]
                    legs["écart_protection_prime"] = legs["protection_leg"]-legs["jambe_prime"]
                    format_table(legs, "pub_cds_legs")
                with st.expander("Type de spread, qualité et collecte CDS"):
                    provenance = ["bank_name", "source_quote_type", "coupon_bps", "quote_quality",
                                  "model_cds_convention", "collected_at", "dtcc_notional_capped"]
                    format_table(current_fit[provenance], "pub_quote_provenance")
            else:
                st.subheader("Repricing des CDS de démonstration")
            format_table(current_fit[cols], "pub_reprice", tuple(c for c in ("hazard_annual", "survival",
                                                                       "recovery", "cumulative_pd") if c in cols))
            st.download_button("Télécharger les calculs et la provenance", fits.to_csv(index=False),
                               file_name="public_repricing.csv", mime="text/csv")
        elif q.empty:
            st.caption("Les CDS de démonstration sont absents à cette date. La courbe est consultable seule.")
    with basket_tab:
        st.subheader("Survie et premier défaut sous indépendance")
        if not basket.empty:
            current_basket = basket[basket["discount_case"].eq(curve_key) & basket["scenario"].eq("baseline")]
            if len(q["entity_id"].unique()) == 1:
                st.caption("Une seule banque éligible ce jour dans ce rang. Le tableau affiche sa survie.")
            else:
                st.caption("Panier illustratif de même séniorité. L’indépendance est une hypothèse.")
            if current_basket["time_status"].eq("tail_scenario").any():
                st.warning("SCENARIO · Au-delà du dernier pilier, multiplicateurs 0,5×, 1× et 1,5× "
                           "de l’intensité terminale. Les projections à 7 ans dépendent de cette hypothèse.")
            format_table(current_basket[["horizon_years", "time_status", "tail_multiplier",
                                         "pd_first_independent", "survival_single"]],
                         "pub_scenario", ("pd_first_independent", "survival_single"))
        if is_systemic:
            study = _systemic_export("dependence_and_convergence.csv")
            if not study.empty:
                st.subheader("Modèles stochastiques du panier retenu")
                st.caption("Cox lognormal : facteur OU exponentié. CIR++ : facteur CIR positif et décalage "
                           "déterministe. Les deux modèles sont recalés aux survies cibles. "
                           "Volatilités, retour à la moyenne et dépendance sont des hypothèses de scénario.")
                at_7 = study[study["time_years"].eq(7.)]
                format_table(at_7[["scenario", "paths", "max_step_years", "first_default_pd", "mc_se",
                                   "prob_0_defaults", "prob_1_defaults", "prob_2_defaults", "prob_3_defaults"]],
                             "public_stochastic", ("first_default_pd", "mc_se", "prob_0_defaults",
                                                   "prob_1_defaults", "prob_2_defaults", "prob_3_defaults"))
    with risk_tab:
        st.subheader("Sensibilités de taux, spread et recovery")
        if not fits.empty:
            st.caption("SCENARIO · Les chocs modifient les hypothèses ou le spread d’entrée du modèle.")
            cols = ["bank_name", "tenor_years", "discount_case", "scenario", "result_kind", "recovery",
                    "hazard_annual", "survival", "residual_bps"]
            format_table(fits[cols], "pub_stress", ("hazard_annual", "survival"))
        st.subheader("Historique des observations rapprochées")
        st.caption("Snapshots rétrospectifs, couverture discontinue. Les champs Tradeweb/DTCC sont reliés par date et UPI.")
        st.info("La couverture ne permet pas d’estimer une volatilité annuelle ou une corrélation historique fiable "
                "du panier. Les options publiques repérées portent sur des indices.")
        if is_systemic:
            with st.expander("Chocs de −50 à +100 pb, recouvrement et prolongement du panier retenu"):
                shocks = _systemic_export("coupon_semesters.csv")
                if not shocks.empty:
                    format_table(shocks.loc[shocks["semester"].eq(14),
                                 ["scenario", "first_default_pd", "expected_credit_only_coupon"]],
                                 "public_coupon_stresses", ("first_default_pd",))
        cols = ["valuation_date", "bank_name", "seniority", "maturity_date", "spread_bps",
                "source_url", "dtcc_source_url"]
        history_display = observed.rename(columns={"bank_name": "Banque", "seniority": "Rang",
                                                   "maturity_date": "Maturité"})
        tape = px.scatter(history_display, x="valuation_date", y="spread_bps", color="Banque", symbol="Rang",
                          hover_data=["Maturité"], color_discrete_sequence=COLORS)
        chart(tape, "Date de transaction", "Spread de clôture (pb)", "pub_credit_tape")
        format_table(observed[cols].sort_values("valuation_date", ascending=False), "pub_history")
        for filename, label in (("dtcc.csv", "Autres transactions DTCC écartées du bootstrap"),
                                ("estr.csv", "€STR overnight de référence"),
                                ("mmsr.csv", "BCE MMSR · moyenne OIS forward 12–24 mois")):
            if (root / "market" / filename).is_file():
                with st.expander(label):
                    format_table(pd.read_csv(root / "market" / filename, dtype={"Dissemination Identifier": str}),
                                 "pub_ref_" + filename)
    with coupon_tab:
        if coupon_cal is None:
            st.info("Sélectionnez l’étude Transactions CDS observées pour voir un coupon du panier PUBLIC.")
        else:
            coupon_view(coupon_cal, q, "SNAPSHOT", "public_coupon")




@st.cache_data(show_spinner=False)
def _systemic_export(filename: str) -> pd.DataFrame:
    path = BASE / "outputs/systemic" / filename
    return pd.read_csv(path) if path.is_file() else pd.DataFrame()


def coupon_view(cal: Calibration, snapshot: pd.DataFrame, mode: str, key: str) -> None:
    st.subheader("Condition de crédit du coupon")
    st.caption("À chaque semestre, la condition est satisfaite si toutes les banques sont encore en vie. "
               "Les montants sont exprimés pour 100 de nominal sous la seule condition de crédit.")
    st.dataframe(cal.repricing[["bank_name", "seniority"]].drop_duplicates(), hide_index=True,
                 width="stretch", alt="Banques et rangs de dette du coupon")
    is_extended = min(c.edges[-1] for c in cal.curves.values()) < 7.-1e-12
    left, right = st.columns(2)
    rate = left.selectbox("Convention du taux illustratif", ["7 % annuel", "7 % par semestre"], key=f"{key}_rate")
    convention = "annual" if rate == "7 % annuel" else "per_semester"
    multiplier = None
    if is_extended:
        choices = [None, .5, 1., 1.5] if mode == "SNAPSHOT" and key != "public_coupon" else [.5, 1., 1.5]
        default = 0 if choices[0] is None else 1
        multiplier = right.selectbox("Intensité après le dernier pilier", choices, index=default,
                                     format_func=lambda x: "Choisir un scénario" if x is None else f"{x:g} ×",
                                     key=f"{key}_tail")
        if multiplier is None:
            st.info("Choisissez un prolongement pour produire les 14 échéances à partir de cette courbe partielle.")
            return
        st.warning("SCÉNARIO · Les échéances au-delà des maturités couvertes utilisent le prolongement choisi.")
    stress = st.checkbox("Comparer avec un choc de spreads de +25 pb", key=f"{key}_stress")
    base = credit_coupon(cal.curves, discount=cal.discount, tail_multiplier=multiplier, rate_convention=convention)
    show = base.basket.assign(scenario="Hypothèse centrale" if not is_extended else f"Queue {multiplier:g} ×")
    if stress:
        try:
            shocked = credit_stress(snapshot, cal.discount, mode=mode, basket=list(cal.curves),
                                     method="constrained" if cal.repricing["fit"].eq("constrained").any() else "strict",
                                     spread_bump_bps=25.)
            stressed = credit_coupon(shocked.curves, discount=cal.discount, tail_multiplier=multiplier,
                                     rate_convention=convention)
            show = pd.concat([show, stressed.basket.assign(scenario="Stress +25 pb")], ignore_index=True)
        except ValueError as exc:
            st.warning(f"Le choc de spread ne peut pas être recalibré · {exc}")
    metrics = st.columns(3)
    metrics[0].metric("Condition satisfaite au 14e semestre", f"{base.basket['credit_condition_probability'].iloc[-1]:.2%}")
    metrics[1].metric("Premier défaut à 7 ans", f"{base.basket['first_default_pd'].iloc[-1]:.2%}")
    metrics[2].metric("Coupon espéré au 14e semestre", f"{base.basket['expected_credit_only_coupon'].iloc[-1]:.2f}")
    drawing = px.line(show, x="time_years", y="credit_condition_probability", color="scenario",
                      markers=True, color_discrete_sequence=COLORS)
    chart(drawing, "Échéance en années", "Probabilité de respecter la condition crédit", f"{key}_chart", percent=True)
    columns = {"semester": "Semestre", "time_years": "Échéance (années)", "scenario": "Hypothèse",
               "credit_condition_probability": "Survie du panier", "first_default_pd": "PD de premier défaut",
               "expected_credit_only_coupon": "Coupon espéré / 100", "extrapolated": "Extrapolation"}
    table = show[list(columns)].rename(columns=columns)
    format_table(table, f"{key}_table", ("Survie du panier", "PD de premier défaut"))
    left, right = st.columns(2)
    left.download_button("Télécharger les 14 échéances", show.to_csv(index=False), "coupon_credit.csv", "text/csv",
                         key=f"{key}_download")
    names = snapshot.drop_duplicates("entity_id").set_index("entity_id")["bank_name"]
    detailed = base.banks.assign(bank_name=base.banks["entity_id"].map(names))
    right.download_button("Télécharger les PD individuelles", detailed.to_csv(index=False),
                          "coupon_banques.csv", "text/csv", key=f"{key}_download_banks")
    with st.expander("Survie et PD par banque"):
        format_table(detailed[["bank_name", "time_years", "risk_neutral_survival", "risk_neutral_pd",
                               "conditional_pd_given_alive_semester", "extrapolated"]], f"{key}_banks",
                     ("risk_neutral_survival", "risk_neutral_pd", "conditional_pd_given_alive_semester"))



def main() -> None:
    st.set_page_config(page_title="ECE · Risque de crédit", layout="wide", initial_sidebar_state="expanded")
    st.markdown(
        '<style>.block-container{padding-top:2.6rem;max-width:1420px}'
        '[data-testid="stSidebar"]{border-right:1px solid #E2EBE8}'
        '[data-testid="stMetric"]{border:1px solid #E2EBE8;border-radius:12px;padding:12px 16px}'
        '[data-testid="stTabs"] [role="tablist"]{gap:20px}'
        '[data-testid="stTabs"] [role="tab"]{padding:12px 2px}'
        '.credit-kicker{font:700 .82rem Arial;letter-spacing:.115em;color:#087E7D;margin:0 0 .4rem}'
        '</style>', unsafe_allow_html=True)
    st.markdown('<p class="credit-kicker">ECE · DÉRIVÉS FINANCIERS</p>', unsafe_allow_html=True)
    st.title("Risque de crédit bancaire")
    st.caption("Calibration CDS, survie du panier et sensibilités de la note ECE Capital Invest / ECE Bank.")
    with st.sidebar:
        st.header("Données")
        mode = st.radio("Mode", ["DEMO", "PUBLIC", "SNAPSHOT", "MARKET"], key="credit_mode")
        qf = df = None
        if mode == "SNAPSHOT":
            a = st.file_uploader("Cotations CDS", type=["csv", "xlsx"])
            b = st.file_uploader("Courbes EUR", type=["csv", "xlsx"])
            if a and b:
                qf, df = (a.name, a.getvalue()), (b.name, b.getvalue())
            else:
                st.info("Chargez les historiques ou un instantané, dans les formats du README.")
                st.stop()
        if mode == "MARKET":
            st.info("Aucun flux de marché direct connecté. Un export autorisé se charge en mode SNAPSHOT. "
                    "Le moteur accepte les enregistrements d’un fournisseur autorisé via son API Python.")
            st.stop()
    if mode == "PUBLIC":
        public_view()
        return
    try:
        q, discounts = read_data(mode, qf, df)
    except (ValueError, TypeError, OSError, ImportError) as exc:
        st.error(f"Import impossible · {exc}")
        st.stop()
    with st.sidebar:
        dates = sorted(q["valuation_date"].unique(), reverse=True)
        selected = st.selectbox("Date de valorisation", dates, format_func=lambda x: str(x.date()))
        snapshot = q[q["valuation_date"].eq(selected)]
        universe = list(snapshot["entity_id"].unique())
        basket = st.multiselect("Panier", universe, default=universe, key=f"basket_{mode}")
        method = st.selectbox("Calibration", ["strict", "constrained"],
                              format_func={"strict": "Bootstrap strict", "constrained": "Repli contraint"}.get)
        extrapolate = st.checkbox("Extrapoler la dernière intensité")
        st.caption(f"Dernière observation disponible · {dates[0].date()}")
    if mode == "DEMO":
        st.warning("DEMO · Cotations CDS, bid-ask et courbes EUR synthétiques.")
    else:
        st.info("SNAPSHOT · Données déclarées observées dans les fichiers chargés.")
    st.caption("Sources CDS · " + ", ".join(snapshot["provider"].unique())
               + f" | Base temps · {', '.join(snapshot['time_basis'].unique())}")
    if selected not in discounts:
        st.error("Courbe EUR indisponible à cette date. Sélectionnez une date couverte.")
        st.stop()
    st.caption(f"Source EUR · {discounts[selected].provider or 'non renseignée'}")
    if not basket:
        st.info("Sélectionnez au moins une banque dans le panier.")
        st.stop()
    try:
        cal = calibrate_snapshot(snapshot, discounts[selected], mode, basket, method, extrapolate)
    except ValueError as exc:
        st.error(f"Calibration impossible · {exc}")
        st.stop()
    horizon = min(7., cal.discount.times[-1], *[
        7. if c.extrapolate else c.edges[-1] for c in cal.curves.values()])
    metrics = st.columns(4)
    metrics[0].metric("Date de valorisation", str(selected.date()))
    metrics[1].metric("Banques dans le panier", len(cal.curves))
    metrics[2].metric("Résidu maximal", f"{cal.repricing['residual_bps'].abs().max():.3g} pb")
    metrics[3].metric(f"PD panier à {horizon:g} ans", f"{1-basket_survival(cal.curves, horizon):.2%}")
    if horizon < 7 - 1e-12:
        st.warning(f"Horizon commun disponible · {horizon:g} ans. La couverture à 7 ans est incomplète.")
    if any(horizon > c.edges[-1] + 1e-12 for c in cal.curves.values()):
        st.warning("Le risque du panier à l’horizon affiché utilise une extrapolation plate sur certaines courbes.")
    fit_type = "exact" if cal.repricing["fit"].eq("exact").all() else "contraint, résidus à examiner"
    st.caption(f"Ajustement numérique · {fit_type} | Validation du livrable · démonstration synthétique")
    tab_cds, tab_basket, tab_risk, tab_coupon = st.tabs(
        ["Calibration CDS", "Premier défaut & corrélation", "Risque, stress & backtest", "Condition de crédit du coupon"])
    with tab_cds:
        calibration_view(cal, horizon)
    with tab_basket:
        basket_view(cal, horizon)
    with tab_risk:
        risk_view(cal, snapshot, q, discounts, mode, basket, method, extrapolate, horizon)
    with tab_coupon:
        coupon_view(cal, snapshot, mode, "regular_coupon")


def export_figures(folder: Path = BASE / "outputs") -> None:
    """Export an offline Plotly summary of the reproducible DEMO results."""
    quotes = pd.read_csv(folder / "repricing.csv")
    displayed = quotes.melt(id_vars=["entity_id", "tenor_years"], value_vars=["spread_bps", "model_bps"],
                            var_name="série", value_name="CDS (pb)")
    displayed["série"] = displayed["série"].map({"spread_bps": "Cotation", "model_bps": "Modèle"})
    probability = pd.read_csv(folder / "probabilities_semiannual.csv")
    basket = pd.read_csv(folder / "basket.csv")
    basket["modèle"] = basket["model"].map(LABELS)
    history = pd.read_csv(folder / "backtest_daily.csv")
    stress = pd.read_csv(folder / "stress_basket.csv").query("status == 'recalibrated'")
    stress["scenario"] = stress["scenario"].replace({
        "one bank +10 bp": "Banque A +10 pb", "one tenor +10 bp": "A 5Y +10 pb",
    }).str.replace(" bp", " pb", regex=False)
    spreads = px.line(displayed, x="tenor_years", y="CDS (pb)", color="entity_id", line_dash="série",
                      color_discrete_sequence=COLORS)
    distinguish_quotes(spreads)
    hazard = go.Figure()
    for i, (bank, rows) in enumerate(quotes.groupby("entity_id")):
        rates = rows["hazard_annual"].to_numpy()
        hazard.add_scatter(x=np.r_[0, rows["tenor_years"]], y=np.r_[rates, rates[-1]], name=bank,
                           line=dict(shape="hv", color=COLORS[i % len(COLORS)]))
    joint = px.line(basket, x="time_years", y="basket_survival", color="modèle", color_discrete_sequence=COLORS)
    independent = basket.query("model == 'deterministic'")
    joint.add_scatter(x=independent["time_years"], y=independent["independent_analytic_survival"],
                      name="Indépendance analytique", line=dict(color=INK, dash="dot"))
    specs = [
        (spreads, "Cotations CDS et repricing", "Tenor (ans)", "CDS (pb)", None),
        (hazard, "Intensité par segment calibré", "Temps (ans)", "Intensité annuelle", ".2%"),
        (px.line(probability, x="time_years", y="survival", color="entity_id",
                 color_discrete_sequence=COLORS), "Survies individuelles", "Temps (ans)", "Survie", ".1%"),
        (joint, "Survie du panier · 50 000 trajectoires", "Temps (ans)", "Survie", ".1%"),
        (px.bar(stress, x="scenario", y="delta_pd_percentage_points", color_discrete_sequence=COLORS),
         "Stress · variation de la PD panier à 7 ans", "Scénario", "Variation (points de pourcentage)", None),
        (px.line(history, x="valuation_date", y="first_default_pd", markers=True, color_discrete_sequence=COLORS),
         "Historique sur six mois · PD panier à 7 ans", "Date du snapshot", "PD premier défaut", ".1%"),
    ]
    panels = []
    for i, (fig, heading, x, y, ticks) in enumerate(specs):
        fig.update_layout(template="plotly_white", xaxis_title=x, yaxis_title=y, height=335,
                          font=dict(family="Arial", color=INK),
                          legend=dict(title_text="", orientation="h", y=1.08, yanchor="bottom", x=0),
                          margin=dict(t=68 if i < 4 else 20, l=55, r=15, b=50), hovermode="x unified")
        if i == 4:
            fig.update_xaxes(tickangle=-24, automargin=True)
        fig.update_yaxes(tickformat=ticks)
        plot = fig.to_html(full_html=False, include_plotlyjs=i == 0, config={"displaylogo": False})
        panels.append(f"<section><h2>{heading}</h2>{plot}</section>")
    page = """<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport"
content="width=device-width, initial-scale=1"><title>ECE · Résultats Crédit</title><style>
*{box-sizing:border-box}body{margin:0;background:#f2f6f5;color:#142d37;font:16px Arial,sans-serif}
header,main{max-width:1500px;margin:auto;padding:24px}h1{margin:.35rem 0}.provenance{color:#087677;font-weight:bold}
main{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;padding-top:0}
section{background:white;border:1px solid #d9e6e3;border-radius:12px;padding:12px;overflow:hidden}
h2{font-size:1.15rem;margin:7px 10px}@media(max-width:900px){main{grid-template-columns:1fr}}
</style><header><p class="provenance">ECE 2026 · DEMO SYNTHÉTIQUE</p><h1>Résultats du moteur Crédit</h1>
<p>Snapshot du 6 novembre 2026 · Trois banques fictives · Probabilités risque-neutres</p></header><main>"""
    (folder / "figures.html").write_text(page + "".join(panels) + "</main></html>", encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1:] == ["--export-figures"]:
        export_figures()
    else:
        main()

