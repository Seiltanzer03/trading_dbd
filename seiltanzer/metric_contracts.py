"""Machine-readable quantitative metric contracts.

This registry is the source of truth for metric provenance, units and decision
authority.  It deliberately does not calculate metrics.  Phase A uses it to
prevent later visual/AI work from silently changing a label, counting several
transforms of one source as independent evidence, or promoting contextual data
to policy authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


Disposition = Literal[
    "KEEP",
    "KEEP + IMPROVE",
    "VISUAL CONTEXT ONLY",
    "MERGE WITH EXISTING FAMILY",
    "REMOVE FROM AI AUTHORITY",
    "BROKEN / FIX",
]


@dataclass(frozen=True)
class MetricContract:
    key: str
    raw_source: str
    formula: str
    units: str
    update_cadence: str
    stale_rules: str
    family: str
    independent_vote: bool
    practical_meaning: str
    ai_influence: str
    duplication_risk: str
    failure_modes: str
    disposition: Disposition

    def as_dict(self) -> dict:
        return asdict(self)


def _m(
    key: str,
    source: str,
    formula: str,
    units: str,
    cadence: str,
    stale: str,
    family: str,
    meaning: str,
    influence: str,
    duplication: str,
    failures: str,
    disposition: Disposition,
    *,
    independent_vote: bool = False,
) -> MetricContract:
    return MetricContract(
        key=key,
        raw_source=source,
        formula=formula,
        units=units,
        update_cadence=cadence,
        stale_rules=stale,
        family=family,
        independent_vote=independent_vote,
        practical_meaning=meaning,
        ai_influence=influence,
        duplication_risk=duplication,
        failure_modes=failures,
        disposition=disposition,
    )


# Individual transforms never cast their own independent vote.  Policy gates
# consume a family-level state plus hard constraints.  This is especially
# important for the option_distribution family, where IV, skew, quantiles,
# barrier probabilities and GEX often originate from the same option snapshot.
CONTRACTS: tuple[MetricContract, ...] = (
    _m("option.p_take_first_touch", "mapped option chain + live price/proxy; rn_cone paths",
       "mean(path first absorbs at take before horizon)", "probability [0,1]",
       "price-sensitive cache key; chain refresh on chain cadence",
       "unavailable when option anchor/tail support is invalid; expose chain age",
       "option_distribution", "Finite-horizon favorable first-touch mass.",
       "Core option-family state and policy scenario input; never a standalone vote.",
       "Same paths also produce p_stop, no_touch, EV, hazards and touch times.",
       "proxy basis error; stale chain; insufficient strike support; MC error",
       "KEEP + IMPROVE"),
    _m("option.p_stop_first_touch", "same paths as option.p_take_first_touch",
       "mean(path first absorbs at stop before horizon)", "probability [0,1]",
       "same as p_take", "same as p_take", "option_distribution",
       "Finite-horizon adverse first-touch mass.",
       "Core option-family state and local-risk input.",
       "Perfectly dependent on p_take/no_touch under the three-outcome identity.",
       "same as p_take; near-barrier discretisation if bridge correction regresses",
       "KEEP + IMPROVE"),
    _m("option.p_no_touch", "same paths as option first-touch probabilities",
       "1 - p_take_first_touch - p_stop_first_touch", "probability [0,1]",
       "same as p_take", "same as p_take", "option_distribution",
       "Mass unresolved inside the current option horizon; not loss probability.",
       "Time-pressure/context inside the option family.",
       "Exact complement of the two touch masses.",
       "incorrect normalization into stop; floating-point sum drift", "KEEP"),
    _m("option.barrier_ev_r", "option first-touch probabilities + final take R",
       "T * p_take_first_touch - p_stop_first_touch", "R (barrier component)",
       "same as first-touch paths", "null when option anchor is invalid",
       "option_distribution", "Expected contribution of resolved barrier events only.",
       "Core family feature; policy uses full path outcomes separately.",
       "Algebraic transform of p_take/p_stop, not independent evidence.",
       "mislabelled as full trade EV; ignores mark-to-horizon value of no-touch paths",
       "KEEP + IMPROVE"),
    _m("option.first_touch_median", "competing take and stop path times",
       "median(time | take), median(time | stop), plus legacy any-touch median",
       "minutes or years",
       "same as first-touch paths", "null when no path resolves",
       "option_distribution", "Separate typical take and stop touch clocks.",
       "Time context inside the option family.",
       "Same path family as hazards and touch probabilities.",
       "legacy median_years remains mixed and must not be labelled take median",
       "KEEP"),
    _m("option.rnd_q10_q50_q90", "free-horizon option-parameter scenario distribution",
       "weighted histogram quantiles at 10%, 50%, 90%", "R",
       "cone cache key", "scenario-only when option anchor is unavailable",
       "option_distribution", "Center and tails of terminal scenario geometry.",
       "Option-family geometry and stress input.",
       "Same distribution as width, tail ratio and lattice revaluation.",
       "currently labelled full RND although generated from fitted sigma/skew proxy",
       "KEEP + IMPROVE"),
    _m("option.distribution_width", "option.rnd_q10_q50_q90",
       "q90 - q10", "R", "derived on each accepted distribution snapshot",
       "requires finite ordered quantiles", "option_distribution",
       "Compression/expansion of the scenario distribution.",
       "Shadow/context until calibrated.", "Direct transform of RND quantiles.",
       "center motion can be confused with widening; poor tails", "KEEP + IMPROVE"),
    _m("option.tail_asymmetry", "option.rnd_q10_q50_q90",
       "log((q90-q50)/max(q50-q10, eps)), signed favorable/adverse", "log ratio",
       "derived on each accepted distribution snapshot", "requires separated tails",
       "option_distribution", "Whether favorable or adverse terminal tail is expanding.",
       "Shadow/context until calibrated.", "Direct transform of the same quantiles.",
       "unstable when either tail is near zero; direction sign error", "KEEP + IMPROVE"),
    _m("option.iv", "ATM option mids/IV surface", "annualized implied volatility",
       "annualized fraction or percent (label explicitly)", "option-chain cadence",
       "chain age >30m blocks strong authority; source status preserved",
       "option_distribution", "Market-implied variance scale.",
       "Path variance input after source-quality weighting.",
       "Drives probabilities, RND width, VRP and policy paths.",
       "stale/delayed proxy; percent-vs-fraction confusion", "KEEP"),
    _m("option.rv", "observed daily/intraday closes", "annualized std(log returns)",
       "annualized fraction", "daily/intraday refresh", "requires sufficient bars",
       "realized_volatility", "Observed variance regime.",
       "Scenario/confidence input, not a directional vote.",
       "Shares price history with ATR, macro and wavelet.",
       "small sample; gaps; mixed session calendars", "KEEP"),
    _m("option.vrp", "IV and RV", "IV/RV ratio and IV-RV spread",
       "ratio / volatility points", "min(option, realized-vol cadence)",
       "invalid if either leg is missing/non-positive", "option_distribution",
       "Relative price of implied versus realized volatility.",
       "Vol-expansion stress/context inside option family.",
       "Algebraic transform of IV and RV, not independent.",
       "mixed horizons; stale IV against fresh RV", "KEEP + IMPROVE"),
    _m("option.skew", "OTM call/put IV", "IV(call OTM)-IV(put OTM), direction-normalized",
       "volatility points", "option-chain cadence", "anomalous or delayed values are context-only",
       "option_distribution", "Directional tail pricing within the option smile.",
       "Shape input/context within option family.",
       "Same surface drives tail geometry and probabilities.",
       "proxy inversion; sparse strikes; smile interpolation", "KEEP"),
    _m("option.term_slope", "ATM IV across real expiries", "(IV_far-IV_near)/IV_near",
       "ratio", "IV-surface cadence", "needs at least two valid expiries",
       "option_distribution", "Whether variance is front- or back-loaded.",
       "Path variance-timing input within option family.",
       "Same option surface as IV and skew.",
       "expiry mismatch; event premium; far extrapolation", "KEEP"),
    _m("option.derivative_state", "time series of accepted option-family snapshots",
       "robust time slopes/accelerations with noise and confidence", "metric unit per minute",
       "only after new accepted observations", "minimum samples/time span; stale/source weights",
       "option_distribution", "Direction and acceleration of option-state change.",
       "Shadow/context first; later scenario weights after validation.",
       "All derivatives remain one option family.",
       "two-point noise; irregular timestamps; changing horizon masquerading as edge change",
       "KEEP + IMPROVE"),
    _m("option.first_touch_hazard", "first-touch path times",
       "P(touch in next window | no prior touch)", "conditional probability / window",
       "same accepted path simulation", "minimum survivors/events per hazard bin",
       "option_distribution", "Immediate local take/stop risk among surviving paths.",
       "Shadow/context and stress weighting before policy authority.",
       "Same paths as global first-touch probabilities.",
       "empty survivor set; sparse events; horizon/bin sensitivity", "KEEP + IMPROVE"),
    _m("gex.profile", "option OI and BS gamma with assumed call+/put- sign",
       "gamma * OI * 100 * S^2 * 0.01", "currency exposure proxy",
       "option-chain cadence", "disabled for inverse proxy; dealer sign unobserved",
       "option_distribution", "Location of heuristic gamma concentrations.",
       "Visual/context only.", "Same option chain and assumed positioning as walls/flip.",
       "unknown dealer sign; stale OI; proxy mapping", "VISUAL CONTEXT ONLY"),
    _m("gex.field_force_stiffness", "gex.profile", "Gaussian field; -dFIELD/dS; d2FIELD/dS2",
       "normalized field / per-price derivatives", "option-chain or live-price change",
       "no authority without valid GEX profile", "option_distribution",
       "Local field slope and curvature around live price.",
       "Shadow/context only until predictive validation.",
       "Analytic transforms of GEX profile, not new votes.",
       "bandwidth sensitivity; sign assumption; unit scaling", "KEEP + IMPROVE"),
    _m("gex.wall_flip_migration", "time-ordered mapped GEX/OI snapshots",
       "level distance and robust level velocity", "price, R, price/minute",
       "chain snapshot cadence", "requires multiple time-separated snapshots",
       "option_distribution", "Whether walls/zero-gamma approach or recede.",
       "Visual/context within option family.", "Same chain/GEX family.",
       "wall identity switching; irregular spacing; stale snapshots", "KEEP + IMPROVE"),
    _m("price.live_impulse", "observed terminal/broker price history",
       "direction-normalized return/move over 5/15/60m", "R and price points",
       "price tick", "requires real observations and aligned timestamps", "live_price",
       "Current favorable/adverse price impulse.", "Independent market family/context for policy gate.",
       "Shares underlying price history with ATR, macro and wavelet.",
       "basis changes; delayed ticks; nearest-sample window error", "KEEP"),
    _m("levels.vwap_poc_value_area", "intraday price/volume profile",
       "VWAP/profile POC/value-area and distance from price", "price and R",
       "intraday refresh", "TPO approximation must be labelled", "orderflow_levels",
       "Accepted value and nearby structural friction.", "Independent family only when directly observed.",
       "Multiple levels from one profile count once.",
       "thin volume data; TPO approximation; stale session", "KEEP + IMPROVE"),
    _m("filters.strategy", "configured/manual setup filters", "PASS/BLOCK/MANUAL",
       "categorical", "on relevant data/manual update", "manual remains manual",
       "strategy_filters", "Hard setup/management constraints.",
       "Hard constraint; not probabilistic evidence.", "Not a statistical vote.",
       "15m context masquerading as full higher-timeframe sequence", "KEEP"),
    _m("correlation.rho", "observed rolling cross-asset matrix",
       "Pearson/producer rolling correlation", "[-1,1]", "correlation feed cadence",
       "finite off-diagonal only; preserve source timestamp", "correlation",
       "Current cross-asset coupling topology.", "Context/confidence family, no directional edge authority.",
       "All links belong to one matrix family.",
       "missing matrix cells; mismatched windows; non-synchronous markets", "KEEP"),
    _m("correlation.velocity_tension", "history of observed correlation matrices",
       "delta rho over stated elapsed window; bounded tension attribution",
       "delta correlation and normalized score", "only after new matrix snapshot",
       "requires time-separated history; no causal direction", "correlation",
       "Relationship break speed and network stress.", "Context/stress scenario input.",
       "Transforms of correlation family, not independent votes.",
       "irregular sampling; in-memory history reset; delta mistaken for per-time slope",
       "KEEP + IMPROVE"),
    _m("macro.xyz", "real 5m price history + vol index + correlation stress",
       "X trend z-state; Y vol state; Z fragility/stress", "normalized state",
       "market-history cache / analytics refresh", "unavailable below 24 real bars",
       "macro_context", "Current regime and transition context.",
       "Strategy/context only; no independent predictive vote.",
       "Shares price, vol and correlation inputs with other families.",
       "runtime adapter bypass; stale history; synthetic prototype invocation", "VISUAL CONTEXT ONLY"),
    _m("macro.velocity_acceleration", "macro.xyz trajectory",
       "finite state displacement divided by real elapsed hours", "state units/hour and /hour^2",
       "on new historical/current point", "zero motion without new state",
       "macro_context", "Speed of regime transition.", "Context/stress timing only.",
       "Derivative of macro.xyz.", "minimum dt floor; repeated current point", "KEEP + IMPROVE"),
    _m("wavelet.energy", "real detrended log-price history", "Morlet CWT power by period band",
       "relative energy percent", "history refresh", "unavailable below 36 real bars",
       "derived_price_context", "Where observed price-cycle energy is concentrated.",
       "Visual/context only.", "Same price history as live price/macro; bands sum to one mix.",
       "cone-of-influence; irregular bars; over-interpreted cycles", "VISUAL CONTEXT ONLY"),
    _m("wavelet.energy_transfer", "successive real wavelet.energy states",
       "change in destination energy minus source energy per elapsed time",
       "percentage points per minute", "only after new CWT input", "zero without new data",
       "derived_price_context", "Observed migration of spectral energy.",
       "Visual/context only.", "Derivative of wavelet energy bands.",
       "clock-driven fake flow; edge effects; normalization jumps", "KEEP + IMPROVE"),
    _m("policy.expected_net_r", "policy outcome path distribution incl. execution costs",
       "mean(net policy outcomes)", "R", "AI review / policy recomputation",
       "source and scenario stability reported", "policy_outcome",
       "Average modeled net result for each action.", "Primary optimizer statistic subject to hard risk gate.",
       "Same policy paths as median/CVaR/P(loss).", "model misspecification; assumed cost", "KEEP"),
    _m("policy.median_net_r", "same policy outcomes", "median(net policy outcomes)", "R",
       "AI review", "same as expected net R", "policy_outcome",
       "Typical modeled net outcome.", "Secondary policy diagnostic.",
       "Same outcome sample as expected/CVaR.", "multimodal distribution hides tails", "KEEP"),
    _m("policy.cvar10_net_r", "same policy outcomes", "mean(worst 10% net outcomes)", "R",
       "AI review", "hard floor uses net basis", "policy_outcome",
       "Average severe-tail outcome.", "Hard risk feasibility constraint.",
       "Same outcome sample; not independent confirmation.", "MC error; wrong gross/net floor", "KEEP"),
    _m("policy.p_loss", "same policy outcomes", "mean(net outcome < 0)", "probability [0,1]",
       "AI review", "same as policy outcomes", "policy_outcome",
       "Modeled probability of a negative final outcome.", "Policy diagnostic/stress output.",
       "Same outcome sample as Expected/median/CVaR.", "threshold at zero; ignores loss size", "KEEP"),
    _m("policy.stress_stability", "policy recomputation under parameter/source scenarios",
       "winner/feasibility share across explicit scenarios", "share [0,1]",
       "AI review", "scenario list and floors must be identical/auditable",
       "policy_robustness", "Whether an action survives plausible model states.",
       "Action authority gate.", "Not new market evidence; robustness of same policy.",
       "magic scenarios; inconsistent floors/costs; small ensemble", "KEEP + IMPROVE"),
    _m("execution.cost_r", "explicit broker costs or labelled fallback",
       "spread+commission+slippage+delay in R", "R", "trade/tick update",
       "assumed fallback must remain visible and stress-tested", "execution",
       "Net cost of changing the position.", "Deduct from every policy consistently.",
       "Not evidence; an outcome adjustment.", "fallback dominates small advantages; missing turnover cost", "KEEP + IMPROVE"),
    _m("strategy.ladder_be", "configured ladder + observed max R + explicit stop",
       "partial closes at rungs; BE only after configured threshold", "R and fraction",
       "price tick / execution acknowledgement", "do not infer execution without acknowledgement",
       "strategy_constraint", "Existing mechanical management of the remaining position.",
       "Hard constraint/baseline policy behavior.", "Not an evidence vote.",
       "double-counted rung; unacknowledged close; candle trailing not simulated", "KEEP"),
)


_BY_KEY = {contract.key: contract for contract in CONTRACTS}


def metric_contract(key: str) -> MetricContract:
    return _BY_KEY[key]


def metric_contracts() -> list[dict]:
    return [contract.as_dict() for contract in CONTRACTS]


def validate_contracts() -> list[str]:
    errors: list[str] = []
    if len(_BY_KEY) != len(CONTRACTS):
        errors.append("metric contract keys must be unique")
    allowed = {
        "KEEP", "KEEP + IMPROVE", "VISUAL CONTEXT ONLY",
        "MERGE WITH EXISTING FAMILY", "REMOVE FROM AI AUTHORITY", "BROKEN / FIX",
    }
    for contract in CONTRACTS:
        if contract.disposition not in allowed:
            errors.append(f"{contract.key}: invalid disposition")
        for field in (
            "raw_source", "formula", "units", "update_cadence", "stale_rules",
            "family", "practical_meaning", "ai_influence", "duplication_risk",
            "failure_modes",
        ):
            if not getattr(contract, field).strip():
                errors.append(f"{contract.key}: empty {field}")
    return errors
