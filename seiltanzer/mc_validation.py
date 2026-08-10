"""Monte-Carlo convergence and seed-robustness diagnostics.

These routines measure numerical uncertainty; they never promote a shadow
model or alter policy arithmetic.
"""
from __future__ import annotations

from collections import Counter
from typing import Callable, Iterable


DEFAULT_SEEDS = (0xF001, 0xF002, 0xF003, 0xF004, 0xF005)


def _ranking(metrics: dict[str, dict]) -> tuple[str, ...]:
    return tuple(sorted(
        metrics,
        key=lambda name: (
            float(metrics[name].get("expected_final_r") or -999.0),
            float(metrics[name].get("cvar10_r") or -999.0),
        ),
        reverse=True,
    ))


def _pairwise_agreement(reference: tuple[str, ...], other: tuple[str, ...]) -> float:
    if len(reference) < 2 or set(reference) != set(other):
        return 0.0
    ref_pos = {name: index for index, name in enumerate(reference)}
    other_pos = {name: index for index, name in enumerate(other)}
    pairs = 0
    agree = 0
    for index, left in enumerate(reference):
        for right in reference[index + 1:]:
            pairs += 1
            agree += int((ref_pos[left] - ref_pos[right]) *
                         (other_pos[left] - other_pos[right]) > 0)
    return agree / max(pairs, 1)


def seed_robustness(
    inputs, *, run_once: Callable, choose: Callable,
    seeds: Iterable[int] = DEFAULT_SEEDS, n_paths: int = 1200,
    n_steps: int = 180,
) -> dict:
    """Repeat the identical model under deterministic seeds."""
    rows = []
    rankings = []
    for seed in tuple(int(value) for value in seeds):
        metrics, _simulation = run_once(
            inputs, n_paths=n_paths, n_steps=n_steps, seed=seed)
        winner, rule = choose(metrics, inputs.r0)
        rankings.append(_ranking(metrics))
        rows.append({
            "seed": seed, "winner": winner,
            "eligible": list(rule.get("eligible") or []),
            "metrics": {
                name: {
                    "expected_final_r": metric.get("expected_final_r"),
                    "cvar10_r": metric.get("cvar10_r"),
                    "p_final_loss": metric.get("p_final_loss"),
                }
                for name, metric in metrics.items()
            },
        })
    counts = Counter(row["winner"] for row in rows)
    total = len(rows)
    modal, modal_count = counts.most_common(1)[0] if counts else (None, 0)
    reference = rankings[0] if rankings else ()
    ranking_agreement = (
        sum(_pairwise_agreement(reference, ranking) for ranking in rankings)
        / max(len(rankings), 1)
    )
    expected_spread = {}
    cvar_spread = {}
    if rows:
        for name in rows[0]["metrics"]:
            expected = [float(row["metrics"][name]["expected_final_r"]) for row in rows]
            cvars = [float(row["metrics"][name]["cvar10_r"]) for row in rows]
            expected_spread[name] = round(max(expected) - min(expected), 6)
            cvar_spread[name] = round(max(cvars) - min(cvars), 6)
    share = modal_count / max(total, 1)
    return {
        "method": "deterministic_multi_seed_common_random_policy_comparison",
        "seeds": [row["seed"] for row in rows],
        "paths_per_seed": int(n_paths), "steps": int(n_steps),
        "winner_counts": dict(counts), "modal_winner": modal,
        "winner_stability": round(share, 4),
        "ranking_agreement": round(ranking_agreement, 4),
        "expected_r_seed_spread": expected_spread,
        "cvar10_seed_spread": cvar_spread,
        "decision_uncertain": bool(share < 0.80 or ranking_agreement < 0.80),
        "rows": rows,
        "authority": "numerical_diagnostic_only",
    }


def convergence_study(
    inputs, *, run_once: Callable, choose: Callable,
    path_counts: Iterable[int] = (1000, 3000, 6000, 12000, 24000),
    n_steps: int = 240, seed: int = 0xF0C0,
) -> dict:
    """Offline/research convergence ladder; not run on every live review."""
    rows = []
    for count in tuple(int(value) for value in path_counts):
        metrics, _simulation = run_once(
            inputs, n_paths=count, n_steps=n_steps, seed=seed)
        winner, _rule = choose(metrics, inputs.r0)
        rows.append({
            "scenario_count": count, "winner": winner,
            "policies": {
                name: {
                    "expected_final_r": metric.get("expected_final_r"),
                    "cvar10_r": metric.get("cvar10_r"),
                    "p_final_loss": metric.get("p_final_loss"),
                    "p_next_rung_before_stop": metric.get(
                        "p_next_rung_before_stop"),
                    "p_stop_before_next_rung": metric.get(
                        "p_stop_before_next_rung"),
                }
                for name, metric in metrics.items()
            },
        })
    final_winner = rows[-1]["winner"] if rows else None
    stable_from = None
    for index, row in enumerate(rows):
        if all(later["winner"] == final_winner for later in rows[index:]):
            stable_from = row["scenario_count"]
            break
    return {
        "method": "nested_path_count_fixed_seed",
        "seed": int(seed), "steps": int(n_steps), "rows": rows,
        "reference_winner": final_winner,
        "winner_stable_from_scenarios": stable_from,
        "live_runtime": False,
    }
