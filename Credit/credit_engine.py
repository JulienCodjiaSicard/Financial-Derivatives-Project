"""EUR credit engine for the ECE project. Rates are decimals, times are years."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import BinaryIO, Mapping

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike, NDArray
from scipy.optimize import brentq, least_squares

BP = 1e-4
COUPON_TIMES = np.arange(1, 15) / 2
WINDOW = ("2026-05-06", "2026-11-06")
BASE = Path(__file__).resolve().parent
FIT_TOL_BPS = 0.01
_ECB_CACHE_LOCK = Lock()
FloatArray = NDArray[np.float64]
TableInput = str | Path | BinaryIO | pd.DataFrame


def _vector(values: ArrayLike, label: str) -> FloatArray:
    vector = np.array(values, dtype=float, copy=True)
    if vector.ndim != 1 or not vector.size or not np.isfinite(vector).all():
        raise ValueError(f"{label}: expected a nonempty finite vector.")
    vector.setflags(write=False)
    return vector


def _grid(values: ArrayLike, label: str) -> FloatArray:
    grid = _vector(values, label)
    if grid.size < 2 or grid[0] != 0 or np.any(np.diff(grid) <= 0):
        raise ValueError(f"{label}: expected strictly increasing times starting at zero.")
    return grid


def _times(values: ArrayLike, horizon: float, label: str) -> FloatArray:
    t = np.asarray(values, dtype=float)
    if not np.isfinite(t).all() or np.any(t < 0) or np.any(t > horizon + 1e-12):
        raise ValueError(f"{label}: times must be within [0, {horizon:g}] years.")
    return np.minimum(t, horizon)


def _date(value: object) -> pd.Timestamp:
    day = pd.Timestamp(value)
    if pd.isna(day) or day.tzinfo is not None or day != day.normalize():
        raise ValueError("Expected a valuation date without time or timezone.")
    return day


def _recovery(value: float) -> None:
    if not 0 <= value < 1:
        raise ValueError("Recovery must be a fraction in [0, 1).")


@dataclass(frozen=True)
class DiscountCurve:
    """EUR discount factors with log-linear interpolation and no extrapolation."""

    times: ArrayLike
    dfs: ArrayLike
    valuation_date: str | pd.Timestamp
    time_basis: str = "ACT/365F"
    provider: str | None = None
    data_kind: str | None = None
    source_url: str | None = None
    available_at: pd.Timestamp | str | None = None
    curve_kind: str | None = None

    def __post_init__(self) -> None:
        times = _grid(self.times, "Discount curve")
        dfs = _vector(self.dfs, "Discount factors")
        if dfs.shape != times.shape or np.any(dfs <= 0) or dfs[0] != 1:
            raise ValueError("Discount factors must match times, be positive and start at one.")
        if self.time_basis not in {"ACT/365F", "idealized_years"}:
            raise ValueError("Time basis must be ACT/365F or idealized_years.")
        object.__setattr__(self, "times", times)
        object.__setattr__(self, "dfs", dfs)
        day = _date(self.valuation_date)
        object.__setattr__(self, "valuation_date", day)
        if self.data_kind not in {None, "observed", "synthetic"}:
            raise ValueError("Discount curve provenance must be observed or synthetic when supplied.")
        if self.available_at is not None:
            stamp = pd.to_datetime(self.available_at, utc=True)
            if pd.isna(stamp) or stamp < day.tz_localize("UTC"):
                raise ValueError("Curve availability must be on or after the valuation date.")
            object.__setattr__(self, "available_at", stamp)

    def __call__(self, t: ArrayLike) -> FloatArray:
        x = _times(t, self.times[-1], "EUR discount curve coverage")
        return np.exp(np.interp(x, self.times, np.log(self.dfs)))


@dataclass(frozen=True)
class HazardCurve:
    """Risk-neutral piecewise-constant annual intensity with optional flat tail."""

    edges: ArrayLike
    lambdas: ArrayLike
    extrapolate: bool = False
    cumulative: FloatArray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        edges = _grid(self.edges, "Hazard knots")
        intensity = _vector(self.lambdas, "Hazard rates")
        if intensity.size != edges.size - 1 or np.any(intensity < 0):
            raise ValueError("Hazard rates must be nonnegative with one per interval.")
        cumulative = _vector(np.r_[0, np.cumsum(intensity * np.diff(edges))], "Integrated hazard")
        object.__setattr__(self, "edges", edges)
        object.__setattr__(self, "lambdas", intensity)
        object.__setattr__(self, "cumulative", cumulative)

    def _bucket(self, t: ArrayLike) -> tuple[FloatArray, NDArray]:
        x = _times(t, np.inf if self.extrapolate else self.edges[-1], "Hazard curve coverage")
        return x, np.minimum(np.searchsorted(self.edges, x, side="right") - 1, self.lambdas.size - 1)

    def hazard_rate(self, t: ArrayLike) -> FloatArray:
        """Instantaneous annual intensity, right-continuous on internal knots."""
        _, bucket = self._bucket(t)
        return self.lambdas[bucket]

    def integrated_hazard(self, t: ArrayLike) -> FloatArray:
        """Cumulative hazard from zero through t."""
        x, bucket = self._bucket(t)
        return self.cumulative[bucket] + self.lambdas[bucket] * (x - self.edges[bucket])

    def survival_probability(self, t: ArrayLike) -> FloatArray:
        """Probability of remaining alive at t under the pricing measure."""
        return np.exp(-self.integrated_hazard(t))

    def default_probability(self, t: ArrayLike, start: ArrayLike = 0) -> FloatArray:
        """Conditional probability of default by t, given survival at start."""
        if np.any(np.asarray(t) < np.asarray(start)):
            raise ValueError("Default probability requires end >= start.")
        return -np.expm1(-(self.integrated_hazard(t) - self.integrated_hazard(start)))

    def default_density(self, t: ArrayLike) -> FloatArray:
        """Risk-neutral density per year, using the right-hand rate at knots."""
        return self.hazard_rate(t) * self.survival_probability(t)

    def _default_times(self, thresholds: FloatArray, horizon: float) -> FloatArray:
        index = np.minimum(np.searchsorted(self.cumulative, thresholds, side="right") - 1,
                           self.lambdas.size - 1)
        rates = self.lambdas[index]
        lag = np.divide(thresholds - self.cumulative[index], rates,
                        out=np.full_like(thresholds, np.inf), where=rates > 0)
        hit = self.edges[index] + lag
        return np.where(hit <= horizon, hit, np.inf)


def _table(data: TableInput) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame):
        return data.copy()
    name = data if isinstance(data, (str, Path)) else getattr(data, "name", "")
    suffix = Path(name).suffix.lower()
    if suffix == ".xlsx":
        return pd.read_excel(data, engine="openpyxl")
    if suffix not in {"", ".csv"}:
        raise ValueError("Expected a CSV or XLSX table.")
    return pd.read_csv(data, encoding="utf-8-sig", sep=None, engine="python")


def _require(frame: pd.DataFrame, columns: set[str]) -> None:
    missing = sorted(columns - set(frame.columns))
    if missing or frame.empty:
        raise ValueError(f"Missing columns {missing} or empty table.")


def _text(frame: pd.DataFrame, columns: tuple[str, ...]) -> None:
    for column in columns:
        values = frame[column].astype("string").str.strip()
        if values.isna().any() or values.eq("").any():
            raise ValueError(f"Missing {column}.")
        frame[column] = values


def _numeric(column: pd.Series) -> pd.Series:
    value = pd.to_numeric(column, errors="raise").astype(float)
    if np.isinf(value).any():
        raise ValueError(f"Nonfinite values in {column.name}.")
    return value


def _days(values: pd.Series) -> pd.Series:
    return pd.to_datetime([_date(value) for value in values]).to_series(index=values.index)


def _availability(frame: pd.DataFrame) -> None:
    """Normalize optional point-in-time evidence without substituting the export time."""
    if "available_at" in frame:
        raw = frame["available_at"].replace(r"^\s*$", np.nan, regex=True)
        stamps = pd.to_datetime(raw, utc=True, format="mixed")
        if (stamps.isna() & raw.notna()).any() or (
            stamps < frame["valuation_date"].dt.tz_localize("UTC")
        ).any():
            raise ValueError("available_at must be a valid publication time on or after the observation date.")
        frame["available_at"] = stamps


def load_quotes(data: TableInput, mode: str) -> pd.DataFrame:
    """Validate input history and append canonical spread/bid/ask in basis points.

    MARKET accepts already-authorized live-provider records passed as a DataFrame.
    SNAPSHOT accepts dated exports. DEMO requires synthetic provenance.
    """
    if mode not in {"MARKET", "SNAPSHOT", "DEMO"}:
        raise ValueError("Mode must be MARKET, SNAPSHOT or DEMO.")
    if mode == "MARKET" and not isinstance(data, pd.DataFrame):
        raise ValueError("MARKET requires an authorized provider DataFrame. Use SNAPSHOT for files.")
    q = _table(data).reset_index(drop=True)
    text_columns = ("entity_id", "bank_name", "unit", "quote_type", "currency", "seniority",
                    "restructuring", "provider", "data_kind", "time_basis")
    _require(q, set(text_columns) | {"valuation_date", "collected_at", "spread", "recovery"})
    _text(q, text_columns)
    q["valuation_date"] = _days(q["valuation_date"])
    _availability(q)
    q["collected_at"] = pd.to_datetime(q["collected_at"], utc=True, format="mixed")
    if q["collected_at"].isna().any() or (
        q["collected_at"].dt.tz_convert(None).dt.normalize() < q["valuation_date"]
    ).any():
        raise ValueError("Collection timestamps must be present and on or after observation dates.")
    if "available_at" in q and (q["available_at"] > q["collected_at"]).any():
        raise ValueError("A quote cannot be collected before its version is available.")
    required_kind = "synthetic" if mode == "DEMO" else "observed"
    if not q["data_kind"].eq(required_kind).all():
        raise ValueError(f"{mode} requires data_kind={required_kind}.")
    if not q["quote_type"].eq("par_spread").all():
        raise ValueError("Only par_spread is supported. Convert upfront/quoted spread with a market pricer.")
    if not q["currency"].eq("EUR").all():
        raise ValueError("Only EUR CDS quotes are compatible with the EUR discount curve.")
    if not q["time_basis"].isin({"ACT/365F", "idealized_years"}).all():
        raise ValueError("Invalid CDS time basis.")
    units = q["unit"].map({"bps": 1.0, "decimal": 10000.0, "percent": 100.0})
    if units.isna().any():
        raise ValueError("Quote units must be bps, decimal or percent.")
    q["recovery"] = _numeric(q["recovery"])
    if not q["recovery"].between(0, 1, inclusive="left").all():
        raise ValueError("Recovery must be a finite fraction in [0, 1).")
    for source in ("spread", "bid", "ask"):
        q[f"{source}_bps"] = _numeric(q[source]) * units if source in q else np.nan
    bps = q[["spread_bps", "bid_bps", "ask_bps"]]
    if q["spread_bps"].isna().any() or np.isinf(bps).any().any() or (bps < 0).any().any():
        raise ValueError("Spreads must be finite and nonnegative. Bid/ask are optional.")
    if ((q["bid_bps"] > q["spread_bps"]) | (q["ask_bps"] < q["spread_bps"])).any():
        raise ValueError("The par spread must lie within supplied bid/ask bounds.")
    tenor = _numeric(q["tenor_years"]) if "tenor_years" in q else pd.Series(np.nan, index=q.index)
    if "maturity_date" in q:
        dated = q["maturity_date"].notna()
        if not q.loc[dated, "time_basis"].eq("ACT/365F").all():
            raise ValueError("Maturity dates require ACT/365F.")
        tenor.loc[dated] = (_days(q.loc[dated, "maturity_date"])
                            - q.loc[dated, "valuation_date"]).dt.days / 365
    q["tenor_years"] = tenor
    if tenor.isna().any() or (tenor <= 0).any() or (tenor > 100).any():
        raise ValueError("Supply a maturity date or a tenor in (0, 100] years.")
    key = ["valuation_date", "entity_id", "tenor_years"]
    if q.duplicated(key).any():
        raise ValueError("Duplicate CDS maturity for the same valuation date and entity.")
    identity = ["bank_name", "seniority", "restructuring", "recovery", "time_basis"]
    if (q.groupby(["valuation_date", "entity_id"])[identity].nunique() > 1).any().any():
        raise ValueError("Mixed contract terms, time bases, recoveries or bank labels for an entity.")
    contract = ["bank_name", "seniority", "restructuring"]
    for column in ("reference_entity_id", "documentation"):
        if column in q:
            q[column] = q[column].astype("string").str.strip().replace("", pd.NA)
            contract.append(column)
    if (q.groupby("entity_id")[contract].nunique() > 1).any().any():
        raise ValueError("Reference identity, seniority and restructuring must stay fixed in history.")
    return q.sort_values(key).reset_index(drop=True)


def load_discount_curves(data: TableInput, mode: str) -> dict[pd.Timestamp, DiscountCurve]:
    """Load dated EUR discount factors using year times or ACT/365F pillar dates."""
    if mode not in {"MARKET", "SNAPSHOT", "DEMO"}:
        raise ValueError("Invalid discount curve mode.")
    df = _table(data).reset_index(drop=True)
    _require(df, {"valuation_date", "discount_factor", "currency", "time_basis", "data_kind", "provider"})
    _text(df, ("currency", "time_basis", "data_kind", "provider"))
    if not df["currency"].eq("EUR").all() or not df["data_kind"].eq(
        "synthetic" if mode == "DEMO" else "observed"
    ).all():
        raise ValueError(f"Expected EUR discount factors with provenance matching {mode}.")
    df["valuation_date"] = _days(df["valuation_date"])
    _availability(df)
    time = _numeric(df["time_years"]) if "time_years" in df else pd.Series(np.nan, index=df.index)
    if "pillar_date" in df:
        dated = df["pillar_date"].notna()
        if not df.loc[dated, "time_basis"].eq("ACT/365F").all():
            raise ValueError("Discount pillar dates require ACT/365F.")
        time.loc[dated] = (_days(df.loc[dated, "pillar_date"]) - df.loc[dated, "valuation_date"]).dt.days / 365
    df["time_years"] = time
    df["discount_factor"] = _numeric(df["discount_factor"])
    curves = {}
    for day, group in df.sort_values("time_years").groupby("valuation_date"):
        source = ["time_basis", "provider"]
        source.extend(name for name in ("source_url", "curve_kind") if name in group)
        if (group[source].nunique() > 1).any():
            raise ValueError(f"Discount curve {day.date()} must use one time basis and source.")
        t, d = group["time_years"].to_numpy(), group["discount_factor"].to_numpy()
        if t[0] > 0:
            t, d = np.r_[0, t], np.r_[1, d]
        meta = group.iloc[0]
        known_availability = "available_at" in group and group["available_at"].notna().all()
        # The curve can only be used once every pillar is available.
        available_at = group["available_at"].max() if known_availability else None
        source_url = group["source_url"].dropna() if "source_url" in group else pd.Series(dtype=str)
        curves[day] = DiscountCurve(t, d, day, meta["time_basis"], meta["provider"], meta["data_kind"],
                                    source_url.iloc[0] if not source_url.empty else None, available_at,
                                    meta["curve_kind"] if "curve_kind" in group else None)
    return curves


@dataclass(frozen=True)
class CDSLegs:
    """Protection PV and premium annuities per unit notional at valuation."""

    protection: float
    scheduled_annuity: float
    accrued_on_default_annuity: float

    @property
    def risky_annuity(self) -> float:
        return self.scheduled_annuity + self.accrued_on_default_annuity

    @property
    def fair_spread_bps(self) -> float:
        if self.risky_annuity <= 0:
            raise ValueError("The instrument has no positive risky annuity.")
        return self.protection / self.risky_annuity / BP

    def buyer_pv(self, coupon_bps: float, notional: float = 1, upfront_paid: float = 0) -> float:
        """Buyer PV in EUR. Optional time-zero upfront is a fraction of notional."""
        if not np.isfinite([coupon_bps, notional, upfront_paid]).all() or coupon_bps < 0 or notional <= 0:
            raise ValueError("Expected finite coupon/upfront and a positive notional.")
        return notional * (self.protection - coupon_bps * BP * self.risky_annuity - upfront_paid)


def _schedule(maturity: float, interval: float) -> FloatArray:
    if not np.isfinite([maturity, interval]).all() or not 0 < maturity <= 100 or interval <= 0:
        raise ValueError("Expected positive CDS maturity and premium interval.")
    if maturity / interval > 10000:
        raise ValueError("Premium schedule exceeds 10,000 periods.")
    # Regular payments from time zero plus a short final stub when needed.
    interior = np.arange(1, np.ceil(maturity / interval)) * interval
    return np.r_[0.0, interior[interior < maturity - 1e-12], maturity]


def _moments(rate: ArrayLike, dt: ArrayLike) -> tuple[FloatArray, FloatArray]:
    """Integrals of exp(-rate*u) and u*exp(-rate*u) on [0, dt]."""
    x = np.asarray(rate) * dt
    small = np.abs(x) < 1e-3
    divisor = np.where(small, 1.0, x)
    e0 = np.where(small, 1 - x / 2 + x*x / 6 - x**3 / 24, -np.expm1(-x) / divisor)
    e1 = np.where(small, .5 - x / 3 + x*x / 8 - x**3 / 30,
                  (-np.expm1(-x) - x * np.exp(-x)) / divisor**2)
    return e0 * dt, e1 * dt**2


def price_cds(maturity: float, hazard: HazardCurve, discount: DiscountCurve, recovery: float = .4,
              *, premium_interval: float = .25, accrue_on_default: bool = True) -> CDSLegs:
    """Value a spot-start par-style CDS in idealized time, with default-time settlement."""
    _recovery(recovery)
    schedule = _schedule(maturity, premium_interval)
    # Split at each discontinuity of hazard, discount forward or accrued-premium slope.
    knots = np.union1d(np.union1d(schedule, hazard.edges[hazard.edges < maturity]),
                       discount.times[discount.times < maturity])
    left, right = knots[:-1], knots[1:]
    dt = right - left
    lam = hazard.hazard_rate(left)
    dfs = discount(knots)
    forward = -np.diff(np.log(dfs)) / dt
    e0, e1 = _moments(lam + forward, dt)
    density = dfs[:-1] * hazard.survival_probability(left) * lam
    protection = (1 - recovery) * np.dot(density, e0)
    scheduled = np.dot(np.diff(schedule), discount(schedule[1:]) * hazard.survival_probability(schedule[1:]))
    offset = left - schedule[np.searchsorted(schedule, left, side="right") - 1]
    accrued = np.dot(density, offset * e0 + e1) if accrue_on_default else 0.0
    return CDSLegs(float(protection), float(scheduled), float(accrued))


def flat_hazard_benchmark(spread_bps: ArrayLike, recovery: float) -> FloatArray:
    """Approximate annual hazard s/(1-R), for scale checks."""
    _recovery(recovery)
    spread = np.asarray(spread_bps, dtype=float)
    if not np.isfinite(spread).all() or np.any(spread < 0):
        raise ValueError("The benchmark requires finite nonnegative spreads.")
    return spread * BP / (1 - recovery)


def _bootstrap(tenor: FloatArray, quotes: FloatArray, discount: DiscountCurve,
               recovery: float, extrapolate: bool) -> HazardCurve:
    calibrated = []
    for k, (maturity, spread) in enumerate(zip(tenor, quotes)):
        edges = np.r_[0, tenor[:k + 1]]

        def objective(rate: float) -> float:
            trial = HazardCurve(edges, [*calibrated, rate])
            return price_cds(maturity, trial, discount, recovery).buyer_pv(spread)

        at_zero = objective(0)
        # Treat tiny positive round-off at a zero boundary relative to the CDS scale.
        pv_tolerance = 1e-13 * spread * BP * maturity
        if at_zero > pv_tolerance:
            raise ValueError(f"{maturity:g}Y, {spread:g} bp: would require negative forward hazard.")
        if at_zero >= 0:
            calibrated.append(0.0)
            continue
        upper = .25
        while objective(upper) < 0 and upper < 128:
            upper *= 2
        if objective(upper) < 0:
            raise ValueError(f"{maturity:g}Y: cannot bracket a nonnegative calibration root.")
        calibrated.append(brentq(objective, 0, upper, xtol=1e-18, rtol=1e-13))
    return HazardCurve(np.r_[0, tenor], calibrated, extrapolate)


@dataclass(frozen=True)
class Calibration:
    """Per-entity curves and dated quote-by-quote diagnostics."""

    curves: dict[str, HazardCurve]
    repricing: pd.DataFrame
    discount: DiscountCurve
    warnings: tuple[str, ...]

    def probabilities(self, grid: ArrayLike) -> pd.DataFrame:
        """Individual probabilities on one supplied grid, including forward PDs."""
        t = _vector(grid, "Probability reporting times")
        if np.any(np.diff(t) <= 0):
            raise ValueError("Reporting times must be strictly increasing.")
        start = np.r_[0, t[:-1]]
        return pd.concat([
            pd.DataFrame({"entity_id": entity, "time_years": t, "hazard_annual": c.hazard_rate(t),
                          "integrated_hazard": c.integrated_hazard(t),
                          "survival": c.survival_probability(t), "cumulative_pd": c.default_probability(t),
                          "period_pd_given_alive": c.default_probability(t, start),
                          "default_density_annual": c.default_density(t),
                          "extrapolated": t > c.edges[-1] + 1e-12,
                          "time_status": np.select(
                              [t == 0, t > c.edges[-1] + 1e-12, np.isin(t, c.edges[1:])],
                              ["origin", "extrapolated_flat", "calibration_pillar"],
                              default="between_pillars")})
            for entity, c in self.curves.items()
        ], ignore_index=True)


def calibrate_credit(quotes: TableInput, discount: DiscountCurve, *, mode: str = "SNAPSHOT",
                     basket: list[str] | tuple[str, ...] | None = None,
                     method: str = "strict", extrapolate: bool = False) -> Calibration:
    """Fit each bank at one exact valuation date; constrained fallback is explicit."""
    if method not in {"strict", "constrained"}:
        raise ValueError("Calibration method must be strict or constrained.")
    q = load_quotes(quotes, mode)
    if discount.data_kind is not None and q["data_kind"].iloc[0] != discount.data_kind:
        raise ValueError("CDS and EUR discount factors must use matching data provenance.")
    if q["valuation_date"].nunique() != 1 or q["valuation_date"].iloc[0] != discount.valuation_date:
        raise ValueError("CDS snapshot and EUR discount curve must have identical valuation dates.")
    if not q["time_basis"].eq(discount.time_basis).all():
        raise ValueError("CDS and EUR discount curve must have the same time basis.")
    if basket is not None:
        if not basket or len(set(basket)) != len(basket) or set(basket) - set(q["entity_id"]):
            raise ValueError("Basket must contain unique entity identifiers present in the snapshot.")
        q = q[q["entity_id"].isin(basket)]
    _times(q["tenor_years"], discount.times[-1], "EUR discount curve must cover CDS maturities")
    curves, tables, warnings = {}, [], []
    for entity, group in q.groupby("entity_id", sort=False):
        tenor = group["tenor_years"].to_numpy()
        spreads = group["spread_bps"].to_numpy()
        recovery = float(group["recovery"].iloc[0])
        edges = np.r_[0, tenor]
        width = (group["ask_bps"] - group["bid_bps"]).where(lambda x: x > 0)
        usable_widths = width.dropna()
        typical_width = float(usable_widths.median()) if not usable_widths.empty else 1.0
        # Zero and missing widths do not certify liquidity. Use the bank's median,
        # with a floor so a very small width cannot dominate the least-squares fit.
        residual_scale = width.fillna(typical_width).clip(lower=max(.1, .1 * typical_width)).to_numpy()

        def reprice(curve: HazardCurve) -> FloatArray:
            return np.array([price_cds(t, curve, discount, recovery).fair_spread_bps for t in tenor])

        status = "exact"
        try:
            curve = _bootstrap(tenor, spreads, discount, recovery, extrapolate)
            model = reprice(curve)
            inaccurate = np.flatnonzero(np.abs(model - spreads) > 1e-7 + 1e-10 * spreads)
            if inaccurate.size:
                raise ValueError(f"{tenor[inaccurate[0]]:g}Y: bootstrap root failed the price residual check.")
        except ValueError as exc:
            if method == "strict":
                raise ValueError(f"{entity}: {exc}") from exc
            fit = least_squares(lambda x: (reprice(HazardCurve(edges, x)) - spreads) / residual_scale,
                                flat_hazard_benchmark(spreads, recovery), bounds=(0, np.inf))
            if not fit.success:
                raise ValueError(f"{entity}: constrained least squares failed: {fit.message}") from exc
            curve = HazardCurve(edges, fit.x, extrapolate)
            model = reprice(curve)
            weighting = "bid-ask-weighted" if not usable_widths.empty else "equal-weighted"
            warnings.append(f"{entity}: {exc} Used {weighting} nonnegative least squares; inspect residuals.")
            status = "constrained"
        table = group.copy()
        table["model_bps"] = model
        table["residual_bps"] = model - spreads
        table["fit_residual_scale_bps"] = residual_scale if status == "constrained" else np.nan
        table["residual_over_bid_ask"] = table["residual_bps"] / width
        table["within_bid_ask"] = ((model >= table["bid_bps"] - 1e-8)
                                  & (model <= table["ask_bps"] + 1e-8)).astype("boolean").where(
                                      table[["bid_bps", "ask_bps"]].notna().all(axis=1))
        table["within_0_01bp"] = np.abs(model - spreads) < FIT_TOL_BPS
        table["fit"] = status
        table["discount_provider"] = discount.provider
        table["discount_curve_kind"] = discount.curve_kind
        table["discount_data_kind"] = discount.data_kind
        table["discount_source_url"] = discount.source_url
        table["discount_available_at"] = discount.available_at
        table["hazard_annual"] = curve.lambdas
        table["flat_hazard_benchmark"] = flat_hazard_benchmark(spreads, recovery)
        table["survival"] = curve.survival_probability(tenor)
        table["cumulative_pd"] = curve.default_probability(tenor)
        table["period_pd_given_alive"] = curve.default_probability(tenor, edges[:-1])
        table["default_density_annual"] = curve.default_density(tenor)
        table["integrated_hazard"] = curve.integrated_hazard(tenor)
        curves[entity] = curve
        tables.append(table)
    if extrapolate:
        warnings.append("Flat last hazard beyond each bank's last observed tenor; tail is extrapolated.")
    return Calibration(curves, pd.concat(tables, ignore_index=True), discount, tuple(warnings))


def basket_survival(curves: Mapping[str, HazardCurve], t: ArrayLike) -> FloatArray:
    """Analytic survival of the basket to first default under independence."""
    if not curves:
        raise ValueError("The basket must contain at least one bank.")
    return np.exp(-np.sum([c.integrated_hazard(t) for c in curves.values()], axis=0))


def validate_credit(cal: Calibration) -> pd.DataFrame:
    """Check each curve's probability invariants and expose exact/approximate fits."""
    rows = []
    for bank, curve in cal.curves.items():
        t = np.unique(np.r_[np.linspace(0, curve.edges[-1], 101), curve.edges])
        s, pd_cum = curve.survival_probability(t), curve.default_probability(t)
        conditional = curve.default_probability(t[1:], t[:-1])
        rows.append({"entity_id": bank, "probability_invariants": bool(
            s[0] == 1 and np.all((0 <= s) & (s <= 1)) and np.all(np.diff(s) <= 0)
            and np.allclose(pd_cum, 1 - s) and np.all((0 <= conditional) & (conditional <= 1))
            and np.all(curve.lambdas >= 0)),
            "max_abs_residual_bps": cal.repricing.loc[
                cal.repricing["entity_id"].eq(bank), "residual_bps"].abs().max()})
    return pd.DataFrame(rows)



# Credit dynamics and the interface to the common Monte Carlo ------------------

def validate_correlation(correlation: ArrayLike) -> FloatArray:
    """Validate the Pricing group's (EUR rates, FX, credit) Gaussian correlation."""
    m = np.asarray(correlation, dtype=float)
    if (m.shape != (3, 3) or not np.isfinite(m).all()
            or not np.allclose(m, m.T, atol=1e-12, rtol=0)
            or not np.allclose(np.diag(m), 1, atol=1e-12, rtol=0)
            or np.any(np.abs(m) > 1)):
        raise ValueError("Expected a symmetric 3×3 correlation matrix with unit diagonal.")
    try:
        np.linalg.cholesky(m)
    except np.linalg.LinAlgError as exc:
        raise ValueError("Correlation must be positive definite for Cholesky.") from exc
    return m.copy()


@dataclass(frozen=True)
class FactorParams:
    """Scenario parameters. For lognormal, beta multiplies the OU factor."""

    kappa: float = .65
    sigma: float = .45
    beta: float = 1.0
    theta: float = .003
    y0: float = .002

    def validate(self, kind: str) -> None:
        if (not np.isfinite([self.kappa, self.sigma, self.beta, self.theta, self.y0]).all()
                or self.kappa <= 0 or min(self.sigma, self.beta, self.theta, self.y0) < 0):
            raise ValueError("Factor parameters must be finite with kappa > 0 and nonnegative scales.")
        if kind == "cirpp" and (self.theta == 0 or 2 * self.kappa * self.theta < self.sigma**2):
            raise ValueError("CIR++ requires theta > 0 and the Feller condition for this scheme.")


def _factor_paths(grid: FloatArray, innovations: FloatArray, kind: str, p: FactorParams) -> FloatArray:
    factors = np.empty((innovations.shape[0], grid.size))
    factors[:, 0] = 0 if kind == "lognormal" else p.y0
    for j, dt in enumerate(np.diff(grid)):
        z = innovations[:, j]
        prev = factors[:, j]
        if kind == "lognormal":
            # Exact OU marginal transition with supplied standardized innovations.
            variance = -np.expm1(-2 * p.kappa * dt) / (2 * p.kappa)
            factors[:, j + 1] = np.exp(-p.kappa * dt) * prev + p.sigma * np.sqrt(variance) * z
        elif p.sigma == 0:
            factors[:, j + 1] = p.theta + (prev - p.theta) * np.exp(-p.kappa * dt)
        else:
            # Drift-implicit Euler on sqrt(Y); rationalize the root for negative shocks.
            a = 1 + p.kappa * dt / 2
            b = np.sqrt(prev) + p.sigma * np.sqrt(dt) * z / 2
            c = (p.kappa * p.theta / 2 - p.sigma**2 / 8) * dt
            root = np.hypot(b, 2 * np.sqrt(a * c))
            positive = (np.abs(b) + root) / (2 * a)
            factors[:, j + 1] = np.where(b >= 0, positive, c / (a * positive))**2
    if not np.isfinite(factors).all():
        raise ValueError("Nonfinite factor paths. Check scales, time grid and innovations.")
    return factors


def cir_forward_hazard(t: ArrayLike, p: FactorParams) -> FloatArray:
    """Instantaneous forward hazard of the unshifted continuous CIR survival."""
    p.validate("cirpp")
    t = _times(t, np.inf, "CIR time")
    gamma = np.hypot(p.kappa, np.sqrt(2) * p.sigma)
    u = -np.expm1(-gamma * t)
    b = 2 * u / (2 * gamma + (p.kappa - gamma) * u)
    return p.kappa * p.theta * b + p.y0 * (1 - p.kappa * b - .5 * p.sigma**2 * b**2)


def _cir_positive_shift(curve: HazardCurve, horizon: float, p: FactorParams) -> None:
    _times(horizon, np.inf if curve.extrapolate else curve.edges[-1], "CIR++ target horizon")
    knots = np.r_[curve.edges[curve.edges < horizon], horizon]
    gamma = np.hypot(p.kappa, np.sqrt(2) * p.sigma)
    # f_CIR(B) is concave in increasing B(t); check its possible interior maximum.
    if p.sigma > 0 and p.y0 > 0:
        b_star = p.kappa * (p.theta - p.y0) / (p.y0 * p.sigma**2)
        if 0 < b_star < 2 / (gamma + p.kappa):
            u_star = 2 * gamma * b_star / (2 - (p.kappa - gamma) * b_star)
            if u_star < 1:
                t_star = -np.log1p(-u_star) / gamma
                knots = np.unique(np.r_[knots, t_star]) if t_star < horizon else knots
    forward = cir_forward_hazard(knots, p)
    if np.any(curve.hazard_rate(knots[:-1]) + 1e-13 < np.maximum(forward[:-1], forward[1:])):
        raise ValueError("CIR++ would require a negative continuous shift. Reduce the CIR baseline or use lognormal.")


@dataclass(frozen=True)
class StochasticCredit:
    """A fitted scenario on one grid. Base hazards/shifts have shape (banks, steps)."""

    kind: str
    params: FactorParams
    grid: FloatArray
    entities: tuple[str, ...]
    base_rates: FloatArray
    targets: FloatArray
    fit_survival: FloatArray
    fit_survival_se: FloatArray
    n_calibration: int
    calibration_seed: int | None

    def _intensity(self, factor: FloatArray, step: int, bank: int) -> FloatArray:
        level = self.base_rates[bank, step]
        if self.kind == "cirpp":
            return level + factor
        return _log_intensity_multiplier(factor, self.params.beta) * level


def _log_intensity_multiplier(factor: FloatArray, beta: float) -> FloatArray:
    with np.errstate(over="ignore"):
        multiplier = np.exp(beta * factor)
    if not np.isfinite(multiplier).all() or np.any(multiplier == 0):
        raise ValueError("Lognormal multiplier outside numerical range. Check volatility and exposure.")
    return multiplier


def fit_stochastic(curves: Mapping[str, HazardCurve], grid: ArrayLike, kind: str,
                   params: FactorParams | None = None, *, n_calibration: int = 16000,
                   seed: int = 7301, calibration_innovations: ArrayLike | None = None) -> StochasticCredit:
    """Sequential survival fitting on an antithetic sample independent of evaluation.

    Optional standard normals for the positive half have shape
    (n_calibration // 2, len(grid) - 1). Supply them only for coupled validation.
    """
    grid = _grid(grid, "Credit simulation grid")
    if kind not in {"lognormal", "cirpp"} or not curves:
        raise ValueError("Supply curves and model lognormal or cirpp.")
    p = params or (FactorParams() if kind == "lognormal" else FactorParams(sigma=.035))
    p.validate(kind)
    if not isinstance(n_calibration, (int, np.integer)) or n_calibration < 4 or n_calibration % 2:
        raise ValueError("Stochastic calibration requires an even number of paths (at least four).")
    if kind == "cirpp":
        for entity, curve in curves.items():
            try:
                _cir_positive_shift(curve, grid[-1], p)
            except ValueError as exc:
                raise ValueError(f"{entity}: {exc}") from exc
    entities = tuple(curves)
    targets = np.array([c.survival_probability(grid) for c in curves.values()])
    if np.any(targets < 1e-14):
        raise ValueError("Stochastic survival fit requires target probabilities >= 1e-14.")
    half = n_calibration // 2
    if calibration_innovations is None:
        z = np.random.default_rng(seed).standard_normal((half, grid.size - 1))
    else:
        z = np.asarray(calibration_innovations, dtype=float)
        if z.shape != (half, grid.size - 1) or not np.isfinite(z).all():
            raise ValueError("Calibration innovations must be finite with one row per antithetic pair.")
    factors = _factor_paths(grid, np.concatenate((z, -z)), kind, p)
    del z
    base = np.zeros((len(entities), grid.size - 1))
    fitted = np.ones_like(targets)
    fit_errors = np.zeros_like(targets)
    weights = np.ones((len(entities), n_calibration))
    for j, dt in enumerate(np.diff(grid)):
        factor = factors[:, j]
        if kind == "cirpp":
            cir_decay = np.exp(-dt * factor)
        else:
            exposure = dt * _log_intensity_multiplier(factor, p.beta)
        for k, entity in enumerate(entities):
            target = targets[k, j + 1]
            if kind == "cirpp":
                weights[k] *= cir_decay
                unshifted = weights[k].mean()
                if unshifted < target - 2e-13:
                    raise ValueError(f"{entity}: grid CIR++ shift negative at {grid[j+1]:g}Y. "
                                     "Lower the CIR baseline, refine the grid or increase calibration paths.")
                base[k, j] = max(0, np.log(unshifted / target) / dt)
                weights[k] *= np.exp(-base[k, j] * dt)
            elif weights[k].mean() > target + 2e-15:
                def objective(rate: float) -> float:
                    return (weights[k] * np.exp(-rate * exposure)).mean() - target

                upper = max(.05, 2 * curves[entity].default_probability(grid[j + 1], grid[j]) / dt)
                while objective(upper) > 0 and upper < 1e6:
                    upper *= 2
                if objective(upper) > 0:
                    raise ValueError(f"{entity}: cannot bracket lognormal survival fit at {grid[j+1]:g}Y.")
                base[k, j] = brentq(objective, 0, upper, xtol=1e-13)
                weights[k] *= np.exp(-base[k, j] * exposure)
            fitted[k, j + 1] = weights[k].mean()
            pairs = .5 * (weights[k, :half] + weights[k, half:])
            fit_errors[k, j + 1] = pairs.std(ddof=1) / np.sqrt(half)
    for arr in (base, targets, fitted, fit_errors):
        arr.setflags(write=False)
    seed_used = seed if calibration_innovations is None else None
    return StochasticCredit(kind, p, grid, entities, base, targets, fitted, fit_errors, n_calibration, seed_used)


@dataclass(frozen=True)
class CreditScenarios:
    """Default times in years and survival states on the common grid."""

    grid: FloatArray
    entities: tuple[str, ...]
    default_times: FloatArray             # (paths, banks), inf if alive at grid[-1]
    first_default_times: FloatArray       # (paths,)
    basket_alive: NDArray[np.bool_]       # (paths, dates)
    factor: FloatArray | None             # (paths, dates) for stochastic models
    conditional_mean_survival: FloatArray # (banks, dates), integrated over idiosyncratic draws
    conditional_survival_se: FloatArray   # (banks, dates), evaluation paths only
    min_intensity: float
    max_intensity: float
    model: StochasticCredit | None

    def bank_alive(self, entity: str) -> NDArray[np.bool_]:
        """Materialize the (paths, dates) state of one bank only when requested."""
        return self.default_times[:, self.entities.index(entity), None] > self.grid


def simulate_credit(curves: Mapping[str, HazardCurve], grid: ArrayLike, n_paths: int, *,
                    credit_innovations: ArrayLike | None = None,
                    model: StochasticCredit | None = None, seed: int = 9011) -> CreditScenarios:
    """Consume Pricing's N(0,1) credit column (paths, steps), without a second Cholesky."""
    grid = _grid(grid, "Credit simulation grid")
    if not curves or not isinstance(n_paths, (int, np.integer)) or n_paths < 2:
        raise ValueError("Supply a nonempty basket and at least two paths.")
    entities = tuple(curves)
    targets = np.array([c.survival_probability(grid) for c in curves.values()])
    if model is None and credit_innovations is not None:
        raise ValueError("Deterministic intensities do not consume credit innovations.")
    if model is not None and (model.entities != entities or not np.array_equal(model.grid, grid)
                              or not np.allclose(model.targets, targets, atol=1e-13, rtol=0)):
        raise ValueError("Stochastic fit must use the exact grid, bank order and target curves supplied.")
    thresholds = np.random.default_rng(seed).exponential(size=(n_paths, len(entities)))
    means = targets.copy()
    errors = np.zeros_like(targets)
    tau = np.full_like(thresholds, np.inf)
    factor = None
    minimum, maximum = np.inf, 0.0
    if model is None:
        for k, curve in enumerate(curves.values()):
            tau[:, k] = curve._default_times(thresholds[:, k], grid[-1])
            rates = curve.lambdas[curve.edges[:-1] < grid[-1]]
            minimum, maximum = min(minimum, rates.min()), max(maximum, rates.max())
    else:
        z = np.asarray(credit_innovations, dtype=float)
        if z.shape != (n_paths, grid.size - 1) or not np.isfinite(z).all():
            raise ValueError("Credit innovations must be finite, shape (n_paths, len(grid)-1).")
        factor = _factor_paths(grid, z, model.kind, model.params)
        for k in range(len(entities)):
            cumulative = np.zeros(n_paths)
            for j, dt in enumerate(np.diff(grid)):
                rate = model._intensity(factor[:, j], j, k)
                minimum, maximum = min(minimum, rate.min()), max(maximum, rate.max())
                next_hazard = cumulative + rate * dt
                hit = np.isinf(tau[:, k]) & (thresholds[:, k] <= next_hazard) & (rate > 0)
                tau[hit, k] = grid[j] + (thresholds[hit, k] - cumulative[hit]) / rate[hit]
                cumulative = next_hazard
                conditional = np.exp(-cumulative)
                means[k, j + 1] = conditional.mean()
                errors[k, j + 1] = conditional.std(ddof=1) / np.sqrt(n_paths)
    first = tau.min(axis=1)
    return CreditScenarios(grid, entities, tau, first, first[:, None] > grid, factor, means,
                           errors, float(minimum), float(maximum), model)


def basket_diagnostics(scenarios: CreditScenarios) -> pd.DataFrame:
    """First-default Monte Carlo probabilities, standard errors and Wilson intervals."""
    n = scenarios.default_times.shape[0]
    pd_first = 1 - scenarios.basket_alive.mean(axis=0)
    z = 1.95996398454
    denom = 1 + z**2 / n
    center = (pd_first + z*z / (2*n)) / denom
    margin = z * np.sqrt((pd_first * (1-pd_first) + z*z / (4*n)) / n) / denom
    return pd.DataFrame({"time_years": scenarios.grid, "basket_survival": 1 - pd_first,
                         "first_default_pd": pd_first, "mc_se": np.sqrt(pd_first*(1-pd_first)/n),
                         "pd_ci95_low": np.maximum(0, center - margin),
                         "pd_ci95_high": np.minimum(1, center + margin)})


def _joint_path_legs(maturity: float, entity: str, recovery: float, scenarios: CreditScenarios,
                     path_discounts: ArrayLike, interval: float,
                     accrue_on_default: bool) -> tuple[FloatArray, FloatArray]:
    _recovery(recovery)
    model, factor, grid = scenarios.model, scenarios.factor, scenarios.grid
    maturity = float(_times(maturity, grid[-1], "Joint CDS maturity"))
    if model is None or factor is None:
        raise ValueError("Joint repricing requires a fitted stochastic credit simulation.")
    dfs = np.asarray(path_discounts, dtype=float)
    if (dfs.shape != factor.shape or not np.isfinite(dfs).all()
            or np.any(dfs <= 0) or not np.allclose(dfs[:, 0], 1, rtol=0, atol=1e-12)):
        raise ValueError("Require positive EUR discount paths matching scenarios, starting at one.")
    schedule = _schedule(maturity, interval)
    knots = np.union1d(grid[grid < maturity], schedule)
    bank = scenarios.entities.index(entity)
    n_paths = dfs.shape[0]
    survival, protection, annuity = np.ones(n_paths), np.zeros(n_paths), np.zeros(n_paths)
    for left, right in zip(knots[:-1], knots[1:]):
        step = np.searchsorted(grid, left, side="right") - 1
        forward = (np.log(dfs[:, step]) - np.log(dfs[:, step + 1])) / (grid[step + 1] - grid[step])
        discounted = dfs[:, step] * np.exp(-forward * (left - grid[step]))
        rate = model._intensity(factor[:, step], step, bank)
        dt = right - left
        e0, e1 = _moments(rate + forward, dt)
        density = discounted * survival * rate
        protection += (1 - recovery) * density * e0
        coupon = np.searchsorted(schedule, left, side="right") - 1
        if accrue_on_default:
            annuity += density * ((left - schedule[coupon]) * e0 + e1)
        survival *= np.exp(-rate * dt)
        if right == schedule[coupon + 1]:
            annuity += (right - schedule[coupon]) * discounted * np.exp(-forward * dt) * survival
    return protection, annuity


def _spread_influence(legs: tuple[FloatArray, FloatArray]) -> tuple[float, FloatArray]:
    protection, annuity = legs
    average = annuity.mean()
    if average <= 0 or not np.isfinite(average) or not np.isfinite(protection).all():
        raise ValueError("Joint CDS legs must be finite with a positive average risky annuity.")
    fair = protection.mean() / average
    return float(fair / BP), (protection - fair * annuity) / (average * BP)


def _paired_spreads(legs: tuple[FloatArray, FloatArray],
                    comparison: tuple[FloatArray, FloatArray]) -> tuple[float, float]:
    spread, influence = _spread_influence(legs)
    other, other_influence = _spread_influence(comparison)
    return spread - other, float((influence - other_influence).std(ddof=1) / np.sqrt(influence.size))


def _joint_summary(legs: tuple[FloatArray, FloatArray], reference_bps: float) -> dict[str, float]:
    protection, annuity = legs
    spread, influence = _spread_influence(legs)
    return {"joint_fair_bps": spread, "difference_bps": spread - reference_bps,
            "spread_mc_se_bps": float(influence.std(ddof=1) / np.sqrt(influence.size)),
            "buyer_pv_per_unit_at_reference": float((protection - reference_bps * BP * annuity).mean()),
            "protection_pv": float(protection.mean()), "risky_annuity": float(annuity.mean())}


def reprice_joint_cds(maturity: float, entity: str, recovery: float, scenarios: CreditScenarios,
                      path_discounts: ArrayLike, *, reference_bps: float,
                      premium_interval: float = .25, accrue_on_default: bool = True,
                      comparison_discounts: ArrayLike | None = None) -> dict[str, float]:
    """Reprice using matched pathwise EUR DFs and conditional Cox survival.

    Optional comparison DFs must align row by row to the same i.i.d. paths.
    Standard errors use the ratio-of-means delta method, conditional on the fitted model.
    """
    if not np.isfinite(reference_bps) or reference_bps < 0:
        raise ValueError("Reference spread must be finite and nonnegative.")
    legs = _joint_path_legs(maturity, entity, recovery, scenarios, path_discounts,
                            premium_interval, accrue_on_default)
    summary = _joint_summary(legs, reference_bps)
    if comparison_discounts is not None:
        other = _joint_path_legs(maturity, entity, recovery, scenarios, comparison_discounts,
                                 premium_interval, accrue_on_default)
        delta, se = _paired_spreads(legs, other)
        summary.update(paired_spread_difference_bps=delta, paired_spread_mc_se_bps=se)
    return summary



# Recalibration risk, historical replay and reproducible demonstration ----------

def credit_stress(quotes: TableInput, discount: DiscountCurve, *, mode: str = "SNAPSHOT",
                  spread_bump_bps: float = 0, recovery: float | None = None,
                  entity: str | None = None, tenor_years: float | None = None,
                  basket: list[str] | None = None, method: str = "strict",
                  extrapolate: bool = False,
                  spread_overrides_bps: Mapping[tuple[str, float], float] | None = None) -> Calibration:
    """Recalibrate after an additive spread shock targeted to all or selected quotes.

    Scenario rows drop the original bid/ask for bumped instruments. An unchanged
    spread and a changed recovery preserve the original quote and bid/ask.
    Absolute overrides are keyed by (entity_id, tenor_years). Results are flagged as
    scenarios and preserve the source spread, source recovery and provenance.
    """
    if not np.isfinite(spread_bump_bps):
        raise ValueError("Spread bump must be finite in basis points.")
    if spread_overrides_bps is not None and (spread_bump_bps or entity is not None or tenor_years is not None):
        raise ValueError("Use either absolute spread overrides or a targeted additive bump.")
    if recovery is not None:
        _recovery(recovery)
    if entity is not None and basket is not None and entity not in basket:
        raise ValueError("The shocked entity must be in the selected basket.")
    q = load_quotes(quotes, mode)
    if basket is not None:
        q = q[q["entity_id"].isin(basket)].copy()
    hit = np.ones(len(q), dtype=bool)
    if entity is not None:
        hit &= q["entity_id"].eq(entity).to_numpy()
    if tenor_years is not None:
        hit &= np.isclose(q["tenor_years"], tenor_years, rtol=0, atol=1e-10)
    if not hit.any():
        raise ValueError("The shock matches no quote in the selected basket.")
    q["unit"] = "bps"
    q["spread"], q["bid"], q["ask"] = q["spread_bps"], q["bid_bps"], q["ask_bps"]
    q["source_spread_bps"], q["source_recovery"] = q["spread_bps"], q["recovery"]
    if spread_bump_bps:
        q.loc[hit, "spread"] += spread_bump_bps
    if spread_overrides_bps is not None:
        for (bank, tenor), override in spread_overrides_bps.items():
            matched = q["entity_id"].eq(bank) & np.isclose(q["tenor_years"], tenor, rtol=0, atol=1e-10)
            if matched.sum() != 1:
                raise ValueError(f"Absolute override must identify one quote: {bank}, {tenor:g}Y.")
            q.loc[matched, "spread"] = override
    changed = q["spread"].ne(q["source_spread_bps"])
    q.loc[changed, ["bid", "ask"]] = np.nan
    if recovery is not None:
        # A recovery assumption is entity-level and applies to all its instruments.
        affected = q["entity_id"].isin(q.loc[hit, "entity_id"])
        q.loc[affected, "recovery"] = recovery
    calibration = calibrate_credit(q, discount, mode=mode, method=method, basket=basket, extrapolate=extrapolate)
    diagnostics = calibration.repricing
    diagnostics["source_data_kind"] = diagnostics["data_kind"]
    diagnostics["data_kind"] = "scenario"
    diagnostics["spread_is_stressed"] = diagnostics["spread_bps"].ne(diagnostics["source_spread_bps"])
    return calibration


@dataclass(frozen=True)
class Backtest:
    """Per-date status, snapshots, and genuinely out-of-calibration tenor predictions."""

    status: pd.DataFrame
    daily: pd.DataFrame
    bank: pd.DataFrame
    repricing: pd.DataFrame
    holdout: pd.DataFrame


def backtest_credit(quotes: TableInput, discounts: Mapping[pd.Timestamp, DiscountCurve], *,
                    mode: str = "SNAPSHOT", basket: list[str] | None = None,
                    start: str = WINDOW[0], end: str = WINDOW[1], horizon: float = 7,
                    holdout_tenor: float = 5, method: str = "strict",
                    extrapolate: bool = False, holdout_extra_tenors: tuple[float, ...] = (),
                    require_point_in_time: bool = False) -> Backtest:
    """Replay each as-of snapshot with a dated discount curve and a fixed basket.

    Holdout predicts a removed interior tenor from the same day's other quotes.
    It is cross-sectional validation, never a time-series forecast.
    Known late versions are skipped. Strict mode also requires release timestamps
    for all selected quotes and discount pillars before end of the valuation day (UTC).
    """
    first, last = _date(start), _date(end)
    holdout_targets = (holdout_tenor, *holdout_extra_tenors)
    if (first > last or not np.isfinite([horizon, *holdout_targets]).all()
            or min(horizon, *holdout_targets) <= 0 or len(set(holdout_targets)) != len(holdout_targets)):
        raise ValueError("Invalid backtest window or horizons.")
    q = load_quotes(quotes, mode)
    q = q[q["valuation_date"].between(first, last)]
    dates = sorted(q["valuation_date"].unique())
    if not dates:
        raise ValueError("No CDS observations inside the requested historical window.")
    if basket is None:
        basket = sorted(q.loc[q["valuation_date"].eq(dates[0]), "entity_id"].unique())
    elif not basket or len(set(basket)) != len(basket):
        raise ValueError("Backtest basket must contain unique entity identifiers.")
    days, summaries, banks, reprices, holdouts = [], [], [], [], []
    for day, group in q.groupby("valuation_date"):
        df = discounts.get(day)
        if df is None:
            days.append({"valuation_date": day, "status": "skipped", "availability_evidence": "missing",
                         "detail": "Missing same-day EUR curve"})
            continue
        selected = group[group["entity_id"].isin(basket)]
        timestamps = selected["available_at"] if "available_at" in selected else None
        cutoff = day.tz_localize("UTC") + pd.Timedelta(days=1)
        verified = timestamps is not None and timestamps.notna().all() and df.available_at is not None
        late = ((df.available_at is not None and df.available_at >= cutoff)
                or (timestamps is not None and (timestamps >= cutoff).any()))
        if late or (require_point_in_time and not verified):
            days.append({"valuation_date": day, "status": "skipped",
                         "availability_evidence": "late" if late else "missing",
                         "detail": "Version available after UTC valuation day" if late else
                         "Missing point-in-time timestamps for CDS or EUR curve"})
            continue
        evidence = "asof_timestamp" if verified else "valuation_date_only"
        try:
            fit = calibrate_credit(group, df, mode=mode, basket=basket, method=method,
                                   extrapolate=extrapolate)
        except ValueError as exc:
            days.append({"valuation_date": day, "status": "skipped", "availability_evidence": evidence,
                         "detail": str(exc)})
            continue
        days.append({"valuation_date": day, "status": "calibrated", "availability_evidence": evidence,
                     "detail": " | ".join(fit.warnings)})
        reprices.append(fit.repricing)
        covered = horizon <= df.times[-1] + 1e-12 and all(
            c.extrapolate or horizon <= c.edges[-1] + 1e-12 for c in fit.curves.values())
        survival = float(basket_survival(fit.curves, horizon)) if covered else np.nan
        summaries.append({"valuation_date": day, "horizon_years": horizon,
                          "basket_survival": survival, "first_default_pd": 1 - survival,
                          "max_abs_residual_bps": fit.repricing["residual_bps"].abs().max(),
                          "horizon_covered": covered,
                          "horizon_extrapolated": covered and any(
                              horizon > c.edges[-1] + 1e-12 for c in fit.curves.values())})
        for entity, curve in fit.curves.items():
            entity_covered = horizon <= df.times[-1] + 1e-12 and (
                curve.extrapolate or horizon <= curve.edges[-1] + 1e-12)
            banks.append({"valuation_date": day, "entity_id": entity, "horizon_years": horizon,
                          "survival": float(curve.survival_probability(horizon)) if entity_covered else np.nan,
                          "hazard_annual": float(curve.hazard_rate(horizon)) if entity_covered else np.nan})
            rows = group[group["entity_id"].eq(entity)]
            tenor = rows["tenor_years"].to_numpy()
            for target in holdout_targets:
                index = np.argmin(np.abs(tenor - target))
                record = {"valuation_date": day, "entity_id": entity, "target_tenor_years": target,
                          "tenor_years": tenor[index]}
                if index in {0, len(rows) - 1} or abs(tenor[index] - target) > .5:
                    holdouts.append({**record, "status": "no nearby interior tenor"})
                    continue
                held = rows.iloc[index]
                try:
                    trimmed = calibrate_credit(rows.drop(held.name), df, mode=mode, method=method)
                    estimate = price_cds(tenor[index], trimmed.curves[entity], df, held["recovery"]).fair_spread_bps
                except ValueError as exc:
                    holdouts.append({**record, "status": str(exc)})
                    continue
                holdouts.append({**record, "status": "predicted", "quote_bps": held["spread_bps"],
                                 "prediction_bps": estimate, "error_bps": estimate - held["spread_bps"]})
    return Backtest(pd.DataFrame(days), pd.DataFrame(summaries), pd.DataFrame(banks),
                    pd.concat(reprices, ignore_index=True) if reprices else pd.DataFrame(),
                    pd.DataFrame(holdouts))


def scenario_normals(grid: ArrayLike, n_paths: int, seed: int) -> FloatArray:
    """Independent standard normals for standalone demonstrations, not Pricing."""
    # Path-major draws make larger samples extend the smaller samples at a fixed grid.
    return np.random.default_rng(seed).standard_normal((n_paths, len(grid) - 1))


def demo_data() -> tuple[pd.DataFrame, dict[pd.Timestamp, DiscountCurve]]:
    """Load the packaged, explicitly synthetic six-month history."""
    return (load_quotes(BASE / "data/demo/demo_cds_history.csv", "DEMO"),
            load_discount_curves(BASE / "data/demo/demo_eur_history.csv", "DEMO"))


def _demo_dynamics(cal: Calibration, grid: FloatArray, folder: Path) -> dict[str, object]:
    rows, basket_rows, marginal_rows, scenario_rows = [], [], [], []
    t = np.asarray(grid)
    reference = basket_survival(cal.curves, t)
    for n in (10000, 50000, 100000):
        independent_pd = None
        for kind in ("deterministic", "lognormal", "cirpp"):
            start = perf_counter()
            model = None if kind == "deterministic" else fit_stochastic(cal.curves, t, kind)
            fit_seconds = perf_counter() - start
            z = None if model is None else scenario_normals(t, n, 7911)
            start = perf_counter()
            result = simulate_credit(cal.curves, t, n, model=model, credit_innovations=z)
            elapsed = perf_counter() - start
            report = basket_diagnostics(result)
            empirical = report["basket_survival"].to_numpy()
            target = np.array([c.survival_probability(t) for c in cal.curves.values()])
            marginal = np.abs(result.conditional_mean_survival - target)
            errors = result.conditional_survival_se
            combined = np.hypot(errors, 0 if model is None else model.fit_survival_se)
            z_errors = np.divide(marginal, combined, out=np.zeros_like(marginal), where=combined > 1e-12)
            simulated_pd = ~result.basket_alive[:, -1]
            if independent_pd is None:
                independent_pd = simulated_pd
            paired = simulated_pd.astype(float) - independent_pd
            rows.append({"model": kind, "paths": n, "fit_seconds": fit_seconds,
                         "simulation_seconds": elapsed, "first_default_pd_7y": report["first_default_pd"].iloc[-1],
                         "first_default_mc_se_7y": report["mc_se"].iloc[-1],
                         "max_marginal_error_probability": marginal.max(),
                         "max_marginal_error_in_combined_se": z_errors.max(),
                         "max_in_sample_error_probability": 0 if model is None else
                         np.max(np.abs(model.fit_survival - model.targets)),
                         "min_hazard_annual": result.min_intensity, "max_hazard_annual": result.max_intensity,
                         "paired_first_default_pd_delta_vs_independence": paired.mean(),
                         "paired_first_default_pd_delta_se": paired.std(ddof=1) / np.sqrt(n)})
            if n == 50000:
                report["model"] = kind
                report["independent_analytic_survival"] = reference
                basket_rows.append(report)
                for k, entity in enumerate(result.entities):
                    marginal_rows.append(pd.DataFrame({"model": kind, "entity_id": entity, "time_years": t,
                        "target_survival": target[k], "mc_conditional_survival": result.conditional_mean_survival[k],
                        "mc_conditional_se": errors[k], "calibration_survival_se": 0 if model is None
                        else model.fit_survival_se[k], "mc_binary_survival": result.bank_alive(entity).mean(axis=0)}))
            del result
    pd.DataFrame(rows).to_csv(folder / "performance_and_models.csv", index=False)
    pd.concat(basket_rows).to_csv(folder / "basket.csv", index=False)
    pd.concat(marginal_rows).to_csv(folder / "stochastic_marginals.csv", index=False)

    # Change the dependence/volatility with common pricing and idiosyncratic shocks.
    n_stress = 50000
    independent_pd = ~simulate_credit(cal.curves, t, n_stress).basket_alive[:, -1]
    z_stress = scenario_normals(t, n_stress, 7911)
    for kind, p, label in (
        ("lognormal", FactorParams(beta=0), "zero_loading"),
        ("lognormal", FactorParams(sigma=.25), "lower_ou_vol"),
        ("lognormal", FactorParams(sigma=.85), "higher_ou_vol"),
        ("lognormal", FactorParams(kappa=.20), "slower_mean_reversion"),
        ("cirpp", FactorParams(sigma=.02), "lower_cir_vol"),
        ("cirpp", FactorParams(sigma=.05), "higher_cir_vol"),
        ("cirpp", FactorParams(sigma=.035, theta=.02, y0=.02), "infeasible_shift"),
    ):
        try:
            fitted = fit_stochastic(cal.curves, t, kind, p)
            result = simulate_credit(cal.curves, t, n_stress, model=fitted, credit_innovations=z_stress)
        except ValueError as exc:
            scenario_rows.append({"model": kind, "scenario": label, "status": str(exc)})
            continue
        risk = basket_diagnostics(result).iloc[-1]
        paired = (~result.basket_alive[:, -1]).astype(float) - independent_pd
        scenario_rows.append({"model": kind, "scenario": label, "status": "simulated",
                              "kappa": p.kappa, "sigma": p.sigma, "beta": p.beta,
                              "theta": p.theta, "y0": p.y0, "first_default_pd_7y": risk["first_default_pd"],
                              "mc_se": risk["mc_se"],
                              "paired_pd_delta_vs_independence": paired.mean(),
                              "paired_pd_delta_se": paired.std(ddof=1) / np.sqrt(n_stress),
                              "max_marginal_error_probability": np.max(
                                  np.abs(result.conditional_mean_survival - fitted.targets))})
    pd.DataFrame(scenario_rows).to_csv(folder / "dependence_stress.csv", index=False)
    convergence = []
    fine_normals = scenario_normals(np.linspace(0, 7, 169), 20000, 7911)
    for steps_per_year in (4, 12, 24):
        finer = np.linspace(0, 7, 7 * steps_per_year + 1)
        for kind in ("lognormal", "cirpp"):
            fit = fit_stochastic(cal.curves, finer, kind)
            stride = 24 // steps_per_year
            blocks = fine_normals.reshape(20000, -1, stride)
            # Couple the grids through the same innovations. OU transitions use
            # exact aggregation weights for the same endpoints on nested grids.
            weights = np.ones(stride) if kind == "cirpp" else np.exp(
                -fit.params.kappa * np.arange(stride - 1, -1, -1) / 24)
            z = (blocks @ weights) / np.linalg.norm(weights)
            result = simulate_credit(cal.curves, finer, 20000, model=fit, credit_innovations=z)
            row = basket_diagnostics(result).iloc[-1]
            convergence.append({"steps_per_year": steps_per_year, "paths": 20000, "model": kind,
                                "first_default_pd_7y": row["first_default_pd"], "mc_se": row["mc_se"],
                                "max_marginal_error_probability": np.max(
                                    np.abs(result.conditional_mean_survival - fit.targets))})
        del result
    pd.DataFrame(convergence).to_csv(folder / "grid_convergence.csv", index=False)
    return {"independent_analytic_first_default_pd_7y": float(1-reference[-1]), "performance": rows}


def _demo_joint(cal: Calibration, t: FloatArray, folder: Path) -> None:
    """Paired covariance audit on an ECE CDS with synthetic discount paths."""
    bank = next(iter(cal.curves))
    quote = cal.repricing.loc[cal.repricing["entity_id"].eq(bank)].iloc[-1]
    samples = (10000, 40000)
    n = samples[-1]
    fine = np.linspace(0, t[-1], 2 * (t.size - 1) + 1)
    innovations = np.random.default_rng(8213).standard_normal((2, n, len(fine) - 1))
    n_fit = 24000
    calibration_fine = np.random.default_rng(7301).standard_normal((n_fit // 2, len(fine) - 1))
    reference_bps = float(quote["model_bps"])
    monthly = {}
    rows = []
    for grid, step_count in ((t, 12), (fine, 24)):
        dt = np.diff(grid)
        initial_df = cal.discount(grid)
        base_rate = (innovations[0].reshape(n, -1, 2).sum(axis=2) / np.sqrt(2)
                     if step_count == 12 else innovations[0])
        for kind, params in (("lognormal", FactorParams(sigma=.85)), ("cirpp", FactorParams(sigma=.035))):
            if step_count == 12:
                weights = np.array([np.exp(-params.kappa / 24), 1]) if kind == "lognormal" else np.ones(2)
                credit_z = (innovations[1].reshape(n, -1, 2) @ weights) / np.linalg.norm(weights)
                calibration_z = (calibration_fine.reshape(n_fit // 2, -1, 2) @ weights) / np.linalg.norm(weights)
            else:
                credit_z = innovations[1]
                calibration_z = calibration_fine
            curves = {bank: cal.curves[bank]}
            model = fit_stochastic(curves, grid, kind, params, n_calibration=n_fit,
                                   calibration_innovations=calibration_z)
            result = simulate_credit(curves, grid, n, model=model, credit_innovations=credit_z)
            comparisons = {}
            for rho in (None, 0., -.55, .55):
                if rho is None:
                    dfs = np.broadcast_to(initial_df, (n, grid.size))
                else:
                    # Common credit paths at every rho. The proxy has E[D] equal to the
                    # initial discount curve at grid nodes, before sampling error.
                    z_rate = rho * credit_z + np.sqrt(1 - rho*rho) * base_rate
                    shock = np.cumsum(.035 * z_rate * np.sqrt(dt), axis=1)
                    dfs = initial_df * np.exp(np.c_[np.zeros(n), -shock] - .5 * .035**2 * grid)
                    del shock
                legs = _joint_path_legs(quote["tenor_years"], bank, quote["recovery"], result, dfs, .25, True)
                comparisons[rho] = legs
                for size in samples:
                    leg_sample = tuple(leg[:size] for leg in legs)
                    other_rho = 0. if rho not in {0., None} else None
                    comparable = tuple(leg[:size] for leg in comparisons[other_rho])
                    delta, se = _paired_spreads(leg_sample, comparable)
                    key = kind, rho, size
                    if step_count == 12:
                        monthly[key] = leg_sample
                    grid_delta, grid_se = _paired_spreads(leg_sample, monthly[key])
                    row = _joint_summary(leg_sample, reference_bps)
                    rows.append({**row, "data_kind": "synthetic", "model": kind, "entity_id": bank,
                                 "tenor_years": quote["tenor_years"],
                                 "reference_bps": reference_bps, "paths": size, "steps_per_year": step_count,
                                 "calibration_paths": n_fit,
                                 "discount": "deterministic" if rho is None else "synthetic_rate_proxy",
                                 "rho_rates_credit": rho,
                                 "empirical_rho_rates_credit": np.nan if rho is None else
                                 np.corrcoef(z_rate[:size].ravel(), credit_z[:size].ravel())[0, 1],
                                 "mean_df_error_max": np.max(np.abs(dfs[:size].mean(axis=0) - initial_df)),
                                 "comparison": "independent_rates" if other_rho == 0. else "deterministic_rates",
                                 "paired_delta_bps": delta, "paired_se_bps": se,
                                 "paired_ci95_low_bps": delta - 1.96 * se,
                                 "paired_ci95_high_bps": delta + 1.96 * se,
                                 "paired_grid_delta_vs_monthly_bps": grid_delta,
                                 "paired_grid_se_bps": grid_se})
                del dfs
            del result, model
    pd.DataFrame(rows).to_csv(folder / "joint_cds.csv", index=False)


def run_demo(folder: Path, *, full: bool = True) -> None:
    """Export a seeded demonstration and validation tables, using the packaged history."""
    import json
    from importlib.metadata import version

    folder.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    q, discounts = demo_data()
    import_seconds = perf_counter() - started
    day = q["valuation_date"].max()
    snapshot = q[q["valuation_date"].eq(day)]
    calibration_start = perf_counter()
    cal = calibrate_credit(snapshot, discounts[day], mode="DEMO")
    calibration_seconds = perf_counter() - calibration_start
    cal.repricing.to_csv(folder / "repricing.csv", index=False)
    cal.probabilities(np.r_[0, COUPON_TIMES]).to_csv(folder / "probabilities_semiannual.csv", index=False)
    validate_credit(cal).to_csv(folder / "invariants.csv", index=False)
    stress_start = perf_counter()
    grids = np.linspace(0, 7, 85)
    stress_rows, stress_banks = [], []
    focus = next(iter(cal.curves))
    scenarios = [("+1 bp", {"spread_bump_bps": 1}), ("+10 bp", {"spread_bump_bps": 10}),
                 ("+50 bp", {"spread_bump_bps": 50}), ("-10 bp", {"spread_bump_bps": -10}),
                 ("one bank +10 bp", {"spread_bump_bps": 10, "entity": focus}),
                 ("one tenor +10 bp", {"spread_bump_bps": 10, "entity": focus, "tenor_years": 5})]
    scenarios += [(f"recovery {r:.0%}", {"recovery": r}) for r in (.2, .3, .4, .5)]
    base_pd = 1 - basket_survival(cal.curves, 7)
    for label, shock in scenarios:
        source = {"scenario": label, "data_kind": "scenario", "source_data_kind": "synthetic"}
        try:
            stressed = credit_stress(snapshot, discounts[day], mode="DEMO", **shock)
        except ValueError as exc:
            stress_rows.append({**source, **shock, "status": str(exc)})
            continue
        pd7 = 1 - basket_survival(stressed.curves, 7)
        stress_rows.append({**source, **shock, "status": "recalibrated",
                            "first_default_pd_7y": pd7, "delta_pd_percentage_points": 100 * (pd7-base_pd)})
        for name, c in stressed.curves.items():
            base = cal.curves[name]
            for end in c.edges[1:]:
                idx = np.searchsorted(c.edges, end) - 1
                stress_banks.append({**source, "entity_id": name, "tenor_years": end,
                                     "hazard_annual": c.lambdas[idx],
                                     "delta_hazard_bps": (c.lambdas[idx] - base.lambdas[idx]) / BP,
                                     "survival": c.survival_probability(end), "cumulative_pd": c.default_probability(end),
                                     "delta_pd_percentage_points": 100 * (
                                         c.default_probability(end) - base.default_probability(end))})
    pd.DataFrame(stress_rows).to_csv(folder / "stress_basket.csv", index=False)
    pd.DataFrame(stress_banks).to_csv(folder / "stress_banks.csv", index=False)
    stress_seconds = perf_counter() - stress_start
    history_start = perf_counter()
    history = backtest_credit(q, discounts, mode="DEMO", holdout_extra_tenors=(3,))
    _export_backtest(history, folder)
    backtest_seconds = perf_counter() - history_start
    extra = {}
    if full:
        dynamics_start = perf_counter()
        extra = _demo_dynamics(cal, grids, folder)
        extra["stochastic_validation_seconds"] = perf_counter() - dynamics_start
        joint_start = perf_counter()
        _demo_joint(cal, grids, folder)
        extra["joint_validation_seconds"] = perf_counter() - joint_start
    diagnostics = {"mode": "DEMO", "provenance": "synthetic", "valuation_date": day.date().isoformat(),
                   "companies": list(cal.curves), "quote_count": len(cal.repricing),
                   "historical_dates_calibrated": int(history.status["status"].eq("calibrated").sum()),
                   "max_abs_repricing_error_bps": float(cal.repricing["residual_bps"].abs().max()),
                   "import_seconds": import_seconds, "calibration_seconds": calibration_seconds,
                   "stress_seconds": stress_seconds, "backtest_seconds": backtest_seconds,
                   "total_seconds": perf_counter() - started,
                   "versions": {name: version(name) for name in ("numpy", "pandas", "scipy")},
                   "seeds": {"calibration": 7301, "pricing": 7911, "thresholds": 9011, "joint": 8213}, **extra}
    (folder / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
    print(f"DEMO {day.date()} | {len(cal.repricing)} quotes | "
          f"max residual {diagnostics['max_abs_repricing_error_bps']:.3g} bp | {folder}")


def _export_backtest(history: Backtest, folder: Path) -> None:
    for key in ("status", "daily", "bank", "repricing", "holdout"):
        getattr(history, key).to_csv(folder / f"backtest_{key}.csv", index=False)


# Authorized public feeds. ECB zero coupons are sovereign proxies.
PUBLIC_SOURCES = {
    "ecb_aaa": ("YC", "G_N_A", "BCE · Souverains AAA", "ecb_sovereign_aaa"),
    "ecb_all": ("YC", "G_N_C", "BCE · Tous souverains", "ecb_sovereign_all"),
    "estr": ("EST", "B.EU000A2X2A25.WT", "BCE · €STR overnight", "overnight_reference"),
}
ECB_API = "https://data-api.ecb.europa.eu/service/data"
ECB_CURVE_INFO = "https://data.ecb.europa.eu/methodology/yield-curves"
ECB_ESTR_INFO = "https://data.ecb.europa.eu/data/datasets/EST/EST.B.EU000A2X2A25.WT"
ECB_RIGHTS = "https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html"
ECB_TENORS = {**{f"SR_{n}M": n / 12 for n in (3, 6, 9)},
              **{f"SR_{n}Y": float(n) for n in range(1, 11)}}


def _public_get(url: str, *, max_bytes: int = 8_000_000) -> bytes:
    """Read public HTTPS with a bounded retry; never retry access denials."""
    from time import sleep
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    for attempt in range(2):
        retry_delay = 1
        try:
            request = Request(url, headers={"User-Agent": "ECE-Credit-Public-Data/1.0"})
            with urlopen(request, timeout=8) as response:
                payload = response.read(max_bytes + 1)
            if not payload or len(payload) > max_bytes:
                raise ValueError("Public response is empty or exceeds the permitted size.")
            return payload
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt:
                raise OSError(f"HTTP {exc.code} · {url}") from exc
            after = exc.headers.get("Retry-After", "")
            if exc.code == 429 and (not after.isdecimal() or int(after) > 5):
                raise OSError(f"HTTP 429 · source asked to defer refresh · {url}") from exc
            if after.isdecimal():
                retry_delay = min(5, int(after))
        except (URLError, TimeoutError) as exc:
            if attempt:
                raise OSError(f"Public source unavailable · {url} · {exc}") from exc
        sleep(retry_delay)
    raise RuntimeError("Unreachable retry state.")


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    """Publish a complete cache file at once, including when readers are active."""
    from tempfile import NamedTemporaryFile

    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent,
                            delete=False) as handle:
        temp = Path(handle.name)
        try:
            frame.to_csv(handle, index=False)
        except (OSError, ValueError, TypeError):
            handle.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def ecb_request_url(source: str, start: object, end: object) -> str:
    """Construct the SDMX CSV URL from official series metadata."""
    from urllib.parse import urlencode
    if source not in PUBLIC_SOURCES:
        raise ValueError(f"Unknown ECB source: {source}")
    start_day, end_day = _date(start), _date(end)
    if not 0 <= (end_day - start_day).days <= 370:
        raise ValueError("Choose an ordered ECB request window of at most 371 days.")
    if end_day.date() > pd.Timestamp.now(tz="UTC").date():
        raise ValueError("The ECB request end date is in the future.")
    dataset, series, _, _ = PUBLIC_SOURCES[source]
    if dataset == "YC":
        series = f"B.U2.EUR.4F.{series}.SV_C_YM." + "+".join(ECB_TENORS)
    params = urlencode({"startPeriod": str(start_day.date()), "endPeriod": str(end_day.date()),
                        "format": "csvdata"})
    return f"{ECB_API}/{dataset}/{series}?{params}"


def parse_ecb_csv(payload: bytes, source: str, start: object, end: object, *,
                  collected_at: object) -> pd.DataFrame:
    """Validate ECB keys, dates and units before turning continuous zeros into DFs."""
    from io import BytesIO

    url = ecb_request_url(source, start, end)
    dataset, series, provider, kind = PUBLIC_SOURCES[source]
    raw = pd.read_csv(BytesIO(payload), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    _require(raw, {"KEY", "FREQ", "TIME_PERIOD", "OBS_VALUE", "UNIT_MULT", "OBS_STATUS"})
    if raw.empty or not raw["FREQ"].eq("B").all() or not raw["UNIT_MULT"].eq("0").all():
        raise ValueError("Unexpected frequency, multiplier, or empty ECB response.")
    date_text = raw["TIME_PERIOD"]
    if not date_text.str.fullmatch(r"\d{4}-\d{2}-\d{2}").all():
        raise ValueError("Non-daily observation date in daily ECB response.")
    day = pd.to_datetime(date_text, format="%Y-%m-%d")
    stamp = pd.to_datetime(collected_at, utc=True)
    if pd.isna(stamp) or not day.between(_date(start), _date(end)).all() or day.max().date() > stamp.date():
        raise ValueError("ECB observation dates are outside the requested or available window.")
    if raw.duplicated(["KEY", "TIME_PERIOD"]).any():
        raise ValueError("ECB response contains duplicate date/series keys.")
    values = pd.to_numeric(raw["OBS_VALUE"], errors="coerce")
    if not np.isfinite(values).all() or not raw["OBS_STATUS"].eq("A").all():
        raise ValueError("ECB response contains missing, non-finite or non-normal observations.")
    out = pd.DataFrame({"valuation_date": date_text, "series_key": raw["KEY"],
                        "observed_rate_percent": values, "observation_status": raw["OBS_STATUS"],
                        "currency": "EUR", "data_kind": "observed", "provider": provider,
                        "curve_kind": kind, "source_url": url, "collected_at": stamp.isoformat()})
    if dataset == "EST":
        _require(raw, {"UNIT_MEASURE", "BENCHMARK_ITEM", "DATA_TYPE_EST"})
        if not (raw["KEY"].eq(f"EST.{series}") & raw["UNIT_MEASURE"].eq("PC")
                & raw["BENCHMARK_ITEM"].eq("EU000A2X2A25") & raw["DATA_TYPE_EST"].eq("WT")).all():
            raise ValueError("Unexpected €STR series or percent unit.")
        # Valuation date in this table is the overnight transaction reference date.
        return out.assign(reference_type="overnight_transaction_date",
                          publication_rule="following_TARGET_business_day", unit="percent")
    _require(raw, {"UNIT", "CURRENCY", "INSTRUMENT_FM", "PROVIDER_FM_ID", "DATA_TYPE_FM"})
    keys = "YC.B.U2.EUR.4F." + series + ".SV_C_YM." + raw["DATA_TYPE_FM"]
    if not (raw["UNIT"].eq("PCPA") & raw["CURRENCY"].eq("EUR") & raw["INSTRUMENT_FM"].eq(series)
            & raw["PROVIDER_FM_ID"].eq("SV_C_YM") & raw["KEY"].eq(keys)
            & raw["DATA_TYPE_FM"].isin(ECB_TENORS)).all():
        raise ValueError("Unexpected ECB zero-coupon series, instruments or percent-per-annum unit.")
    if not out.groupby("valuation_date")["series_key"].size().eq(len(ECB_TENORS)).all():
        raise ValueError("An ECB curve has missing published pillars.")
    out["time_years"] = raw["DATA_TYPE_FM"].map(ECB_TENORS)
    out["discount_factor"] = np.exp(-values / 100 * out["time_years"])
    out = out.assign(time_basis="ACT/365F", compounding="continuous", unit="percent_per_annum")
    load_discount_curves(out, "SNAPSHOT")
    return out.sort_values(["valuation_date", "time_years"]).reset_index(drop=True)


def _public_file(source: str, root: Path) -> Path:
    folder = "market" if source in {"estr", "dtcc", "ois_trades", "dtcc_ois", "mmsr", "tradeweb"} else "proxies"
    return root / folder / f"{source}.csv"


def refresh_ecb(source: str, start: object, end: object, *,
                root: Path = BASE / "data") -> pd.DataFrame:
    """Refresh one authorized cache. A failed response leaves previous data intact."""
    from hashlib import sha256

    url = ecb_request_url(source, start, end)
    payload = _public_get(url)
    data = parse_ecb_csv(payload, source, start, end, collected_at=pd.Timestamp.now(tz="UTC"))
    data["response_sha256"] = sha256(payload).hexdigest()
    path = _public_file(source, root)
    with _ECB_CACHE_LOCK:
        old = pd.read_csv(path) if path.is_file() else data.iloc[:0].copy()
        # Preserve the first version in the reproducible offline cache. This is not
        # a vintage guarantee for dates downloaded after the actual observation.
        merged = pd.concat([old, data], ignore_index=True).drop_duplicates(
            ["valuation_date", "series_key"], keep="first")
        _atomic_csv(merged.sort_values(["valuation_date", "series_key"]), path)
    return merged


def public_discount_data(source: str, *, root: Path = BASE / "data"
                         ) -> tuple[pd.DataFrame, dict[pd.Timestamp, DiscountCurve]]:
    if source not in {"ecb_aaa", "ecb_all", "eiopa", "dtcc_ois"}:
        raise ValueError(f"Unknown public discount source: {source}")
    frame = pd.read_csv(_public_file(source, root))
    discounts = load_discount_curves(frame, "SNAPSHOT")
    if not discounts:
        raise ValueError("Public discount cache contains no curve.")
    return frame, discounts


def discount_sensitivity(snapshot: pd.DataFrame, discounts: Mapping[str, DiscountCurve], *,
                         mode: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Same-date discount/recovery scenarios on observed or explicitly synthetic credit."""
    from dataclasses import replace

    if not discounts:
        raise ValueError("Supply at least one discount curve.")
    if snapshot.empty:
        raise ValueError("Supply a nonempty credit snapshot.")
    if "market_par_verified" in snapshot and snapshot["seniority"].nunique() != 1:
        raise ValueError("Group public observations by debt tier before computing basket risk.")
    fits, baskets = [], []
    quoted_time_basis = str(snapshot["time_basis"].iloc[0])
    for label, curve in discounts.items():
        fit_curve = curve
        if mode == "DEMO" and curve.data_kind == "observed":
            # Only this analysis bridges the continuous mathematical time axis to
            # the demo grid. The mode and exported source provenance stay explicit.
            fit_curve = replace(curve, data_kind=None, time_basis=quoted_time_basis)
        base_recovery = float(snapshot["recovery"].iloc[0])
        scenarios = (("baseline", 0, None), ("spread_+25bp", 25, None),
                     ("recovery_minus_15pp", 0, max(0., base_recovery-.15)),
                     ("recovery_plus_15pp", 0, min(.99, base_recovery+.15)))
        for scenario, bump, recovery in scenarios:
            cal = (calibrate_credit(snapshot, fit_curve, mode=mode) if scenario == "baseline"
                   else credit_stress(snapshot, fit_curve, mode=mode,
                                      spread_bump_bps=bump, recovery=recovery))
            table = cal.repricing.copy()
            table["scenario"] = scenario
            table["result_kind"] = "model_calibration" if scenario == "baseline" else "scenario"
            table["discount_case"] = label
            table["discount_data_kind"] = curve.data_kind
            table["credit_input_kind"] = snapshot["data_kind"].iloc[0]
            fit_tenors = {key: float(value.edges[-1]) for key, value in cal.curves.items()}
            common = min(fit_tenors.values())
            check = validate_credit(cal)
            table["all_invariants_pass"] = bool(check["probability_invariants"].all())
            legs = [price_cds(row.tenor_years, cal.curves[row.entity_id], fit_curve, row.recovery)
                    for row in table.itertuples()]
            table["risky_annuity"] = [leg.risky_annuity for leg in legs]
            table["protection_leg"] = [leg.protection for leg in legs]
            fits.append(table)
            for horizon in sorted({min(7., common), 5., 7.}):
                factors = (1.,) if horizon <= common else (.5, 1., 1.5)
                for factor in factors:
                    surv = {entity: float(c.survival_probability(min(horizon, fit_tenors[entity])))
                            * np.exp(-factor*c.lambdas[-1]*max(0., horizon-fit_tenors[entity]))
                            for entity, c in cal.curves.items()}
                    baskets.append({"valuation_date": curve.valuation_date, "scenario": scenario,
                                    "discount_case": label, "discount_data_kind": curve.data_kind,
                                    "credit_input_kind": snapshot["data_kind"].iloc[0],
                                    "bank_count": len(surv), "horizon_years": horizon,
                                    "last_common_pillar_years": common, "tail_multiplier": factor,
                                    "time_status": "tail_scenario" if horizon > common else "inside_calibration_domain",
                                    "result_kind": ("scenario" if scenario != "baseline" or horizon > common
                                                    else "model_calibration"),
                                    "pd_first_independent": 1 - np.prod(list(surv.values())) if len(surv) > 1 else np.nan,
                                    "survival_single": next(iter(surv.values())) if len(surv) == 1 else np.nan})
    return pd.concat(fits, ignore_index=True), pd.DataFrame(baskets)




DTCC_REPORT = "https://pddata.dtcc.com/ppd/api/report/cumulative"
OIS_PROVIDER = "DTCC/CFTC · transactions €STR OIS"
OIS_KIND = "eur_estr_ois_transaction_derived"
OIS_NAMES = {"EUR-EuroSTR-OIS Compound", "EUR-EuroSTR-COMPOUND"}
OIS_PILLARS = {f"{months//12}Y" if months % 12 == 0 else f"{months}M": months
               for months in (3, 6, 12, 24, 36, 48, 60, 72, 84, 96, 108, 120)}


def dtcc_report_url(day: object, jurisdiction: str, asset: str) -> str:
    day = _date(day)
    if (jurisdiction, asset) not in {("cftc", "RATES"), ("sec", "CREDITS")}:
        raise ValueError("Unsupported DTCC public tape.")
    return f"{DTCC_REPORT}/{jurisdiction}/{jurisdiction.upper()}_CUMULATIVE_{asset}_{day:%Y_%m_%d}.zip"


def _dtcc_csv(payload: bytes) -> pd.DataFrame:
    from io import BytesIO
    from zipfile import BadZipFile, ZipFile

    try:
        with ZipFile(BytesIO(payload)) as archive:
            entries = archive.infolist()
            if len(entries) != 1 or not entries[0].filename.endswith(".csv") or entries[0].file_size > 80_000_000:
                raise ValueError("Unexpected DTCC archive structure or size.")
            with archive.open(entries[0]) as handle:
                return pd.read_csv(handle, dtype=str, keep_default_na=False, low_memory=False)
    except BadZipFile as exc:
        raise ValueError("Invalid public DTCC ZIP file.") from exc


def _ois_calendar_dates(valuation: pd.Timestamp, months: int, *, payment_lag_days: int = 1
                        ) -> tuple[list[pd.Timestamp], list[pd.Timestamp]]:
    """TARGET Modified Following with a common assumed payment lag."""
    import QuantLib as ql

    cal = ql.TARGET()

    def convert(x: ql.Date) -> pd.Timestamp:
        return pd.Timestamp(x.ISO())

    def qdate(x: pd.Timestamp) -> ql.Date:
        return ql.Date(x.day, x.month, x.year)

    spot = convert(cal.advance(qdate(valuation), 2, ql.Days))
    ends = [spot + pd.DateOffset(months=m) for m in [*range(12, months, 12), months]]
    accrual = [spot] + [convert(cal.adjust(qdate(x), ql.ModifiedFollowing)) for x in ends]
    pay = [convert(cal.advance(qdate(x), payment_lag_days, ql.Days)) for x in accrual[1:]]
    return accrual, pay


def parse_dtcc_ois(payload: bytes, day: object, *, collected_at: object) -> pd.DataFrame:
    """Filter delivered outright spot-start €STR OIS observations, keeping trade IDs."""
    from hashlib import sha256

    day = _date(day)
    raw = _dtcc_csv(payload)
    equal = {"Action type": "NEWT", "Event type": "TRAD", "Asset Class": "IR",
             "Notional currency-Leg 1": "EUR", "Notional currency-Leg 2": "EUR",
             "UPI FISN": "NA/Swap OIS EUR", "Cleared": "I", "Package indicator": "FALSE",
             "Other payment type": "", "Other payment amount": "", "Fixed rate-Leg 2": "",
             "Spread-Leg 1": "", "Spread-Leg 2": "", "Price": "",
             "Fixed rate day count convention-leg 1": "A004",
             "Floating rate day count convention-leg 2": "A004"}
    required = {"Execution Timestamp", "Event timestamp", "Effective Date", "Expiration Date",
                "UPI Underlier Name", "Unique Product Identifier", "Dissemination Identifier",
                "Original Dissemination Identifier", "Non-standardized term indicator",
                "Fixed rate-Leg 1", "Platform identifier"}
    for leg in ("Fixed rate", "Floating rate"):
        number = 1 if leg == "Fixed rate" else 2
        required.update(f"{leg} payment frequency period{x}-Leg {number}" for x in ("", " multiplier"))
    _require(raw, required | set(equal))
    if raw["Dissemination Identifier"].eq("").any() or not raw["Dissemination Identifier"].is_unique:
        raise ValueError("DTCC file contains missing or duplicate dissemination identifiers.")
    candidates = raw.copy()
    for column, value in equal.items():
        candidates = candidates[candidates[column].eq(value)]
    linked = raw.loc[raw["Action type"].isin(["EROR", "CORR", "MODI", "TERM"]),
                     "Original Dissemination Identifier"]
    spot = _ois_calendar_dates(day, 3)[0][0]
    x = candidates.loc[candidates["UPI Underlier Name"].isin(OIS_NAMES)
                       & candidates["Execution Timestamp"].str[:10].eq(str(day.date()))
                       & candidates["Effective Date"].eq(str(spot.date()))
                       & candidates["Non-standardized term indicator"].isin(["", "FALSE"])
                       & ~candidates["Dissemination Identifier"].isin(linked)].copy()
    x["fixed_rate_decimal"] = pd.to_numeric(x["Fixed rate-Leg 1"], errors="coerce")
    x = x[np.isfinite(x["fixed_rate_decimal"]) & x["fixed_rate_decimal"].ne(0)
          & x["fixed_rate_decimal"].abs().lt(.2)]
    frames = []
    for label, months in OIS_PILLARS.items():
        unadjusted = spot + pd.DateOffset(months=months)
        adjusted = _ois_calendar_dates(day, months)[0][-1]
        valid_frequency = pd.Series(True, index=x.index)
        for leg, n in (("Fixed rate", 1), ("Floating rate", 2)):
            period = x[f"{leg} payment frequency period-Leg {n}"]
            multiple = x[f"{leg} payment frequency period multiplier-Leg {n}"]
            annual = (period.eq("YEAR") & multiple.eq("1")) | (period.eq("MNTH") & multiple.eq("12"))
            valid_frequency &= annual | (period.eq("EXPI") & multiple.eq("1") & (months <= 12))
        hit = x[x["Expiration Date"].isin([str(unadjusted.date()), str(adjusted.date())]) & valid_frequency]
        if not hit.empty:
            frames.append(hit.assign(tenor=label, adjusted_maturity=str(adjusted.date())))
    if not frames:
        raise ValueError("No qualifying €STR OIS transactions in DTCC file.")
    stamp = pd.to_datetime(collected_at, utc=True)
    if pd.isna(stamp) or day.date() > stamp.date():
        raise ValueError("DTCC T+0 collection cannot precede execution date.")
    columns = sorted(required - {"Original Dissemination Identifier"})
    normalized = pd.concat(frames, ignore_index=True)
    if not normalized["Dissemination Identifier"].is_unique:
        raise ValueError("A DTCC trade matches more than one OIS maturity.")
    return normalized[columns + ["fixed_rate_decimal", "tenor", "adjusted_maturity"]].assign(
        valuation_date=str(day.date()), data_kind="observed", provider=OIS_PROVIDER, currency="EUR",
        clearing_status="intent_to_clear",
        source_url=dtcc_report_url(day, "cftc", "RATES"), collected_at=stamp.isoformat(),
        response_sha256=sha256(payload).hexdigest())


def build_ois_curve(trades: pd.DataFrame, estr: pd.DataFrame, *,
                    payment_lag_days: int = 1) -> pd.DataFrame:
    """Reconstruct €STR OIS from medians, with an explicit common lag assumption."""
    if type(payment_lag_days) is not int or not 0 <= payment_lag_days <= 5:
        raise ValueError("OIS payment lag must be an integer from 0 through 5 TARGET business days.")
    trade_required = {"valuation_date", "tenor", "Effective Date", "adjusted_maturity", "fixed_rate_decimal",
                      "source_url", "response_sha256", "collected_at", "Dissemination Identifier"}
    _require(trades, trade_required)
    _require(estr, {"valuation_date", "observed_rate_percent", "source_url"})
    if trades["Dissemination Identifier"].isna().any() or not trades["Dissemination Identifier"].is_unique:
        raise ValueError("OIS input requires distinct dissemination identifiers.")
    if not trades["tenor"].isin(OIS_PILLARS).all() or not np.isfinite(trades["fixed_rate_decimal"]).all():
        raise ValueError("OIS trades must have known maturities and finite rates.")
    day_text = trades["valuation_date"].unique()
    if len(day_text) != 1:
        raise ValueError("OIS bootstrap requires one exact execution date.")
    day = _date(day_text[0])
    group = {label: values for label, values in trades.groupby("tenor")}
    mandatory = {"3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y"}
    if not mandatory.issubset(group):
        raise ValueError("OIS strip lacks required pillars: " + ", ".join(sorted(mandatory - group.keys())))
    short_past = estr[pd.to_datetime(estr["valuation_date"]).lt(day)].sort_values("valuation_date")
    if short_past.empty or (day - _date(short_past.iloc[-1]["valuation_date"])).days > 5:
        raise ValueError("A recent and already published €STR fixing is required for the spot bridge.")
    fixing = short_past.iloc[-1]
    overnight = float(fixing["observed_rate_percent"]) / 100
    spot = _date(trades["Effective Date"].iloc[0])
    bridge = 1 + overnight * (spot - day).days / 360
    if bridge <= 0 or not np.isfinite(bridge) or not trades["Effective Date"].eq(str(spot.date())).all():
        raise ValueError("Invalid €STR spot bridge.")
    times = [0., (spot - day).days / 365]
    log_dfs = [0., -np.log(bridge)]
    rows = []
    for label, months in OIS_PILLARS.items():
        if label not in group:
            continue
        prints = group[label]
        rate = float(prints["fixed_rate_decimal"].median())
        maturity = _date(prints["adjusted_maturity"].iloc[0])
        accrual, pay = _ois_calendar_dates(day, months, payment_lag_days=payment_lag_days)
        if (accrual[0] != spot or accrual[-1] != maturity
                or not prints["adjusted_maturity"].eq(str(maturity.date())).all()):
            raise ValueError("Unexpected spot or maturity date in OIS observations.")
        t = np.array([(x - day).days / 365 for x in accrual])
        p = np.array([(x - day).days / 365 for x in pay])
        fractions = np.array([(b - a).days / 360 for a, b in zip(accrual, accrual[1:])])
        knots = [*times, p[-1]]

        def legs(log_last: float) -> tuple[float, float]:
            dfs = [*log_dfs, log_last]
            at_accrual = np.interp(t, knots, dfs)
            df_pay = np.exp(np.interp(p, knots, dfs))
            return float(df_pay @ np.expm1(at_accrual[:-1] - at_accrual[1:])), float(df_pay @ fractions)

        def objective(log_last: float) -> float:
            floating, annuity = legs(log_last)
            return floating - rate * annuity

        root = brentq(objective, -5., 2., xtol=1e-13)
        floating, annuity = legs(root)
        rows.append({"series_key": f"OIS_{label}", "tenor": label, "pillar_date": str(pay[-1].date()),
                     "accrual_maturity": str(maturity.date()), "time_years": p[-1],
                     "discount_factor": np.exp(root), "observed_rate_percent": 100 * rate,
                     "zero_rate_percent": -100 * root / p[-1], "repricing_error_bps": (floating / annuity - rate) / BP,
                     "trade_count": len(prints), "low_trade_rate_percent": 100*prints["fixed_rate_decimal"].min(),
                     "high_trade_rate_percent": 100*prints["fixed_rate_decimal"].max(),
                     "observation_status": "median_of_executed_rates"})
        times.append(p[-1])
        log_dfs.append(root)
    reference = {"series_key": "ECB_ON_BRIDGE", "tenor": "spot", "pillar_date": str(spot.date()),
                 "time_years": times[1], "discount_factor": np.exp(log_dfs[1]),
                 "observed_rate_percent": 100*overnight, "zero_rate_percent": -100*log_dfs[1]/times[1],
                 "observation_status": "flat_bridge_assumption_from_prior_ON"}
    stamp = trades.iloc[0]
    curve = pd.DataFrame([reference, *rows]).assign(valuation_date=str(day.date()), currency="EUR",
        data_kind="observed", provider=OIS_PROVIDER, curve_kind=OIS_KIND, time_basis="ACT/365F",
        unit="percent_per_annum", compounding="continuous_zero_equivalent",
        source_url=stamp["source_url"], response_sha256=stamp["response_sha256"],
        collected_at=stamp["collected_at"], overnight_reference_date=fixing["valuation_date"],
        overnight_source_url=fixing["source_url"], ois_convention=f"TARGET_MF_ACT360_assumed_equal_lag_{payment_lag_days}BD")
    load_discount_curves(curve, "SNAPSHOT")
    return curve


def refresh_dtcc_ois(day: object, *, root: Path = BASE / "data") -> pd.DataFrame:
    day = _date(day)
    if day.date() >= pd.Timestamp.now(tz="UTC").date():
        raise ValueError("Use a completed business day for the cumulative DTCC archive.")
    payload = _public_get(dtcc_report_url(day, "cftc", "RATES"), max_bytes=16_000_000)
    trades = parse_dtcc_ois(payload, day, collected_at=pd.Timestamp.now(tz="UTC"))
    estr = pd.read_csv(_public_file("estr", root))
    curve = build_ois_curve(trades, estr)
    with _ECB_CACHE_LOCK:
        for label, data, key in (("ois_trades", trades, "Dissemination Identifier"),
                                 ("dtcc_ois", curve, "series_key")):
            path = _public_file(label, root)
            if path.is_file():
                data = pd.concat([pd.read_csv(path, dtype={key: str}), data], ignore_index=True)
            _atomic_csv(data.drop_duplicates(["valuation_date", key], keep="first"), path)
    return curve




DTCC_BANK_NAMES = {
    "BANCO SANTANDER, S.A.": "Banco Santander, S.A.", "BANCO SANTANDER SA": "Banco Santander, S.A.",
    "BNP PARIBAS": "BNP Paribas SA", "COMMERZBANK AKTIENGESELLSCHAFT": "Commerzbank AG",
    "CREDIT AGRICOLE SA": "Crédit Agricole SA",
    "DEUTSCHE BANK AKTIENGESELLSCHAFT": "Deutsche Bank AG",
    "INTESA SANPAOLO S.P.A.": "Intesa Sanpaolo S.p.A.",
    "INTESA SANPAOLO SPA": "Intesa Sanpaolo S.p.A.",
    "SOCIETE GENERALE": "Société Générale SA",
    "UNICREDIT, SOCIETA' PER AZIONI": "UniCredit S.p.A.", "UNICREDIT SPA": "UniCredit S.p.A.",
}
DTCC_CDS_FIELDS = (
    "Dissemination Identifier", "Original Dissemination Identifier", "Action type", "Event type",
    "Event timestamp", "Execution Timestamp", "Effective Date", "Expiration Date",
    "Notional currency-Leg 1", "Notional amount-Leg 1", "Fixed rate-Leg 1", "Fixed rate-Leg 2",
    "Price", "Price notation", "Spread-Leg 1", "Spread notation-Leg 1", "Spread-Leg 2",
    "Other payment amount", "Other payment type", "Other payment currency",
    "Package indicator", "Non-standardized term indicator", "Fixed rate day count convention-leg 1",
    "Fixed rate payment frequency period-Leg 1", "Fixed rate payment frequency period multiplier-Leg 1",
    "UPI Underlier Name", "Underlying Asset Name", "Underlier ID-Leg 1", "Underlier ID source-Leg 1",
    "UPI FISN", "Unique Product Identifier",
)


def assess_dtcc_cds(archive: pd.DataFrame, day: object, *, collected_at: object,
                    response_sha256: str, later_messages: pd.DataFrame | None = None) -> pd.DataFrame:
    """Preserve regulatory bank tickets and reject ambiguous normalization to par."""
    day = _date(day)
    stamp = pd.to_datetime(collected_at, utc=True)
    if pd.isna(stamp) or day.date() > stamp.date():
        raise ValueError("DTCC review cannot precede the archive date.")
    _require(archive, set(DTCC_CDS_FIELDS))
    if archive["Dissemination Identifier"].eq("").any() or not archive["Dissemination Identifier"].is_unique:
        raise ValueError("SEC archive contains missing or duplicate dissemination identifiers.")
    name = archive["Underlying Asset Name"].str.strip().str.upper()
    name = name.where(name.ne(""), archive["UPI Underlier Name"].str.strip().str.upper())
    primary = (archive["Notional currency-Leg 1"].eq("EUR") & archive["UPI FISN"].eq("NA/CDS Corp SN Sr")
               & archive["Action type"].eq("NEWT") & archive["Event type"].eq("TRAD")
               & archive["Execution Timestamp"].str[:10].eq(str(day.date())) & name.isin(DTCC_BANK_NAMES))
    out = archive.loc[primary, list(DTCC_CDS_FIELDS)].copy()
    lifecycle = archive if later_messages is None else pd.concat([archive, later_messages])
    changes = lifecycle[lifecycle["Original Dissemination Identifier"].isin(out["Dissemination Identifier"])
                        & lifecycle["Action type"].isin(["CORR", "MODI", "EROR", "TERM"])]
    related = changes.groupby("Original Dissemination Identifier")["Action type"].agg(
        lambda actions: ";".join(sorted(set(actions))))
    out["lifecycle_actions_seen"] = out["Dissemination Identifier"].map(related).astype("string").fillna("")
    out["bank_name"] = name[primary].map(DTCC_BANK_NAMES)
    out["tenor_years"] = (pd.to_datetime(out["Expiration Date"], errors="coerce") - day).dt.days / 365
    out["notional_capped"] = out["Notional amount-Leg 1"].str.strip().str.endswith("+")
    out["coupon_bps"] = pd.to_numeric(out["Fixed rate-Leg 1"], errors="coerce") / BP
    numeric = pd.to_numeric(out["Spread-Leg 1"], errors="coerce")
    notation = out["Spread notation-Leg 1"]
    out["reported_spread_field_bps"] = numeric * notation.map({"3": 10_000., "4": 1.})
    upfront = out["Other payment type"].str.contains("UFRO", regex=False)
    out["exclusion_reason"] = np.select(
        [out["lifecycle_actions_seen"].str.contains("EROR|CORR", regex=True),
         out["Package indicator"].ne("FALSE"), upfront & out["notional_capped"], upfront,
         numeric.notna() & out["coupon_bps"].notna(), out["coupon_bps"].notna()],
        ["linked_correction_or_error", "package_or_unconfirmed_outright", "unsigned_upfront_and_capped_notional",
         "unsigned_upfront_and_unknown_clean_convention", "spread_coexists_with_fixed_coupon",
         "coupon_only_missing_price_and_upfront"],
        default="insufficient_public_contract_or_par_evidence")
    return out.assign(valuation_date=str(day.date()), data_kind="observed", quote_type="regulatory_transaction",
                      eligible_for_calibration=False, restructuring="undisclosed", recovery="unreported",
                      provider="DTCC/SEC · Regulation SBSR", source_url=dtcc_report_url(day, "sec", "CREDITS"),
                      collected_at=stamp.isoformat(), response_sha256=response_sha256)


def refresh_dtcc_cds(day: object, *, root: Path = BASE / "data") -> pd.DataFrame:
    from hashlib import sha256

    day = _date(day)
    if day.date() >= pd.Timestamp.now(tz="UTC").date():
        raise ValueError("Use a completed business day for the cumulative DTCC archive.")
    url = dtcc_report_url(day, "sec", "CREDITS")
    payload = _public_get(url)
    trades = assess_dtcc_cds(_dtcc_csv(payload), day, collected_at=pd.Timestamp.now(tz="UTC"),
                             response_sha256=sha256(payload).hexdigest())
    path = _public_file("dtcc", root)
    if not trades.empty:
        with _ECB_CACHE_LOCK:
            if path.is_file():
                previous = pd.read_csv(path, dtype=str, keep_default_na=False)
                merged = pd.concat([previous, trades], ignore_index=True).drop_duplicates(
                    ["Dissemination Identifier"], keep="first")
            else:
                merged = trades
            _atomic_csv(merged, path)
    return trades


PUBLIC_STUDY_DATE = "2026-11-06"
PUBLIC_CURVE_LABELS = {"dtcc_ois": "€STR OIS · DTCC/CFTC", "ecb_aaa": "BCE · Souverains AAA",
                       "ecb_all": "BCE · Tous souverains", "eiopa": "EIOPA · RFR de base sans VA"}


def public_curves_on(day: object, *, root: Path = BASE / "data") -> dict[str, DiscountCurve]:
    day = _date(day)
    curves = {}
    for key in PUBLIC_CURVE_LABELS:
        if _public_file(key, root).is_file():
            _, dated = public_discount_data(key, root=root)
            if day in dated:
                curves[key] = dated[day]
    return curves


def run_public_study(folder: Path, day: object = PUBLIC_STUDY_DATE) -> None:
    """Run same-date rate comparisons with the packaged synthetic credit inputs."""
    day = _date(day)
    q, demos = demo_data()
    snapshot = q[q["valuation_date"].eq(day)]
    public = public_curves_on(day)
    if snapshot.empty or not public:
        raise ValueError("The rate sensitivity requires the same date in public and synthetic data.")
    fits, baskets = discount_sensitivity(snapshot, {**public, "demo": demos[day]}, mode="DEMO")
    folder.mkdir(parents=True, exist_ok=True)
    fits.to_csv(folder / "credit_sensitivity.csv", index=False)
    baskets.to_csv(folder / "first_default_sensitivity.csv", index=False)
    t = np.array([1., 2., 3., 5., 7.])
    quality = []
    for label, c in public.items():
        for year, value in zip(t, c(t)):
            quality.append({"valuation_date": day, "discount_case": label, "curve_kind": c.curve_kind,
                            "time_years": year, "discount_factor": value,
                            "continuous_zero_percent": -100*np.log(value)/year, "source_url": c.source_url,
                            "max_covered_years": c.times[-1], "pillar_count": len(c.times)-1,
                            "pillar_dfs_positive": bool(np.all(c.dfs > 0)),
                            "pillar_dfs_nonincreasing": bool(np.all(np.diff(c.dfs) <= 1e-12))})
    pd.DataFrame(quality).to_csv(folder / "discount_quality.csv", index=False)
    path = _public_file("dtcc", BASE / "data")
    if path.is_file():
        trades = pd.read_csv(path, dtype={"Dissemination Identifier": str})
        coverage = trades.groupby(["valuation_date", "bank_name", "exclusion_reason"]).size()
        coverage.rename("observations").reset_index().to_csv(folder / "cds_coverage.csv", index=False)
        near5 = trades[trades["tenor_years"].between(4.5, 5.5)]
        columns = ["bank_name", "valuation_date", "provider", "tenor_years", "Expiration Date",
                   "Dissemination Identifier", "coupon_bps", "reported_spread_field_bps", "exclusion_reason",
                   "source_url"]
        near5[columns].assign(observed_par_bps=np.nan, model_par_bps=np.nan, hazard_annual=np.nan,
                              survival=np.nan).to_csv(folder / "cds_near_5y_audit.csv", index=False)




TW_SOURCES = {
    "QZ1ZPP7V49J6": ("BNP", "BNP Paribas SA", "SEN", "Sr", .40),
    "QZ5PHBF50P4P": ("BNP", "BNP Paribas SA", "SUB", "Sub", .20),
    "QZ33BCC269KW": ("DB", "Deutsche Bank AG", "SUB", "Sub", .20),
    "QZC5PR8X53F8": ("CMZB", "Commerzbank AG", "SUB", "Sub", .20),
    "QZBWPV8THS72": ("SOCGEN", "Société Générale SA", "SUB", "Sub", .20),
    "QZHNZQVFGPMW": ("SOCGEN", "Société Générale SA", "SNRLAC", "Mz", .40),
    "QZJMLVK1QXGR": ("CMZB", "Commerzbank AG", "SNRLAC", "Mz", .40),
    "QZ3PQZ8XHQCG": ("UCGIM", "UniCredit S.p.A.", "SUB", "Sub", .20),
    "QZBB27CTWXW3": ("SANTAN", "Banco Santander, S.A.", "SUB", "Sub", .20),
    "QZ99LCVLS3HJ": ("ISPIM", "Intesa Sanpaolo S.p.A.", "SUB", "Sub", .20),
}
TW_TIER = {"SEN": "senior", "SNRLAC": "senior_non_preferred", "SUB": "subordinated"}
TW_REPORT = "https://reports.tradeweb.com/DownloadSbSef.adl?file=/TWSBSEF-CDS-Report_{day}.xml"
TRADEWEB_PROVIDER = "Tradeweb TW SBSEF · clôture transactionnelle + DTCC/SEC"
TRANSACTION_BASE_DATE = "2026-05-20"


def parse_tradeweb_cds(xml: bytes, sec: bytes, day: object, *, collected_at: object,
                       linked_invalid_ids: set[str] | None = None) -> pd.DataFrame:
    """Match single-trade SBSEF close, named UPI and exact maturity from DTCC."""
    from hashlib import sha256
    from xml.etree import ElementTree as ET

    day = _date(day)
    date_text = str(day.date())
    try:
        report = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError("Invalid Tradeweb regulatory report XML.") from exc
    if (report.tag != "dailyMarketDataReport" or report.findtext("date") != date_text
            or report.findtext("sbsef") != "TW SEF LLC"):
        raise ValueError("Tradeweb publication does not identify the requested venue and date.")
    generated = pd.to_datetime(report.findtext("generated"), utc=True)
    stamp = pd.to_datetime(collected_at, utc=True)
    if pd.isna(generated) or pd.isna(stamp) or generated < day.tz_localize("UTC") or stamp < generated:
        raise ValueError("Invalid Tradeweb generation or collection date.")
    raw = _dtcc_csv(sec)
    match = {"Action type": "NEWT", "Event type": "TRAD", "Platform identifier": "TWSF",
             "Asset Class": "CR", "Notional currency-Leg 1": "EUR", "Cleared": "I",
             "Package indicator": "FALSE"}
    coupon_fields = {"Fixed rate day count convention-leg 1": "A004",
                     "Fixed rate payment frequency period-Leg 1": "MNTH",
                     "Fixed rate payment frequency period multiplier-Leg 1": "3"}
    time_fields = {"Effective Date", "Expiration Date", "Execution Timestamp"}
    _require(raw, set(DTCC_CDS_FIELDS) | set(match) | set(coupon_fields) | time_fields)
    invalid = set(raw.loc[raw["Action type"].isin(["CORR", "EROR"]), "Original Dissemination Identifier"])
    invalid.update(linked_invalid_ids or ())
    tape = raw[raw["Execution Timestamp"].str[:10].eq(date_text)
               & raw["Dissemination Identifier"].ne("")
               & ~raw["Dissemination Identifier"].isin(invalid)
               & raw["Non-standardized term indicator"].isin(["", "FALSE"])]
    for col, val in match.items():
        tape = tape[tape[col].eq(val)]
    for col, val in coupon_fields.items():
        tape = tape[tape[col].isin(["", val])]
    rows = []
    for node in report.findall("sbsTnr"):
        upi = node.findtext("upi", "")
        if upi not in TW_SOURCES or node.findtext("ccy") != "EUR":
            continue
        ticker, bank, tier, fisn, recovery = TW_SOURCES[upi]
        name = node.findtext("sbsNm", "")
        words = name.split()
        tier_status = TW_TIER[tier]
        ticket = tape[(tape["Unique Product Identifier"].eq(upi))
                      & tape["UPI FISN"].eq(f"NA/CDS Corp SN {fisn}")]
        if len(words) != 5 or words[:2] != [ticker, tier] or words[-1] != "EUR" or len(ticket) != 1:
            continue
        record = ticket.iloc[0]
        if node.findtext("prcFrmt") != "SprdBsisPts" or node.findtext("tradCnt") != "1":
            continue
        try:
            coupon = float(words[3])
            levels = np.array([float(node.findtext(x, "nan"))
                               for x in ("opngPrc", "clsgPrc", "lwstPrc", "hgstPrc")])
            dtcc_coupon = float(record["Fixed rate-Leg 1"]) / BP
            term, effective = _date(record["Expiration Date"]), _date(record["Effective Date"])
            maturity = (term - day).days / 365
            named_tenor = float(words[2].removesuffix("Y"))
        except (TypeError, ValueError):
            continue
        if (not np.isfinite(levels).all() or levels[0] <= 0 or not np.all(levels == levels[0])
                or not np.isfinite([dtcc_coupon, coupon, named_tenor]).all() or coupon <= 0
                or abs(dtcc_coupon-coupon) > 1e-10 or abs(levels[0]-coupon) < 1e-8
                or not 0 < maturity <= 10 or abs(named_tenor - maturity) > .6
                or not 0 <= (effective-day).days <= 3):
            continue
        rows.append({"valuation_date": date_text, "source_quote_type": "transaction_closing_spread",
                     "quote_type": "transaction_closing_spread", "spread": levels[0], "unit": "bps",
                     "bank_name": bank, "entity_id": upi, "reference_entity_id": f"tradeweb:{ticker}",
                     "maturity_date": str(term.date()), "effective_date": str(effective.date()),
                     "reported_tenor": node.findtext("tenor"), "symbol": name,
                     "currency": "EUR", "seniority": tier_status, "restructuring": "undisclosed",
                     "recovery": recovery, "recovery_status": "ISDA_standard_model_assumption",
                     "provider": TRADEWEB_PROVIDER, "collected_at": stamp.isoformat(),
                     "generated_at": generated.isoformat(), "data_kind": "observed", "time_basis": "ACT/365F",
                     "source_url": TW_REPORT.format(day=date_text), "response_sha256": sha256(xml).hexdigest(),
                     "dtcc_source_url": dtcc_report_url(day, "sec", "CREDITS"),
                     "dtcc_response_sha256": sha256(sec).hexdigest(),
                     "dtcc_dissemination_id": record["Dissemination Identifier"],
                     "dtcc_execution_at": record["Execution Timestamp"],
                     "dtcc_reference_code": record["Underlier ID-Leg 1"],
                     "dtcc_reference_code_type": record["Underlier ID source-Leg 1"],
                     "dtcc_reference_name": record["UPI Underlier Name"],
                     "dtcc_notional_reported": record["Notional amount-Leg 1"],
                     "dtcc_clearing_status": "intent_to_clear",
                     "dtcc_upfront_amount_unsigned": record["Other payment amount"],
                     "dtcc_upfront_currency": record["Other payment currency"],
                     "coupon_bps": coupon, "dtcc_coupon_day_count": record["Fixed rate day count convention-leg 1"],
                     "dtcc_coupon_frequency": record["Fixed rate payment frequency period-Leg 1"],
                     "dtcc_coupon_frequency_multiplier": record["Fixed rate payment frequency period multiplier-Leg 1"],
                     "model_cds_convention": "ECE_standard_quarterly_ACT365F_accrual_on_default",
                     "matching_rule": "date_UPI_TWSF_one_outright_TRADE_intent_to_clear_and_consistent_fixed_coupon"})
    output = pd.DataFrame(rows)
    if not output.empty and output.duplicated(["entity_id", "maturity_date"]).any():
        raise ValueError("Duplicate matched Tradeweb contracts.")
    return output


def refresh_tradeweb_cds(day: object, *, root: Path = BASE / "data") -> pd.DataFrame:
    day = _date(day)
    if day.date() >= pd.Timestamp.now(tz="UTC").date():
        raise ValueError("Use a completed day for the Tradeweb public report.")
    xml = _public_get(TW_REPORT.format(day=day.date()), max_bytes=2_000_000)
    sec = _public_get(dtcc_report_url(day, "sec", "CREDITS"))
    quotes = parse_tradeweb_cds(xml, sec, day, collected_at=pd.Timestamp.now(tz="UTC"))
    if quotes.empty:
        return quotes
    key = ["valuation_date", "entity_id", "maturity_date"]
    path = _public_file("tradeweb", root)
    with _ECB_CACHE_LOCK:
        if path.is_file():
            old = pd.read_csv(path, dtype={"dtcc_dissemination_id": str})
            merged = pd.concat([old, quotes], ignore_index=True).drop_duplicates(key, keep="first")
        else:
            merged = quotes
        _atomic_csv(merged.sort_values(key), path)
    return quotes


def transaction_credit_data(*, root: Path = BASE / "data") -> pd.DataFrame:
    """Use observed closes as running equivalents only inside the academic pricer."""
    source = pd.read_csv(_public_file("tradeweb", root), dtype={"dtcc_dissemination_id": str})
    _require(source, {"entity_id", "bank_name", "seniority", "recovery", "spread", "unit", "coupon_bps",
                      "quote_type", "source_quote_type", "dtcc_dissemination_id", "dtcc_notional_reported"})
    if not (source["quote_type"].eq("transaction_closing_spread")
            & source["source_quote_type"].eq("transaction_closing_spread")
            & source["unit"].eq("bps")).all():
        raise ValueError("Unexpected source quote type or unit in the electronic trade cache.")
    identity = {upi: (name, TW_TIER[tier], recovery)
                for upi, (_, name, tier, _, recovery) in TW_SOURCES.items()}
    expected = [identity.get(upi) for upi in source["entity_id"]]
    if any(spec != actual for spec, actual in zip(expected, zip(
            source["bank_name"], source["seniority"], source["recovery"]))):
        raise ValueError("Tradeweb UPI, bank, debt tier and recovery assumption must match.")
    audit_id = source["dtcc_dissemination_id"]
    if not audit_id.str.fullmatch(r"\d+", na=False).all() or not audit_id.is_unique:
        raise ValueError("Missing or duplicate DTCC dissemination IDs in public cache.")
    coupon, spread = _numeric(source["coupon_bps"]), _numeric(source["spread"])
    if (not np.isfinite(coupon).all() or not np.isfinite(spread).all() or (coupon <= 0).any()
            or (spread <= 0).any() or np.isclose(coupon, spread, atol=1e-8, rtol=0).any()):
        raise ValueError("A public transaction spread must be positive and distinct from its coupon.")
    return load_quotes(source.assign(
        quote_type="par_spread",
        spread_model_mapping="published_execution_spread_as_model_par_equivalent",
        quote_quality="option_B_academic_equivalence",
        market_par_verified=False,
        dtcc_notional_capped=source["dtcc_notional_reported"].astype("string").str.endswith("+", na=False)),
        "SNAPSHOT")


def run_transaction_study(folder: Path) -> None:
    """Study date-by-date, tier-by-tier. Never forward-fill CDS or pool debt tiers."""
    quotes = transaction_credit_data()
    discounts = {source: public_discount_data(source)[1] for source in ("ecb_aaa", "ecb_all", "dtcc_ois")
                 if _public_file(source, BASE / "data").exists()}
    fits, baskets, probabilities = [], [], []
    for (day, tier), snapshot in quotes.groupby(["valuation_date", "seniority"]):
        available = {name: curves[day] for name, curves in discounts.items() if day in curves}
        if not available:
            continue
        fit, basket = discount_sensitivity(snapshot, available, mode="SNAPSHOT")
        fits.append(fit)
        baskets.append(basket.assign(seniority=tier, entities=";".join(sorted(snapshot["entity_id"]))))
        preferred = available.get("dtcc_ois") or available["ecb_aaa"]
        cal = calibrate_credit(snapshot, preferred, mode="SNAPSHOT")
        for entity, curve in cal.curves.items():
            last = float(curve.edges[-1])
            grid = np.unique(np.r_[0., COUPON_TIMES[COUPON_TIMES <= last], last])
            status = np.where(grid == last, "fitted_maturity", "within_single_trade_model")
            probabilities.append(pd.DataFrame({"valuation_date": day, "entity_id": entity, "seniority": tier,
                "discount_curve_kind": preferred.curve_kind, "time_years": grid,
                "survival": curve.survival_probability(grid), "cumulative_pd": curve.default_probability(grid),
                "hazard_annual": curve.hazard_rate(grid), "time_status": status, "tail_multiplier": np.nan}))
            for horizon in (5., 7.):
                if horizon <= last:
                    continue
                tail = np.array([.5, 1., 1.5])
                hazard = tail*curve.lambdas[-1]
                survival = float(curve.survival_probability(last))*np.exp(-hazard*(horizon-last))
                probabilities.append(pd.DataFrame({"valuation_date": day, "entity_id": entity, "seniority": tier,
                    "discount_curve_kind": preferred.curve_kind, "time_years": horizon, "survival": survival,
                    "cumulative_pd": 1-survival, "hazard_annual": hazard, "time_status": "tail_scenario",
                    "tail_multiplier": tail}))
    if not fits:
        raise ValueError("No matched public CDS date overlaps a discount curve.")
    folder.mkdir(parents=True, exist_ok=True)
    result = pd.concat(fits, ignore_index=True)
    result.to_csv(folder / "transaction_repricing_sensitivity.csv", index=False)
    pd.concat(baskets, ignore_index=True).to_csv(folder / "transaction_first_default.csv", index=False)
    pd.concat(probabilities, ignore_index=True).to_csv(folder / "transaction_survival.csv", index=False)
    history = result[result["scenario"].eq("baseline") & result["discount_case"].isin(["dtcc_ois", "ecb_aaa"])]
    priority = history["discount_case"].map({"dtcc_ois": 0, "ecb_aaa": 1})
    history = history.assign(priority=priority).sort_values("priority").drop_duplicates(
        ["valuation_date", "entity_id", "maturity_date"])
    history = history.sort_values(["valuation_date", "bank_name", "seniority"])
    summary = history[["bank_name", "seniority", "valuation_date", "maturity_date", "tenor_years",
                       "spread_bps", "coupon_bps", "source_quote_type", "quote_quality", "market_par_verified",
                       "recovery", "model_bps", "residual_bps", "hazard_annual", "survival", "cumulative_pd",
                       "all_invariants_pass", "discount_case", "discount_curve_kind", "provider",
                       "source_url", "dtcc_source_url", "dtcc_dissemination_id"]]
    summary.assign(status="one_observed_transaction_maturity_per_bank", higher_tenor_holdout="unavailable",
                   historical_grade="retrospective_dated_snapshots").to_csv(
                       folder / "transaction_snapshots.csv", index=False)




def _run_snapshot(args: argparse.Namespace) -> None:
    import json

    quotes = load_quotes(args.quotes, "SNAPSHOT")
    discounts = load_discount_curves(args.discount, "SNAPSHOT")
    day = _date(args.date) if args.date else quotes["valuation_date"].max()
    snapshot = quotes[quotes["valuation_date"].eq(day)]
    if snapshot.empty or day not in discounts:
        raise ValueError(f"Both CDS and EUR factors are required on {day.date()}.")
    calibration = calibrate_credit(snapshot, discounts[day], basket=args.bank, method=args.method,
                                   extrapolate=args.extrapolate)
    cap = min(discounts[day].times[-1], 7.)
    horizon = cap if args.extrapolate else min(cap, *(c.edges[-1] for c in calibration.curves.values()))
    grid = np.unique(np.r_[0., COUPON_TIMES[COUPON_TIMES <= horizon + 1e-12], horizon])
    history = None
    if args.backtest_start:
        history = backtest_credit(quotes, discounts, basket=list(calibration.curves), start=args.backtest_start,
                                  end=args.backtest_end, method=args.method, extrapolate=args.extrapolate,
                                  holdout_extra_tenors=(3,), require_point_in_time=args.require_point_in_time)
    args.output.mkdir(parents=True, exist_ok=True)
    calibration.repricing.to_csv(args.output / "repricing.csv", index=False)
    calibration.probabilities(grid).to_csv(args.output / "probabilities_semiannual.csv", index=False)
    validate_credit(calibration).to_csv(args.output / "invariants.csv", index=False)
    if history is not None:
        _export_backtest(history, args.output)
    summary = {
        "mode": "SNAPSHOT", "data_kind_declared_by_inputs": "observed", "valuation_date": day.date().isoformat(),
        "cds_providers": list(calibration.repricing["provider"].unique()), "discount_provider": discounts[day].provider,
        "bank_ids": list(calibration.curves), "basket_selection": "explicit" if args.bank else "all_snapshot_entities",
        "requested_horizon_years": 7, "reported_horizon_years": float(horizon),
        "horizon_uses_extrapolation": any(horizon > c.edges[-1] + 1e-12 for c in calibration.curves.values()),
        "independent_first_default_pd": float(1 - basket_survival(calibration.curves, horizon)),
        "max_abs_repricing_error_bps": float(calibration.repricing["residual_bps"].abs().max()),
        "calibration_method_requested": args.method, "warnings": list(calibration.warnings),
    }
    (args.output / "diagnostics.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"SNAPSHOT {day.date()} | {len(calibration.repricing)} quotes | "
          f"reported horizon {horizon:g}Y | {args.output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the ECE credit demonstration or a dated snapshot.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quick", action="store_true", help="Skip stochastic benchmarks and joint repricing.")
    parser.add_argument("--quotes", type=Path, help="Authorized snapshot or history of EUR par quotes.")
    parser.add_argument("--discount", type=Path, help="Same-date EUR discount curve(s).")
    parser.add_argument("--date", help="Valuation date or completed DTCC archive date.")
    parser.add_argument("--bank", action="append", help="Select an entity ID. Repeat for a basket.")
    parser.add_argument("--method", choices=["strict", "constrained"], default="strict")
    parser.add_argument("--extrapolate", action="store_true", help="Allow the last hazard to extend beyond its pillar.")
    parser.add_argument("--backtest-start", help="First historical date, inclusive.")
    parser.add_argument("--backtest-end", help="Last historical date, inclusive.")
    parser.add_argument("--require-point-in-time", action="store_true", help="Require release timestamp evidence.")
    public = parser.add_mutually_exclusive_group()
    public.add_argument("--transaction-study", action="store_true", help="Calibrate cross-verified public CDS prints.")
    public.add_argument("--public-study", action="store_true", help="Same-date public-rate sensitivity on demo CDS.")
    public.add_argument("--refresh-public", choices=[*PUBLIC_SOURCES, "dtcc_ois", "dtcc_cds", "tradeweb_cds"],
                        help="Explicit, bounded refresh of an authorized public source.")
    parser.add_argument("--start", help="First BCE reference date.")
    parser.add_argument("--end", help="Last BCE reference date.")
    options = parser.parse_args()
    if options.public_study or options.transaction_study or options.refresh_public:
        if (options.quotes or options.discount or options.bank or options.extrapolate or options.quick
                or options.backtest_start or options.backtest_end or options.require_point_in_time
                or options.method != "strict"):
            parser.error("Public commands use only the date, the ECB request window and the study output.")
        try:
            if options.transaction_study:
                if options.date or options.start or options.end:
                    parser.error("The transaction study spans all verified cached dates.")
                target = options.output or BASE / "outputs/public"
                if target.resolve() == (BASE / "outputs").resolve():
                    parser.error("Use a separate output directory for the public study.")
                run_transaction_study(target)
                print(f"Transaction study written to {target}")
            elif options.public_study:
                if options.start or options.end:
                    parser.error("Use --date for the public study.")
                target = options.output or BASE / "outputs/public"
                if target.resolve() == (BASE / "outputs").resolve():
                    parser.error("Use a separate output directory for the public study.")
                run_public_study(target, options.date or PUBLIC_STUDY_DATE)
                print(f"Public-rate sensitivity written to {target}")
            elif options.output:
                parser.error("Refresh writes to the project data cache.")
            elif options.refresh_public in PUBLIC_SOURCES:
                if not (options.start and options.end) or options.date:
                    parser.error("ECB refresh requires --start and --end.")
                data = refresh_ecb(options.refresh_public, options.start, options.end)
                print(f"Public cache · {options.refresh_public} · {data['valuation_date'].max()} · {len(data)} rows")
            else:
                if not options.date or options.start or options.end:
                    parser.error("DTCC refresh requires one completed --date.")
                fetch = {"dtcc_ois": refresh_dtcc_ois, "dtcc_cds": refresh_dtcc_cds,
                         "tradeweb_cds": refresh_tradeweb_cds}[options.refresh_public]
                data = fetch(options.date)
                print(f"Public cache · {options.refresh_public} · {options.date} · {len(data)} rows")
        except (OSError, ValueError, ImportError) as error:
            parser.error(str(error))
        raise SystemExit(0)
    if options.start or options.end:
        parser.error("--start and --end are reserved for public ECB refreshes.")
    if bool(options.quotes) != bool(options.discount):
        parser.error("--quotes and --discount must be supplied together.")
    if bool(options.backtest_start) != bool(options.backtest_end):
        parser.error("Supply both --backtest-start and --backtest-end.")
    if options.require_point_in_time and not options.backtest_start:
        parser.error("--require-point-in-time requires a backtest window.")
    if options.quotes:
        if options.output is None:
            parser.error("Supply a separate --output directory for a snapshot run.")
        if options.output.resolve() == (BASE / "outputs").resolve():
            parser.error("Use a separate --output directory from the DEMO outputs.")
        try:
            _run_snapshot(options)
        except (ValueError, OSError) as error:
            parser.error(str(error))
    else:
        if (options.date or options.bank or options.extrapolate or options.backtest_start
                or options.require_point_in_time or options.method != "strict"):
            parser.error("Snapshot options require --quotes and --discount.")
        run_demo(options.output or BASE / "outputs", full=not options.quick)

