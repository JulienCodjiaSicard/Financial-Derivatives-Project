"""Analytical, market-input, stochastic and Pricing handoff validation."""
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import json
import os

import numpy as np
import pandas as pd
import pytest
from scipy.integrate import quad

from credit_engine import (
    COUPON_TIMES, Backtest, DiscountCurve, FactorParams, HazardCurve,
    backtest_credit, basket_diagnostics, basket_survival, calibrate_credit,
    cir_forward_hazard, credit_stress, demo_data, fit_stochastic, flat_hazard_benchmark,
    load_discount_curves, load_quotes, price_cds, reprice_joint_cds, scenario_normals,
    simulate_credit, validate_correlation, validate_credit,
)

DAY = "2026-11-06"


@pytest.fixture(scope="module")
def history():
    return demo_data()


@pytest.fixture(scope="module")
def demo(history):
    q, discounts = history
    day = pd.Timestamp(DAY)
    snapshot = q[q["valuation_date"].eq(day)].reset_index(drop=True)
    return snapshot, discounts[day], calibrate_credit(snapshot, discounts[day], mode="DEMO")


def flat_df(rate=.025, horizon=7):
    return DiscountCurve([0, horizon], [1, np.exp(-rate*horizon)], DAY)


def quotes_from(hazard, df, recovery=.4):
    tenor = hazard.edges[1:]
    spreads = np.array([price_cds(t, hazard, df, recovery).fair_spread_bps for t in tenor])
    return pd.DataFrame({
        "valuation_date": DAY, "collected_at": DAY+"T16:00Z", "entity_id": "TEST",
        "bank_name": "Synthetic reference bank", "tenor_years": tenor, "spread": spreads,
        "unit": "bps", "quote_type": "par_spread", "currency": "EUR", "seniority": "senior",
        "restructuring": "test", "recovery": recovery, "provider": "Test fixture",
        "time_basis": df.time_basis, "data_kind": "synthetic",
    })


def audit_evidence(kind, evidence):
    """Optional validation output. Tests never write files by default."""
    folder = os.environ.get("ECE_VALIDATION_OUTPUT")
    if folder:
        Path(folder, f"{kind}.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")


def test_flat_probability_and_cds():
    h, recovery, t = .019, .4, 3.27
    c = HazardCurve([0, 7], [h])
    rates = flat_df(rate=0)
    legs = price_cds(t, c, rates, recovery)
    assert legs.protection == pytest.approx((1-recovery) * (1-np.exp(-h*t)), abs=1e-13)
    assert legs.risky_annuity == pytest.approx((1-np.exp(-h*t))/h, abs=1e-12)
    assert legs.fair_spread_bps == pytest.approx(114, abs=1e-10)
    assert legs.buyer_pv(114, 3e6) == pytest.approx(0, abs=1e-8)
    assert legs.buyer_pv(114, 3e6, .02) == pytest.approx(-60000)
    assert flat_hazard_benchmark(114, recovery) == pytest.approx(h)
    assert c.survival_probability(0) == 1
    assert c.default_probability(7, 2) == pytest.approx(1-np.exp(-h*5))
    assert c.default_density(2) == pytest.approx(h*np.exp(-h*2))
    assert price_cds(t, c, rates, accrue_on_default=False).accrued_on_default_annuity == 0


def test_piecewise_pricer_vs_independent_quadrature():
    t = 2.17
    times = np.array([0, .17, .83, 1.42, 2.5])
    dfs = np.exp(-np.array([0, -.01, .02, .05, .06]))
    df = DiscountCurve(times, dfs, DAY)
    h = HazardCurve([0, .48, 1.27, t], [.015, .008, .028])
    payments = np.r_[np.arange(0, 2.01, .25), t]
    density = lambda u: df(u) * h.survival_probability(u) * h.hazard_rate(u)
    breaks = np.union1d(times, h.edges)
    protection = .6 * quad(density, 0, t, points=breaks[(breaks>0) & (breaks<t)], epsabs=1e-13)[0]
    accrued = sum(quad(lambda u: density(u)*(u-l), l, r, points=breaks[(breaks>l) & (breaks<r)],
                       epsabs=1e-13)[0] for l,r in zip(payments[:-1], payments[1:]))
    legs = price_cds(t, h, df)
    assert legs.protection == pytest.approx(protection, abs=1e-13)
    assert legs.accrued_on_default_annuity == pytest.approx(accrued, abs=1e-12)


@pytest.mark.parametrize("hazards", [[.017]*5, [.002, .014, .0, .027, .035], [0]*5])
def test_bootstrap_round_trip_with_stubs(hazards):
    h = HazardCurve([0, .81, 1.29, 2.95, 4.26, 7], hazards)
    df = flat_df()
    cal = calibrate_credit(quotes_from(h, df), df, mode="DEMO")
    assert np.allclose(cal.curves["TEST"].lambdas, hazards, atol=3e-12)
    assert cal.repricing["residual_bps"].abs().max() < .01
    assert validate_credit(cal).probability_invariants.all()


def test_archive_regression(demo):
    _, _, cal = demo
    assert len(cal.repricing) == 15
    assert cal.repricing["residual_bps"].abs().max() < .01
    assert cal.repricing["within_bid_ask"].all()
    expected = [[.0075, .01, .0125, .015, .018], [.009375, .0125, .015625, .01875, .0225],
                [.006, .008, .01, .012, .0144]]
    for c, rates in zip(cal.curves.values(), expected):
        assert np.allclose(c.lambdas, rates, atol=3e-9)
    assert len(cal.probabilities(COUPON_TIMES)) == 42
    assert validate_credit(cal).probability_invariants.all()


def test_incoherent_curve_constrained_fallback():
    h, df = HazardCurve([0, 1, 2], [.02, .01]), flat_df()
    q = quotes_from(h, df)
    q.loc[1, "spread"] = 1
    with pytest.raises(ValueError, match="negative"):
        calibrate_credit(q, df, mode="DEMO")
    cal = calibrate_credit(q, df, mode="DEMO", method="constrained")
    assert cal.warnings
    assert (cal.curves["TEST"].lambdas >= 0).all()
    assert not cal.repricing["within_0_01bp"].all()
    assert set(cal.repricing["fit"]) == {"constrained"}


@pytest.mark.parametrize(("column", "value", "match"), [
    ("unit", "pips", "units"), ("quote_type", "quoted_spread", "par_spread"),
    ("quote_type", "upfront", "par_spread"), ("currency", "USD", "EUR"),
    ("spread", np.nan, "nonnegative"), ("spread", np.inf, "Nonfinite"),
    ("recovery", 1.0, "Recovery"), ("recovery", np.nan, "Recovery"),
    ("collected_at", "2026-11-05T23:01Z", "timestamps"),
    ("data_kind", "observed", "synthetic"), ("entity_id", "", "entity_id"),
    ("tenor_years", -1, "tenor"), ("time_basis", "ACT/360", "basis"),
    ("seniority", "subordinated", "Mixed"),
])
def test_reject_bad_quotes(demo, column, value, match):
    q = demo[0].copy()
    q.loc[0, column] = value
    with pytest.raises(ValueError, match=match):
        load_quotes(q, "DEMO")


def test_duplicates_and_mixed_snapshot(history, demo):
    q, df, _ = demo
    with pytest.raises(ValueError, match="Duplicate"):
        load_quotes(pd.concat([q, q.iloc[[0]]]), "DEMO")
    with pytest.raises(ValueError, match="identical"):
        calibrate_credit(history[0], df, mode="DEMO")
    with pytest.raises(ValueError, match="identical"):
        calibrate_credit(q, replace(df, valuation_date="2026-11-07"), mode="DEMO")
    with pytest.raises(ValueError, match="same time"):
        calibrate_credit(q, replace(df, time_basis="ACT/365F"), mode="DEMO")


def test_discount_coverage_and_no_implicit_tail(demo):
    q, _, cal = demo
    with pytest.raises(ValueError, match="cover"):
        calibrate_credit(q, replace(flat_df(horizon=5), time_basis="idealized_years"), mode="DEMO")
    c = cal.curves["DEMO_BANK_A"]
    with pytest.raises(ValueError, match="coverage"):
        c.survival_probability(7.1)
    with pytest.raises(ValueError, match="coverage"):
        c.hazard_rate(np.nan)
    with pytest.raises(ValueError, match="end"):
        c.default_probability(2, 3)
    tail = replace(c, extrapolate=True)
    assert tail.integrated_hazard(8) - tail.integrated_hazard(7) == pytest.approx(c.lambdas[-1])
    assert np.isinf(HazardCurve([0, 7], [0])._default_times(np.array([.2]), 7)).all()
    with pytest.raises(ValueError, match="finite"):
        DiscountCurve([0, np.nan], [1, .9], DAY)
    with pytest.raises(ValueError, match="finite"):
        HazardCurve([0, 1], [np.nan])
    with pytest.raises(ValueError, match="Factor parameters"):
        FactorParams(kappa=np.inf).validate("lognormal")


def test_csv_excel_dates_units_and_market_api(tmp_path):
    quotes = quotes_from(HazardCurve([0, 1, 2], [.01, .02]), flat_df())
    quotes["maturity_date"] = [pd.Timestamp(DAY)+pd.Timedelta(days=365*j) for j in (1, 2)]
    quotes["tenor_years"] = np.nan
    quotes.loc[0, "spread"] /= 10000
    quotes.loc[0, "unit"] = "decimal"
    quotes.loc[1, "spread"] /= 100
    quotes.loc[1, "unit"] = "percent"
    quotes["data_kind"], quotes["provider"] = "observed", "Test fixture pretending to be incoming data"
    dates = [pd.Timestamp(DAY)+pd.Timedelta(days=365*j) for j in (1, 2)]
    discount = pd.DataFrame({"valuation_date": DAY, "pillar_date": dates, "discount_factor": np.exp(-.025*np.array([1,2])),
                             "currency": "EUR", "time_basis": "ACT/365F", "data_kind": "observed", "provider": "test"})
    path1, path2 = tmp_path/"q.xlsx", tmp_path/"eur.csv"
    quotes.to_excel(path1, index=False, engine="openpyxl")
    discount.to_csv(path2, index=False, sep=";")
    df = load_discount_curves(path2, "SNAPSHOT")[pd.Timestamp(DAY)]
    assert df(0) == 1
    assert df(1.5) == pytest.approx(np.exp(-.025*1.5))
    xlsx_file = BytesIO(path1.read_bytes())
    xlsx_file.name = "uploaded.xlsx"
    cal = calibrate_credit(xlsx_file, df)
    assert np.allclose(cal.curves["TEST"].lambdas, [.01, .02], atol=1e-10)
    market = calibrate_credit(quotes, df, mode="MARKET")
    assert np.allclose(market.curves["TEST"].lambdas, [.01, .02], atol=1e-10)
    with pytest.raises(ValueError, match="authorized provider"):
        load_quotes(path1, "MARKET")


def test_stress_targets_and_sign(demo):
    q, df, cal = demo
    baseline = float(1-basket_survival(cal.curves, 7))
    stressed = credit_stress(q, df, mode="DEMO", spread_bump_bps=1)
    assert 1-basket_survival(stressed.curves, 7) > baseline
    target = credit_stress(q, df, mode="DEMO", spread_bump_bps=10, entity="DEMO_BANK_A", tenor_years=5)
    residual = target.repricing["spread_bps"] - cal.repricing["spread_bps"]
    assert np.allclose(residual.to_numpy()[[0, 1, 2, *range(4, 15)]], 0)
    assert residual.iloc[3] == pytest.approx(10)
    rec = credit_stress(q, df, mode="DEMO", recovery=.2)
    assert 1-basket_survival(rec.curves, 7) < baseline
    with pytest.raises(ValueError, match="matches no"):
        credit_stress(q, df, mode="DEMO", tenor_years=9)
    with pytest.raises(ValueError, match="nonnegative"):
        credit_stress(q, df, mode="DEMO", spread_bump_bps=-200)


def test_backtest_has_no_future_inputs_or_silent_basket_change(history):
    q, discounts = history
    dates = sorted(discounts)[:3]
    active = q[q["valuation_date"].isin(dates)].copy()
    only_current = {dates[0]: discounts[dates[0]], dates[-1]: discounts[dates[-1]]}
    before = backtest_credit(active, only_current, mode="DEMO")
    assert isinstance(before, Backtest)
    assert list(before.status["status"]) == ["calibrated", "skipped", "calibrated"]
    assert before.holdout["status"].eq("predicted").all()
    assert np.max(np.abs(before.holdout["error_bps"])) > .001
    active.loc[active["valuation_date"].eq(dates[-1]), ["spread", "bid", "ask"]] *= 1.2
    after = backtest_credit(active, only_current, mode="DEMO")
    pd.testing.assert_series_equal(before.daily.iloc[0], after.daily.iloc[0])
    assert before.daily["first_default_pd"].iloc[-1] < after.daily["first_default_pd"].iloc[-1]
    missing_bank = active[~(active["valuation_date"].eq(dates[-1]) & active["entity_id"].eq("DEMO_BANK_C"))]
    failed = backtest_credit(missing_bank, only_current, mode="DEMO")
    assert failed.status["status"].iloc[-1] == "skipped"


def test_independent_first_default_mc_and_absorption(demo):
    _, _, cal = demo
    grid = np.r_[0, COUPON_TIMES]
    n = 80000
    result = simulate_credit(cal.curves, grid, n)
    expected = basket_survival(cal.curves, grid)
    mc = result.basket_alive.mean(axis=0)
    se = np.sqrt(expected*(1-expected)/n)
    assert np.all(np.abs(mc-expected) <= 4.3*se + 1e-12)
    assert np.array_equal(result.first_default_times, result.default_times.min(axis=1))
    assert np.array_equal(result.basket_alive, result.first_default_times[:, None] > grid)
    assert not np.any(result.basket_alive[:, 1:] & ~result.basket_alive[:, :-1])
    for bank in result.entities:
        mask = result.bank_alive(bank)
        assert not np.any(mask[:, 1:] & ~mask[:, :-1])
    assert basket_diagnostics(result).iloc[-1]["pd_ci95_low"] < 1 - expected[-1]
    assert np.array_equal(result.default_times[:100], simulate_credit(cal.curves, grid, 100).default_times)


@pytest.mark.parametrize("kind", ["lognormal", "cirpp"])
def test_stochastic_survival_and_nonnegative_out_of_sample(demo, kind):
    _, _, cal = demo
    grid = np.linspace(0, 7, 85)
    fit = fit_stochastic(cal.curves, grid, kind, n_calibration=22000)
    z = scenario_normals(grid, 30000, 9123)
    result = simulate_credit(cal.curves, grid, len(z), model=fit, credit_innovations=z)
    assert np.max(np.abs(fit.targets - fit.fit_survival)) < 1e-10
    assert np.max(np.abs(fit.targets - result.conditional_mean_survival)) < 5e-4
    assert result.min_intensity >= 0
    assert np.isfinite(result.factor).all()
    assert np.array_equal(result.basket_alive, result.first_default_times[:, None] > grid)
    assert result.bank_alive(result.entities[0]).shape == (len(z), len(grid))
    assert np.array_equal(z[:100], scenario_normals(grid, 100, 9123))
    copy = simulate_credit(cal.curves, grid, 100, model=fit, credit_innovations=z[:100])
    assert np.array_equal(copy.default_times, result.default_times[:100])
    assert np.array_equal(copy.factor, result.factor[:100])


def test_dependence_and_zero_loading_limit():
    grid = np.linspace(0, 7, 85)
    curves = {str(i): HazardCurve([0, 7], [.045]) for i in range(5)}
    n = 30000
    z = scenario_normals(grid, n, 123)
    def sim(beta):
        fit = fit_stochastic(curves, grid, "lognormal", FactorParams(kappa=.2, sigma=.9, beta=beta))
        return simulate_credit(curves, grid, n, model=fit, credit_innovations=z)
    independent = simulate_credit(curves, grid, n)
    assert np.array_equal(sim(0).basket_alive, independent.basket_alive)
    correlated = sim(1.5)
    assert correlated.basket_alive[:, -1].mean() > independent.basket_alive[:, -1].mean() + .06
    assert np.max(np.abs(correlated.conditional_mean_survival[:, -1] - np.exp(-.045*7))) < .004


def test_cir_positivity_gate_and_unshifted_marginal_convergence(demo):
    _, _, cal = demo
    grid = np.linspace(0, 7, 85)
    with pytest.raises(ValueError, match="negative continuous shift"):
        fit_stochastic(cal.curves, grid, "cirpp", FactorParams(sigma=.035, theta=.02, y0=.02))
    with pytest.raises(ValueError, match="Feller"):
        fit_stochastic(cal.curves, grid, "cirpp", FactorParams(sigma=.3))
    p = FactorParams(sigma=.035)
    assert cir_forward_hazard(0, p) == pytest.approx(p.y0)
    theoretical = np.exp(-quad(lambda t: cir_forward_hazard(t, p), 0, 7)[0])
    n = 26000
    # The exact calibrated shift factors out of simulated survival.
    errors = []
    for steps in (7, 28, 84):
        g = np.linspace(0, 7, steps+1)
        fit = fit_stochastic(cal.curves, g, "cirpp", p, n_calibration=4000)
        result = simulate_credit(cal.curves, g, n, model=fit,
                                 credit_innovations=scenario_normals(g, n, 924))
        unshifted = np.exp(-(result.factor[:, :-1] * np.diff(g)).sum(axis=1))
        errors.append(abs(unshifted.mean() - theoretical))
    assert errors[-1] < .00035
    assert errors[-1] < errors[0]


@pytest.mark.parametrize("matrix", [
    [[1, .2], [.2, 1]], [[1,.3,.4],[.2,1,0],[.4,0,1]], [[.9,.2,0],[.2,1,0],[0,0,1]],
    [[1,1,0],[1,1,0],[0,0,1]], [[1,.9,.9],[.9,1,-.9],[.9,-.9,1]],
])
def test_cholesky_input_validation(matrix):
    with pytest.raises(ValueError):
        validate_correlation(matrix)


def test_pricing_integration_and_no_double_cholesky(demo):
    q, df, cal = demo
    # Pretend these came from the groups. The credit module receives only column 2.
    matrix = validate_correlation([[1, -.25, .35], [-.25, 1, -.4], [.35, -.4, 1]])
    g = np.linspace(0, 7, 29)
    raw = np.random.default_rng(52).normal(size=(12000, 28, 3))
    all_z = raw @ np.linalg.cholesky(matrix).T
    empirical = np.corrcoef(all_z.reshape(-1, 3), rowvar=False)
    assert np.max(np.abs(empirical-matrix)) < .008
    fit = fit_stochastic(cal.curves, g, "lognormal", n_calibration=3000)
    result = simulate_credit(cal.curves, g, len(all_z), model=fit, credit_innovations=all_z[:, :, 2])
    k, s = fit.params.kappa, fit.params.sigma
    expected = s * np.sqrt(-np.expm1(-2*k*.25)/(2*k)) * all_z[:, 0, 2]
    assert np.array_equal(result.factor[:, 1], expected)
    assert result.default_times.shape == (len(all_z), 3)
    assert result.basket_alive[:, ::2].shape == (len(all_z), 15)
    assert df.valuation_date == q["valuation_date"].iloc[0]
    with pytest.raises(ValueError, match="shape"):
        simulate_credit(cal.curves, g, len(raw), model=fit, credit_innovations=all_z)
    with pytest.raises(ValueError, match="exact grid"):
        simulate_credit(cal.curves, g[::2], len(raw), model=fit, credit_innovations=all_z[:, ::2, 2])
    with pytest.raises(ValueError, match="do not consume"):
        simulate_credit(cal.curves, g, len(raw), credit_innovations=all_z[:, :, 2])


def test_joint_cds_matches_when_factors_are_deterministic():
    c = HazardCurve([0, .55, 1.5, 3], [.015, .015, .015])
    df = flat_df()
    g = np.linspace(0, 3, 36)
    fit = fit_stochastic({"A": c}, g, "lognormal", FactorParams(sigma=0), n_calibration=80)
    result = simulate_credit({"A": c}, g, 120, model=fit, credit_innovations=np.zeros((120, 35)))
    target = price_cds(2.28, c, df).fair_spread_bps
    prices = reprice_joint_cds(2.28, "A", .4, result, np.broadcast_to(df(g), (120, len(g))),
                               reference_bps=target)
    assert abs(prices["difference_bps"]) < 1e-8
    assert prices["spread_mc_se_bps"] < 1e-12


def test_external_quantlib_with_calendar_aligned_integral_engine():
    ql = pytest.importorskip("QuantLib", reason="Optional independent CDS comparison")
    anchor = ql.Date(2, 1, 2026)
    settings = ql.Settings.instance()
    previous = settings.evaluationDate
    settings.evaluationDate = anchor
    daycount = ql.Actual365Fixed()
    maturity = anchor + 365
    schedule = ql.Schedule([anchor, anchor+91, anchor+182, anchor+273, maturity])
    rate, hazard, rec, coupon = .025, .017, .4, .01
    df = flat_df(rate)
    own_curve = HazardCurve([0, 7], [hazard])
    qdf = ql.YieldTermStructureHandle(ql.FlatForward(anchor, rate, daycount))
    qhaz = ql.DefaultProbabilityTermStructureHandle(ql.FlatHazardRate(
        anchor, ql.QuoteHandle(ql.SimpleQuote(hazard)), daycount))
    evidence = []
    try:
        for accrued in (False, True):
            cds = ql.CreditDefaultSwap(ql.Protection.Buyer, 1., coupon, schedule, ql.Unadjusted,
                                       daycount, accrued, True, anchor, ql.FaceValueClaim(),
                                       daycount, False, anchor, 0)
            cds.setPricingEngine(ql.IntegralCdsEngine(ql.Period(1, ql.Days), qhaz, rec, qdf))
            ours = price_cds(1, own_curve, df, rec, premium_interval=91/365, accrue_on_default=accrued)
            diff = ours.fair_spread_bps - cds.fairSpread()*10000
            pv_delta = ours.buyer_pv(coupon*10000) - cds.NPV()
            evidence.append({"accrual": accrued, "quantlib_version": ql.__version__,
                             "reference_bps": cds.fairSpread()*10000, "engine_bps": ours.fair_spread_bps,
                             "difference_bps": diff, "buyer_pv_difference_per_unit": pv_delta})
            assert abs(diff) < .01
            assert abs(pv_delta) < 1e-6
    finally:
        settings.evaluationDate = previous
    audit_evidence("quantlib_comparison", evidence)


def test_streamlit_demo_paths(monkeypatch):
    st_testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    ap = st_testing.AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=60).run()
    assert not ap.exception
    assert not ap.error
    assert len(ap.tabs) == 4
    assert any("synthétiques" in element.value for element in ap.warning)
    assert len(ap.get("plotly_chart")) == 9
    model_input = next(m for m in ap.multiselect if m.label == "Modèles stochastiques à comparer")
    model_input.select("cirpp").run()
    assert not ap.exception
    assert not ap.error
    assert not any("CIR++ ·" in element.value for element in ap.warning)
    stress = next(element for element in ap.selectbox if element.label == "Choc CDS (pb)")
    original = next(element.value for element in ap.metric if "stressée" in element.label)
    stress.set_value(50).run()
    assert not ap.exception
    assert next(element.value for element in ap.metric if "stressée" in element.label) != original
    ap.session_state["edit_quotes"] = {
        "edited_rows": {3: {"spread_scenario_bps": 76.332363}},
        "added_rows": [], "deleted_rows": [],
    }
    ap.button(key="apply_quotes").click().run()
    assert not ap.exception
    assert any("Scénario édité" in element.value for element in ap.success)
    assert not any("(+0.00 pp)" in element.value for element in ap.success)
    before = next(element.value for element in ap.metric if "stressée" in element.label)
    next(widget for widget in ap.selectbox if widget.label == "Recovery").set_value(.2).run()
    assert next(element.value for element in ap.metric if "stressée" in element.label) != before
    theta = next(widget for widget in ap.number_input if widget.label == "θ CIR (1/an)")
    theta.set_value(.02).run()
    assert any("negative continuous shift" in item.value for item in ap.warning)
    date = next(widget for widget in ap.selectbox if widget.label == "Date de valorisation")
    date.set_value(pd.Timestamp("2026-05-06")).run()
    assert any(metric.value == "2026-05-06" for metric in ap.metric)
    ap.multiselect(key="basket_DEMO").set_value(["DEMO_BANK_A"]).run()
    assert any(metric.label == "Banques dans le panier" and metric.value == "1" for metric in ap.metric)
    assert not ap.error
    ap.radio[0].set_value("MARKET").run()
    assert any("Aucun flux" in element.value for element in ap.info)
    ap.radio[0].set_value("SNAPSHOT").run()
    assert any("Chargez" in element.value for element in ap.info)



def test_bootstrap_resolves_positive_hazards_near_zero_and_near_recovery_boundary():
    df = flat_df()
    for rates, recovery in (([1e-15, 3e-13, 1e-12], .4), ([.003, .024, .011], .999)):
        hazard = HazardCurve([0, .11, 2.73, 6.11], rates)
        q = quotes_from(hazard, df, recovery)
        cal = calibrate_credit(q, df, mode="DEMO")
        np.testing.assert_allclose(cal.curves["TEST"].lambdas, rates, rtol=4e-4, atol=3e-19)
        assert cal.repricing["within_0_01bp"].all()


def test_constrained_fit_respects_quote_precision():
    df = flat_df()
    q = quotes_from(HazardCurve([0, 1, 2], [.03, .02]), df)
    q["spread"] = [180., 30.]
    unweighted = calibrate_credit(q, df, mode="DEMO", method="constrained")
    q["bid"] = q["spread"] - [.25, 20]
    q["ask"] = q["spread"] + [.25, 20]
    fit = calibrate_credit(q, df, mode="DEMO", method="constrained")
    assert "bid-ask-weighted" in fit.warnings[0]
    assert fit.repricing["fit_residual_scale_bps"].min() > 0
    assert fit.repricing.residual_bps.abs().iloc[0] < unweighted.repricing.residual_bps.abs().iloc[0]
    assert np.count_nonzero(~fit.repricing["within_0_01bp"]) >= 1
    assert (fit.curves["TEST"].lambdas >= 0).all()


def test_discount_provenance_and_material_curve_change(demo):
    q, df, reference = demo
    q = q.copy()
    changed = replace(df, dfs=np.exp(-.14 * df.times), provider="Alternative synthetic EUR")
    cal = calibrate_credit(q, changed, mode="DEMO")
    assert cal.repricing["discount_provider"].eq(changed.provider).all()
    assert np.max(np.abs(reference.curves["DEMO_BANK_A"].lambdas - cal.curves["DEMO_BANK_A"].lambdas)) > 1e-4
    assert cal.repricing["residual_bps"].abs().max() < .01
    with pytest.raises(ValueError, match="provenance"):
        calibrate_credit(q, replace(df, data_kind="observed"), mode="DEMO")
    q["reference_entity_id"] = ["REF1", "REF2", *(["REF1"] * (len(q) - 2))]
    with pytest.raises(ValueError, match="Reference identity"):
        load_quotes(q, "DEMO")


def test_snapshot_cli_partial_curve_and_metadata(tmp_path):
    import argparse
    from credit_engine import _run_snapshot

    df = flat_df()
    q = quotes_from(HazardCurve([0, 1, 3, 5], [.01, .02, .04]), df)
    q["data_kind"] = "observed"  # Schema exercise only. Every row here is a synthetic fixture.
    q["provider"], q["source_url"] = "SYNTHETIC TEST FIXTURE", "https://example.invalid/fixture"
    q["reference_entity_id"], q["documentation"] = "FAKE-RED", "fixture"
    q["available_at"] = DAY+"T15:00:00Z"
    qpath = tmp_path/"test_fixture.csv"
    dpath = tmp_path/"test_curve.xlsx"
    q.to_csv(qpath,index=False)
    pd.DataFrame({"valuation_date": DAY, "time_years": df.times, "discount_factor": df.dfs,
                  "time_basis": "ACT/365F", "currency": "EUR", "provider": "SYNTHETIC TEST FIXTURE",
                  "data_kind": "observed", "available_at": DAY+"T16:00Z"}).to_excel(dpath,index=False)
    output = tmp_path/"replay"
    args = argparse.Namespace(quotes=qpath, discount=dpath, date=DAY, bank=["TEST"], method="strict",
                              extrapolate=False, backtest_start=DAY, backtest_end=DAY,
                              require_point_in_time=True, output=output)
    _run_snapshot(args)
    summary = json.loads((output/"diagnostics.json").read_text())
    assert summary["reported_horizon_years"] == 5.
    assert summary["horizon_uses_extrapolation"] is False
    prices = pd.read_csv(output/"repricing.csv")
    assert prices["reference_entity_id"].eq("FAKE-RED").all()
    assert prices["discount_provider"].eq("SYNTHETIC TEST FIXTURE").all()
    probs = pd.read_csv(output/"probabilities_semiannual.csv")
    assert probs.iloc[-1]["time_status"] == "calibration_pillar"
    assert "between_pillars" in set(probs["time_status"])
    assert pd.isna(pd.read_csv(output/"backtest_daily.csv")["first_default_pd"].iloc[0])
    real_schema_df = load_discount_curves(dpath, "SNAPSHOT")[pd.Timestamp(DAY)]
    scenario = credit_stress(qpath, real_schema_df, spread_overrides_bps={("TEST", 3.): 88.885})
    diagnostic = scenario.repricing
    assert diagnostic["data_kind"].eq("scenario").all()
    assert diagnostic["source_data_kind"].eq("observed").all()
    assert diagnostic["spread_is_stressed"].sum() == 1
    changed = diagnostic.loc[diagnostic["spread_is_stressed"]].iloc[0]
    assert changed.spread_bps == 88.885 and changed.source_spread_bps != changed.spread_bps
    with pytest.raises(ValueError, match="data_kind=observed"):
        load_quotes(diagnostic, "SNAPSHOT")
    args.extrapolate = True
    _run_snapshot(args)
    summary = json.loads((output/"diagnostics.json").read_text())
    assert summary["reported_horizon_years"] == 7.
    assert summary["horizon_uses_extrapolation"] is True
    assert pd.read_csv(output/"probabilities_semiannual.csv").iloc[-1]["time_status"] == "extrapolated_flat"


def test_replay_rejects_late_versions_and_distinguishes_archive_time(history):
    q, discounts = history
    days = sorted(discounts)[-3:]
    q = q[q["valuation_date"].isin(days)].copy()
    q["available_at"] = q["valuation_date"].dt.tz_localize("UTC") + pd.Timedelta(hours=15)
    q["collected_at"] = pd.Timestamp("2026-11-17T18:00:00Z")  # later export, not publication
    # A single revised quote published tomorrow disqualifies its same-day snapshot.
    index = q.index[q["valuation_date"].eq(days[1])][0]
    q.loc[index,"available_at"] += pd.Timedelta(days=1)
    stamped = {day: replace(discounts[day], available_at=day+pd.Timedelta(hours=16)) for day in days}
    stamped[days[2]] = replace(stamped[days[2]], available_at=None)
    dated = backtest_credit(q, stamped, mode="DEMO", holdout_extra_tenors=(3,))
    assert list(dated.status["status"]) == ["calibrated","skipped","calibrated"]
    assert list(dated.status["availability_evidence"]) == ["asof_timestamp","late","valuation_date_only"]
    assert set(dated.holdout["target_tenor_years"]) == {3,5}
    assert len(dated.holdout.query("status == 'predicted'")) == 12
    strict = backtest_credit(q, stamped, mode="DEMO", require_point_in_time=True)
    assert list(strict.status["status"]) == ["calibrated","skipped","skipped"]
    assert strict.status.iloc[-1]["availability_evidence"] == "missing"


def test_joint_covariance_paired_control_and_boundary():
    grid = np.linspace(0, 7, 85)
    n = 10000
    rng = np.random.default_rng(519)
    r, z = rng.standard_normal((2, n, 84))
    hazard = HazardCurve([0, 3, 7], [.012, .021])
    discount = flat_df()
    with pytest.raises(ValueError, match="one row per antithetic pair"):
        fit_stochastic({"A": hazard}, grid, "lognormal", n_calibration=8, calibration_innovations=np.zeros((4, 85)))
    fit = fit_stochastic({"A": hazard}, grid, "lognormal", FactorParams(sigma=.9), n_calibration=8000)
    credit = simulate_credit({"A": hazard}, grid, n, model=fit, credit_innovations=z)
    anchor = discount(grid)
    spread = price_cds(7, hazard, discount).fair_spread_bps

    def discount_paths(rho):
        shock = .05 * np.cumsum((rho*z + np.sqrt(1-rho**2)*r)*np.sqrt(np.diff(grid)),axis=1)
        return anchor * np.exp(np.c_[np.zeros(n),-shock] - .05**2*grid/2)

    independent = discount_paths(0)
    identity = reprice_joint_cds(np.nextafter(7, np.inf), "A", .4, credit, independent,
                                reference_bps=spread, comparison_discounts=independent)
    assert identity["paired_spread_difference_bps"] == identity["paired_spread_mc_se_bps"] == 0.
    for rho in (-.65, .65):
        row = reprice_joint_cds(7, "A", .4, credit, discount_paths(rho), reference_bps=spread,
                                comparison_discounts=independent)
        assert -np.sign(rho) * row["paired_spread_difference_bps"] > 4 * row["paired_spread_mc_se_bps"]
        assert row["paired_spread_mc_se_bps"] < row["spread_mc_se_bps"]
    with pytest.raises(ValueError, match="Joint CDS maturity"):
        reprice_joint_cds(7.01, "A", .4, credit, independent, reference_bps=spread)


def test_quantlib_matrix_curve_shape_recovery_and_maturity():
    ql = pytest.importorskip("QuantLib", reason="Optional independent CDS comparison")
    anchor = ql.Date(2, 1, 2026)
    daycount = ql.Actual365Fixed()
    settings = ql.Settings.instance()
    previous = settings.evaluationDate
    settings.evaluationDate = anchor
    cases = [
        ("low_1Y", [0,365], [.0004], [0,365], [0,.025], .05),
        ("stub_piecewise", [0,169,437,857], [.007,.035,.017],
         [0,71,452,857], [0,.001,.042,.059], .4),
        ("high_7Y", [0,578,1430,2555], [.19,.068,.125],
         [0,105,583,2220,2555], [0,.009,.081,.24,.26], .65),
        ("inverted_forward", [0,323,590], [.022,.042], [0,71,366,590], [0,-.014,-.016,.006], .2),
    ]
    evidence = []
    try:
        for name, hdays, rates, ddays, neglogdf, recovery in cases:
            maturity = hdays[-1]
            times = np.asarray(ddays)/365
            dfs = np.exp(-np.asarray(neglogdf))
            hazard = HazardCurve(np.asarray(hdays)/365, rates)
            discount = DiscountCurve(times, dfs, DAY)
            qdf = ql.YieldTermStructureHandle(ql.DiscountCurve([anchor+d for d in ddays], dfs.tolist(), daycount))
            qhaz = ql.DefaultProbabilityTermStructureHandle(
                ql.HazardRateCurve([anchor+d for d in hdays], [rates[0], *rates], daycount))
            assert qhaz.survivalProbability(anchor+maturity) == pytest.approx(hazard.survival_probability(maturity/365))
            schedule = ql.Schedule([anchor+d for d in range(0,maturity,91)] + [anchor+maturity])
            for accrue in (False, True):
                instrument = ql.CreditDefaultSwap(ql.Protection.Buyer, 1., .01, schedule, ql.Unadjusted,
                                                   daycount, accrue, True, anchor, ql.FaceValueClaim(),
                                                   daycount, False, anchor, 0)
                own = price_cds(maturity/365, hazard, discount, recovery, premium_interval=91/365,
                                accrue_on_default=accrue)
                row = {"case": name, "accrual": accrue, "maturity_years": maturity/365,
                       "recovery": recovery, "analytic_spread_bps": own.fair_spread_bps,
                       "analytic_pv": own.buyer_pv(100), "results": []}
                forward_bound = np.max(np.abs(np.diff(neglogdf)/np.diff(times)))
                for step in (2,1):
                    instrument.setPricingEngine(ql.IntegralCdsEngine(ql.Period(step,ql.Days),
                                                                     qhaz,recovery,qdf))
                    pv = own.buyer_pv(100)-instrument.NPV()
                    spread = own.fair_spread_bps-instrument.fairSpread()*10000
                    row["results"].append({"integration_days": step, "spread_difference_bps": spread,
                                           "pv_difference_per_unit": pv})
                    # Discounting at the end of a daily step versus at default, plus
                    # at most one step of accrued premium on that default mass.
                    default_mass = hazard.default_probability(maturity/365)
                    bound = np.expm1(forward_bound*step/365) * (
                        own.protection + .01*own.accrued_on_default_annuity)
                    bound += .01*step/365*max(dfs) * default_mass if accrue else 0
                    assert abs(pv) < bound + 1e-11
                assert abs(row["results"][-1]["spread_difference_bps"]) < .14
                evidence.append(row)
    finally:
        settings.evaluationDate = previous
    audit_evidence("quantlib_matrix", {"quantlib_version": ql.__version__, "cases": evidence})


def test_public_ecb_documented_pillars_and_reference():
    from credit_engine import BASE, public_discount_data

    date = pd.Timestamp("2026-11-06")
    for source, expected_kind in [("ecb_aaa", "ecb_sovereign_aaa"), ("ecb_all", "ecb_sovereign_all")]:
        rates, days = public_discount_data(source)
        assert len(days) == 140
        curve = days[date]
        row = rates.loc[rates["valuation_date"].eq(str(date.date())) & rates["time_years"].eq(7)].iloc[0]
        assert curve.curve_kind == expected_kind
        assert curve(7.) == pytest.approx(np.exp(-.07*row["observed_rate_percent"]))
        assert curve(0.) == 1 and curve.times[-1] == 10.
    estr = pd.read_csv(BASE / "data/market/estr.csv")
    assert len(estr) == 140 and estr["series_key"].eq("EST.B.EU000A2X2A25.WT").all()
    assert estr["valuation_date"].is_unique and "discount_factor" not in estr


def test_ecb_parser_checks_dates_units_duplicates_and_completeness():
    from credit_engine import ECB_TENORS, parse_ecb_csv

    day = "2026-11-06"
    stamp = "2026-11-18T10:00:00Z"
    feed = pd.DataFrame([{"KEY": "YC.B.U2.EUR.4F.G_N_A.SV_C_YM."+code,
                         "TIME_PERIOD": day, "OBS_VALUE": 2.5, "OBS_STATUS": "A", "FREQ": "B",
                         "UNIT": "PCPA", "UNIT_MULT": 0, "CURRENCY": "EUR", "INSTRUMENT_FM": "G_N_A",
                         "PROVIDER_FM_ID": "SV_C_YM", "DATA_TYPE_FM": code} for code in ECB_TENORS])

    def decode(table):
        return parse_ecb_csv(table.to_csv(index=False).encode(), "ecb_aaa", day, day, collected_at=stamp)

    parsed = decode(feed)
    assert parsed.loc[parsed["time_years"].eq(7), "discount_factor"].iloc[0] == pytest.approx(np.exp(-.175))
    for invalid in (feed.assign(UNIT="DECIMAL"), feed.assign(OBS_VALUE="NaN"), feed.iloc[:-1],
                    pd.concat([feed, feed.iloc[:1]]), feed.assign(TIME_PERIOD="2026-11-07")):
        with pytest.raises(ValueError):
            decode(invalid)


def test_public_refresh_retains_cache_after_failure(monkeypatch, tmp_path):
    import credit_engine as engine

    cache = tmp_path / "proxies/ecb_all.csv"
    cache.parent.mkdir()
    good = (engine.BASE / "data/proxies/ecb_all.csv").read_bytes()
    cache.write_bytes(good)
    for failure in (OSError("HTTP 403"), TimeoutError("offline")):
        def unavailable(_):
            raise failure
        monkeypatch.setattr(engine, "_public_get", unavailable)
        with pytest.raises((OSError, TimeoutError)):
            engine.refresh_ecb("ecb_all", "2026-11-05", "2026-11-06", root=tmp_path)
        assert cache.read_bytes() == good
    monkeypatch.setattr(engine, "_public_get", lambda _: b"<html>Service unavailable</html>")
    with pytest.raises(ValueError):
        engine.refresh_ecb("ecb_all", "2026-11-05", "2026-11-06", root=tmp_path)
    assert cache.read_bytes() == good


def test_public_http_does_not_retry_denial_or_long_throttle(monkeypatch):
    from urllib.error import HTTPError
    import urllib.request
    import credit_engine as engine

    calls = []
    for code, headers in [(403, {}), (429, {"Retry-After": "60"})]:
        def deny(request, timeout):
            calls.append(timeout)
            raise HTTPError(request.full_url, code, "mock", headers, None)
        monkeypatch.setattr(urllib.request, "urlopen", deny)
        with pytest.raises(OSError, match=f"HTTP {code}"):
            engine._public_get("https://example.invalid/")
    assert calls == [8, 8]


def test_trade_based_ois_rebuilds_observed_strip_and_rejects_missing_pillar():
    from credit_engine import BASE, build_ois_curve, public_discount_data

    folder = BASE / "data/market"
    prints = pd.read_csv(folder / "ois_trades.csv", dtype={"Dissemination Identifier": str})
    fixings = pd.read_csv(folder / "estr.csv")
    date = "2026-11-06"
    prints = prints[prints["valuation_date"].eq(date)]
    rebuild = build_ois_curve(prints, fixings)
    assert prints["Dissemination Identifier"].is_unique
    assert rebuild.loc[rebuild["tenor"].eq("7Y"), "observed_rate_percent"].iloc[0] == pytest.approx(2.9988)
    assert rebuild["overnight_reference_date"].eq("2026-11-05").all()
    assert rebuild["repricing_error_bps"].dropna().abs().max() < 1e-7
    _, curves = public_discount_data("dtcc_ois")
    assert np.allclose(curves[pd.Timestamp(date)].dfs[1:], rebuild["discount_factor"])
    with pytest.raises(ValueError, match="7Y"):
        build_ois_curve(prints[~prints["tenor"].eq("7Y")], fixings)


def test_dtcc_audit_has_no_unauthorized_coupon_to_par_conversion():
    from credit_engine import BASE

    tape = pd.read_csv(BASE / "data/market/dtcc.csv", dtype={"Dissemination Identifier": str})
    one = tape.loc[tape["Dissemination Identifier"].eq("5557523115000000101")].iloc[0]
    assert one["exclusion_reason"] == "linked_correction_or_error"
    assert not tape["eligible_for_calibration"].any()
    assert tape["Dissemination Identifier"].is_unique and tape["bank_name"].nunique() == 8
    assert tape.shape[0] == 735
    assert (tape["exclusion_reason"] == "spread_coexists_with_fixed_coupon").any()


def test_transaction_study_recovers_public_bnp_and_independent_subordinated_basket():
    from credit_engine import BASE, run_transaction_study, transaction_credit_data

    credits = transaction_credit_data()
    expected = credits.loc[(credits["valuation_date"] == "2026-06-25") & (credits["seniority"] == "senior")]
    assert expected["bank_name"].tolist() == ["BNP Paribas SA"]
    one = expected.iloc[0]
    assert one["spread_bps"] == pytest.approx(39.75)
    assert str(one["maturity_date"]) == "2031-06-20"
    assert one["source_quote_type"] == "transaction_closing_spread"
    assert one["quote_type"] == "par_spread"  # ECE model-side mapping
    assert one["dtcc_dissemination_id"] == "3864990514000000401"
    assert not credits.duplicated(["valuation_date", "entity_id", "tenor_years"]).any()

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        run_transaction_study(Path(tmp))
        fits = pd.read_csv(Path(tmp) / "transaction_repricing_sensitivity.csv")
        basket = pd.read_csv(Path(tmp) / "transaction_first_default.csv")
    assert fits["all_invariants_pass"].all() and fits["residual_bps"].abs().max() < 1e-7
    base = (fits["valuation_date"].eq("2026-05-20") & fits["discount_case"].eq("dtcc_ois")
            & fits["scenario"].eq("baseline"))
    aligned = fits[base]
    assert aligned["seniority"].eq("subordinated").all() and len(aligned) == 3
    corresponding = basket[basket["valuation_date"].eq("2026-05-20") &
                           basket["discount_case"].eq("dtcc_ois") & basket["scenario"].eq("baseline")]
    fitted = corresponding[corresponding["time_status"].eq("inside_calibration_domain")].iloc[0]
    assert fitted["pd_first_independent"] == pytest.approx(1-aligned["survival"].prod())
    tail = corresponding[corresponding["horizon_years"].eq(7)].sort_values("tail_multiplier")
    assert len(tail) == 3 and tail["pd_first_independent"].is_monotonic_increasing
    assert all(len(key) == 19 for key in credits["dtcc_dissemination_id"])


def test_public_streamlit_has_four_tabs_and_genuine_transaction_display():
    from streamlit.testing.v1 import AppTest
    from credit_engine import BASE

    app = AppTest.from_file(str(BASE / "app.py"), default_timeout=40)
    app.session_state["credit_mode"] = "PUBLIC"
    app.run()
    assert not app.exception
    assert [item.label for item in app.tabs] == ["Calibration CDS", "Premier défaut & corrélation",
                                               "Risque, stress & backtest", "Condition de crédit du coupon"]
    assert any("Spreads de clôture Tradeweb" in item.value for item in app.info)
    assert any("Collecte" in item.value for item in app.caption)
    assert any("Repricing des spreads traités" in item.value for item in app.subheader)
    assert any("OPTION B" in item.value for item in app.warning)
    assert any("Condition satisfaite au 14e semestre" == item.label for item in app.metric)
    assert any("SCENARIO" in item.value for item in app.warning)



def _export_audit_csv(name, rows):
    destination = os.environ.get("ECE_VALIDATION_OUTPUT")
    if destination:
        pd.DataFrame(rows).to_csv(Path(destination, f"{name}.csv"), index=False)


def _ql_date(ql, value):
    value = pd.Timestamp(value)
    return ql.Date(value.day, value.month, value.year)


def _ql_discount(ql, day, curve):
    days = curve.times * 365
    days = np.where(np.abs(days-np.rint(days)) < 1e-8, np.rint(days), days)
    offsets = np.unique(np.r_[np.floor(days), np.ceil(days)]).astype(int)
    anchor = _ql_date(ql, day)
    dates = [anchor + int(n) for n in offsets]
    return ql.YieldTermStructureHandle(ql.DiscountCurve(
        dates, curve(offsets/365).tolist(), ql.Actual365Fixed(), ql.TARGET()))


def test_public_tradeweb_mapping_is_academic_and_rejects_identity_errors(tmp_path):
    from credit_engine import BASE, discount_sensitivity, public_discount_data, transaction_credit_data

    source = pd.read_csv(BASE / "data/market/tradeweb.csv", dtype={"dtcc_dissemination_id": str})
    mapped = transaction_credit_data()
    assert len(mapped) == 21 and mapped["valuation_date"].nunique() == 18 and mapped.bank_name.nunique() == 6
    assert mapped.dtcc_dissemination_id.is_unique
    assert mapped["quote_quality"].eq("option_B_academic_equivalence").all()
    assert not mapped["market_par_verified"].any()
    assert mapped["source_quote_type"].eq("transaction_closing_spread").all()
    assert not {"signed_upfront", "market_par_bps"} & set(mapped)
    bnp = mapped.loc[mapped["entity_id"].eq("QZ1ZPP7V49J6")].iloc[0]
    assert bnp["spread_bps"] == 39.75 and bnp["coupon_bps"] == 100
    assert bnp["dtcc_notional_capped"] and bnp["dtcc_upfront_amount_unsigned"] > 0
    _, discounts = public_discount_data("dtcc_ois")
    day = pd.Timestamp("2026-05-20")
    mixed_tiers = mapped[mapped["valuation_date"].eq(day)].copy()
    mixed_tiers.loc[mixed_tiers.index[0], "seniority"] = "senior"
    with pytest.raises(ValueError, match="debt tier"):
        discount_sensitivity(mixed_tiers, {"ois": discounts[day]}, mode="SNAPSHOT")
    market = tmp_path / "market"
    market.mkdir()
    for field, bad, message in [
        ("spread", 100., "distinct from its coupon"),
        ("entity_id", "BAD_UPI", "UPI, bank"),
        ("seniority", "senior_non_preferred", "debt tier"),
        ("unit", "percent", "type or unit"),
    ]:
        corrupted = source.copy()
        corrupted.loc[0, field] = bad
        corrupted.to_csv(market / "tradeweb.csv", index=False)
        with pytest.raises(ValueError, match=message):
            transaction_credit_data(root=tmp_path)


def test_independent_isda_cds_sensitivity_and_unsigned_cash_comparison():
    from scipy.optimize import brentq
    from credit_engine import public_discount_data, transaction_credit_data

    ql = pytest.importorskip("QuantLib")
    frame = transaction_credit_data()
    prior_date = ql.Settings.instance().evaluationDate
    disc = {key: public_discount_data(key)[1] for key in ("dtcc_ois", "ecb_aaa", "ecb_all")}
    results = []
    settings = ql.Settings.instance()
    try:
        for day in map(pd.Timestamp, ("2026-05-20", "2026-06-25")):
            settings.evaluationDate = trade = _ql_date(ql, day)
            cal = ql.TARGET()
            cash = cal.advance(trade, 3, ql.Days)
            snapshot = frame[frame["valuation_date"].eq(day)]
            for row in snapshot.itertuples():
                exact_mat = _ql_date(ql, row.maturity_date)
                schedule = ql.Schedule(trade, exact_mat, ql.Period(3, ql.Months), cal,
                                       ql.Following, ql.Unadjusted, ql.DateGeneration.CDS2015, False)
                assert schedule[-1] == exact_mat
                step_in = trade + 1
                assert _ql_date(ql, row.effective_date) == step_in
                base = calibrate_credit(snapshot[snapshot.entity_id.eq(row.entity_id)],
                                        disc["dtcc_ois"][day], mode="SNAPSHOT").repricing.iloc[0]
                for rate_case, recovery, accrual in [
                    ("dtcc_ois", row.recovery, "ACT/360"),
                    ("dtcc_ois", row.recovery, "ACT/365F"),
                    ("ecb_aaa", row.recovery, "ACT/360"),
                    ("ecb_all", row.recovery, "ACT/360"),
                    *(("dtcc_ois", r, "ACT/360") for r in (.20, .30, .40, .50) if r != row.recovery),
                ]:
                    discount = disc[rate_case][day]
                    handle = _ql_discount(ql, day, discount)
                    a360 = accrual == "ACT/360"
                    swap = ql.CreditDefaultSwap(ql.Protection.Buyer, 1., 0., row.coupon_bps / 1e4, schedule,
                        ql.Following, ql.Actual360() if a360 else ql.Actual365Fixed(), True, True, step_in, cash,
                        ql.FaceValueClaim(), ql.Actual360(True) if a360 else ql.Actual365Fixed(), True, trade, 3)

                    def par(hazard):
                        flat = ql.FlatHazardRate(trade, ql.QuoteHandle(ql.SimpleQuote(hazard)), ql.Actual365Fixed())
                        swap.setPricingEngine(ql.IsdaCdsEngine(ql.DefaultProbabilityTermStructureHandle(flat),
                                                              recovery, handle))
                        return swap.fairSpread() * 1e4

                    same_hazard_bps = par(float(base["hazard_annual"]))
                    fitted = brentq(lambda h: par(h)-row.spread_bps, 1e-10, .25, xtol=1e-14)
                    model_bps = par(fitted)
                    pd_ql = -np.expm1(-fitted*row.tenor_years)
                    clean = swap.fairUpfront()
                    net = clean-swap.accrualRebateNPV()/handle.discount(cash)
                    # The model cash may be compared in magnitude only. The public
                    # notional must never be used when it is an explicit cap.
                    comparable = (rate_case == "dtcc_ois" and a360 and recovery == row.recovery
                                  and not row.dtcc_notional_capped
                                  and row.dtcc_upfront_currency == "EUR"
                                  and pd.notna(row.dtcc_upfront_amount_unsigned))
                    magnitude_error = None
                    if comparable:
                        notional = float(row.dtcc_notional_reported.replace(",", ""))
                        magnitude_error = abs(net)*notional-row.dtcc_upfront_amount_unsigned
                        assert abs(magnitude_error) < 15
                    assert abs(model_bps-row.spread_bps) < 1e-8
                    if rate_case == "dtcc_ois" and a360 and recovery == row.recovery:
                        assert .4 < row.spread_bps-same_hazard_bps < 1.
                        assert fitted > base["hazard_annual"]
                    results.append({
                        "date": day.date(), "bank": row.bank_name, "upi": row.entity_id, "debt_tier": row.seniority,
                        "observed_spread_bps": row.spread_bps, "fixed_coupon_bps": row.coupon_bps,
                        "maturity": row.maturity_date, "accrual_assumption": accrual,
                        "accrual_start_assumed": schedule[0].ISO(), "step_in_assumed": step_in.ISO(),
                        "settlement_assumed": cash.ISO(), "curve_assumption": rate_case, "recovery_assumed": recovery,
                        "ece_baseline_hazard": base["hazard_annual"], "ece_baseline_pd": base["cumulative_pd"],
                        "ql_spread_at_ece_hazard_bps": same_hazard_bps, "ql_refit_spread_bps": model_bps,
                        "ql_hazard": fitted, "ql_pd": pd_ql, "ql_minus_ece_pd_bps": (pd_ql-base["cumulative_pd"])*1e4,
                        "ql_model_clean_upfront_paid_by_buyer_pct": 100*clean,
                        "ql_model_net_cash_paid_by_buyer_pct": 100*net,
                        "dtcc_notional_capped": row.dtcc_notional_capped,
                        "dtcc_unsigned_amount_eur": row.dtcc_upfront_amount_unsigned,
                        "model_absolute_cash_minus_dtcc_amount_eur": magnitude_error,
                        "status": "conditional_QuantLib_ISDA_benchmark_not_market_conversion",
                    })
    finally:
        settings.evaluationDate = prior_date
    assert len(results) == 28
    assert sum(item["model_absolute_cash_minus_dtcc_amount_eur"] is not None for item in results) == 3
    _export_audit_csv("cds_convention_comparison", results)


def test_four_ois_strips_independent_cashflows_lag_and_curve_comparison():
    from credit_engine import BASE, OIS_PILLARS, build_ois_curve, public_discount_data, transaction_credit_data

    ql = pytest.importorskip("QuantLib")
    folder = BASE / "data/market"
    raw = pd.read_csv(folder / "ois_trades.csv", dtype={"Dissemination Identifier": str})
    estr = pd.read_csv(folder / "estr.csv")
    _, original_curves = public_discount_data("dtcc_ois")
    proxies = {k: public_discount_data(k)[1] for k in ("ecb_aaa", "ecb_all")}
    quotes = transaction_credit_data()
    assert len(raw) == 187 and raw["Dissemination Identifier"].is_unique
    prior_date = ql.Settings.instance().evaluationDate
    details, summary = [], []
    try:
        for date, prints in raw.groupby("valuation_date"):
            day = pd.Timestamp(date)
            trade = _ql_date(ql, day)
            ql.Settings.instance().evaluationDate = trade
            cal = ql.TARGET()
            spot = cal.advance(trade, 2, ql.Days)
            assert prints["Effective Date"].eq(spot.ISO()).all()
            assert prints["UPI Underlier Name"].isin(["EUR-EuroSTR-OIS Compound", "EUR-EuroSTR-COMPOUND"]).all()
            assert prints["currency"].eq("EUR").all() and prints["clearing_status"].eq("intent_to_clear").all()
            for lag in (0, 1, 2):
                rebuilt = build_ois_curve(prints, estr, payment_lag_days=lag)
                curve = load_discount_curves(rebuilt, "SNAPSHOT")[day]
                assert np.all(np.diff(curve.dfs) < 0) and curve.times[-1] >= 7.
                if lag == 1:
                    assert np.allclose(curve.dfs, original_curves[day].dfs, atol=1e-12, rtol=0)
                handle = _ql_discount(ql, day, curve)
                est = ql.Estr(handle)
                errors = []
                for row in rebuilt[rebuilt["tenor"].ne("spot")].itertuples():
                    months = OIS_PILLARS[row.tenor]
                    accrual_ends = [cal.adjust(spot+ql.Period(m, ql.Months), ql.ModifiedFollowing)
                                    for m in (*range(12, months, 12), months)]
                    assert row.accrual_maturity == accrual_ends[-1].ISO()
                    coupon = row.observed_rate_percent / 100
                    swap = ql.OvernightIndexedSwap(ql.Swap.Payer, 1., ql.Schedule([spot, *accrual_ends]),
                                                   coupon, ql.Actual360(), est, 0., lag, ql.Following, cal)
                    swap.setPricingEngine(ql.DiscountingSwapEngine(handle))
                    error = (swap.fairRate()-coupon)*1e4
                    assert abs(error) < 1e-7
                    errors.append(abs(error))
                    if lag == 1:
                        matched = prints[prints["tenor"].eq(row.tenor)]
                        assert matched.fixed_rate_decimal.median() == pytest.approx(coupon)
                        details.append({"date": date, "tenor": row.tenor, "trades": len(matched),
                            "first_execution_at_utc": matched["Execution Timestamp"].min(),
                            "last_execution_at_utc": matched["Execution Timestamp"].max(),
                            "trade_rate_median_percent": row.observed_rate_percent,
                            "trade_rate_range_bps": (row.high_trade_rate_percent-row.low_trade_rate_percent)*100,
                            "max_abs_distance_trade_to_median_bps":
                                (matched.fixed_rate_decimal-coupon).abs().max()*1e4,
                            "discount_factor": row.discount_factor, "payment_date_assumed": row.pillar_date,
                            "ql_independent_residual_bps": error, "quality": "single_print" if len(matched)==1 else "median"})
                strip_quotes = quotes[quotes["valuation_date"].eq(day)]
                pd_range = {}
                if not strip_quotes.empty:
                    credit = calibrate_credit(strip_quotes, curve)
                    pd_range = {str(r.bank_name): float(r.cumulative_pd) for r in credit.repricing.itertuples()}
                zero = -np.log(curve(7.))/7
                summary.append({"date": date, "lag_bd_assumed": lag, "trades": len(prints),
                    "pillars_excluding_bridge": len(errors), "observed_tenors": "/".join(rebuilt.tenor.iloc[1:]),
                    "overnight_reference_date": rebuilt.overnight_reference_date.iloc[0],
                    "overnight_rate_percent": rebuilt.observed_rate_percent.iloc[0],
                    "qualifying_single_prints": int(rebuilt.trade_count.eq(1).sum()),
                    "independent_max_abs_residual_bps": max(errors), "df_7y": float(curve(7.)),
                    "zero_7y_percent": 100*float(zero),
                    "zero_7y_ecb_aaa_percent": -100*np.log(proxies["ecb_aaa"][day](7.))/7,
                    "zero_7y_ecb_all_percent": -100*np.log(proxies["ecb_all"][day](7.))/7,
                    "pd_at_cds_pillar_by_bank": json.dumps(pd_range, ensure_ascii=False)})
    finally:
        ql.Settings.instance().evaluationDate = prior_date
    assert len(summary) == 12
    for _, group in pd.DataFrame(summary).groupby("date"):
        assert group["zero_7y_percent"].max()-group["zero_7y_percent"].min() < .03
    strip = raw[raw.valuation_date.eq("2026-11-06")]
    invalid = pd.concat([strip, strip.tail(1)])
    with pytest.raises(ValueError, match="distinct"):
        build_ois_curve(invalid, estr)
    _export_audit_csv("ois_curve_validation", summary)
    _export_audit_csv("ois_pillar_validation", details)



def test_public_basket_tail_continuity_and_independent_monte_carlo():
    from credit_engine import discount_sensitivity, public_discount_data, transaction_credit_data

    day = pd.Timestamp("2026-05-20")
    q = transaction_credit_data()
    q = q[q["valuation_date"].eq(day)]
    curve = public_discount_data("dtcc_ois")[1][day]
    calibrated = calibrate_credit(q, curve, extrapolate=True)
    assert q.seniority.eq("subordinated").all() and len(calibrated.curves) == 3
    last = (pd.Timestamp("2029-06-20")-day).days/365
    hazards = calibrated.repricing.hazard_annual.to_numpy()
    _, rows = discount_sensitivity(q, {"ois": curve}, mode="SNAPSHOT")
    baseline = rows[rows["scenario"].eq("baseline")]
    cumulative = hazards.sum()*last
    graph = simulate_credit(calibrated.curves, [0., last, 7.], 40_000, seed=80901)
    evidence = []
    for row in baseline.itertuples():
        beyond = max(0., row.horizon_years-last)
        expected = -np.expm1(-hazards.sum()*(min(row.horizon_years, last)+beyond*row.tail_multiplier))
        assert abs(row.pd_first_independent-expected) < 1e-12
        assert row.result_kind == ("scenario" if beyond else "model_calibration")
        evidence.append({"horizon_years": row.horizon_years, "tail_multiplier": row.tail_multiplier,
                         "pd_first_independent": expected, "status": row.result_kind})
    assert -np.expm1(-cumulative) == pytest.approx(.063379, abs=5e-7)
    assert abs(np.exp(-cumulative-hazards.sum()*1.5*1e-9)-np.exp(-cumulative)) < 1e-9
    analytic = basket_survival(calibrated.curves, graph.grid[1:])
    measured = graph.basket_alive[:, 1:].mean(axis=0)
    se = np.sqrt(analytic*(1-analytic)/len(graph.basket_alive))
    assert np.all(np.abs(measured-analytic) < 4*se)
    _export_audit_csv("basket_tail_revalidation", evidence)
    audit_evidence("basket_public_mc", {"paths": len(graph.basket_alive), "seed": 80901,
        "horizons_years": graph.grid[1:].tolist(), "analytical_survival": analytic.tolist(),
        "simulated_survival": measured.tolist(), "standard_error": se.tolist()})


def test_pricing_integration_document_runs_against_external_curve(monkeypatch):
    from credit_engine import BASE

    monkeypatch.chdir(BASE)
    notice = (BASE / "INTEGRATION_PRICING.md").read_text(encoding="utf-8")
    example = notice.split("```python\n", 1)[1].split("```", 1)[0]
    variables = {}
    exec(compile(example, "INTEGRATION_PRICING.md (example)", "exec"), variables)
    result = variables["result"]
    calibration = variables["calibration"]
    assert result.factor.shape == (512, 15) and result.basket_alive.dtype == np.bool_
    assert not np.diff(result.basket_alive.astype(int), axis=1).clip(min=0).any()
    assert np.array_equal(result.first_default_times, np.min(result.default_times, axis=1))
    assert result.conditional_mean_survival.shape == (3, 15)
    factors = np.broadcast_to(calibration.discount(result.grid), result.basket_alive.shape)
    entity = result.entities[0]
    fit = calibration.repricing[calibration.repricing.entity_id.eq(entity)].iloc[0]
    joint = reprice_joint_cds(fit.tenor_years, entity, fit.recovery, result, factors,
                              reference_bps=fit.spread_bps)
    assert all(np.isfinite(list(joint.values())))
    audit_evidence("pricing_handoff_fixture", {"nodes": result.grid.tolist(), "upis": list(result.entities),
        "paths": 512, "factor_dimensions": list(result.factor.shape),
        "cascade_alive": list(variables["coupon_alive"].shape), "joint_cds_test": joint,
        "collective_status": "PENDING EXTERNAL TEAMS"})



def test_systemic_snapshot_and_independent_premium_legs():
    from credit_engine import independent_cds_repricing, systemic_snapshot

    quotes, discount, registry = systemic_snapshot()
    assert quotes.valuation_date.nunique() == quotes.seniority.nunique() == 1
    assert sorted(quotes.spread_bps) == [51.85, 54.60, 63.75]
    assert set(quotes.entity_id) == set(registry.loc[registry.decision.eq("selected"), "upi"])
    assert pd.to_datetime(registry.loc[registry.decision.eq("selected"), "known_from"]).le(
        discount.valuation_date).all()
    cal = calibrate_credit(quotes, discount)
    rows = independent_cds_repricing(cal)
    assert rows.within_tolerance.all()
    assert (rows.accrued_on_default_annuity > 0).all()
    np.testing.assert_allclose(rows.protection_leg_pv, rows.premium_leg_pv_at_input_spread, atol=2e-8)
    assert not rows.upfront_signed_verified.any()


def test_coupon_strict_default_boundary_and_semiannual_amount():
    from credit_engine import coupon_survival_masks, credit_coupon, extend_credit_curves

    curves = {"a": HazardCurve([0., 3.], [.015]), "b": HazardCurve([0., 7.], [.025])}
    with pytest.raises(ValueError, match="explicit terminal hazard"):
        credit_coupon(curves)
    annual = credit_coupon(curves, tail_multiplier=1.)
    per_period = credit_coupon(curves, tail_multiplier=1., rate_convention="per_semester")
    np.testing.assert_allclose(annual.basket.independent_survival, np.exp(-.04*COUPON_TIMES))
    np.testing.assert_allclose(annual.basket.expected_credit_only_coupon, 3.5*np.exp(-.04*COUPON_TIMES))
    np.testing.assert_allclose(per_period.basket.expected_credit_only_coupon,
                               2*annual.basket.expected_credit_only_coupon)
    assert annual.basket.extrapolated.tolist() == [False]*6 + [True]*8
    np.testing.assert_allclose(annual.banks.query("entity_id == 'a'").conditional_pd_given_alive_semester,
                               -np.expm1(-.015*.5))
    low = credit_coupon(curves, tail_multiplier=.5).basket
    high = credit_coupon(curves, tail_multiplier=1.5).basket
    assert low.first_default_pd.iloc[-1] < annual.basket.first_default_pd.iloc[-1] < high.first_default_pd.iloc[-1]
    projected = extend_credit_curves(curves, 7., tail_multiplier=1.)
    simulation = simulate_credit(projected, np.r_[0., COUPON_TIMES], 3, seed=433)
    simulation = replace(simulation, default_times=np.array([[.5, np.inf], [.51, 5.],
                                                              [np.inf, np.inf]]))
    individual, basket = coupon_survival_masks(simulation)
    assert individual.shape == (3, 2, 14) and basket.shape == (3, 14)
    assert not basket[0].any()
    assert basket[1, 0] and not basket[1, 1:].any()
    assert basket[2].all()
    conditional = credit_coupon(curves, tail_multiplier=1., scenarios=simulation).basket
    np.testing.assert_allclose(conditional.credit_condition_probability, np.r_[2/3, np.repeat(1/3, 13)])


def test_pricing_intensity_paths_match_integrated_survival():
    from credit_engine import credit_path_intensities, extend_credit_curves

    curves = extend_credit_curves({"bank": HazardCurve([0., 2., 3.], [.013, .018])}, 7.,
                                  tail_multiplier=1.5)
    grid = np.r_[0., COUPON_TIMES]
    model = fit_stochastic(curves, grid, "lognormal", FactorParams(), n_calibration=512, seed=12)
    z = scenario_normals(grid, 500, 15)
    paths = simulate_credit(curves, grid, 500, credit_innovations=z, model=model, seed=27)
    intensity, integrated = credit_path_intensities(curves, paths)
    assert intensity.shape == (500, 1, 14) and integrated.shape == (500, 1, 15)
    assert np.isfinite(intensity).all() and np.all(intensity >= 0)
    np.testing.assert_allclose(np.diff(integrated, axis=2), intensity*np.diff(grid))
    np.testing.assert_allclose(np.exp(-integrated).mean(axis=0), paths.conditional_mean_survival,
                               atol=1e-13, rtol=0)
