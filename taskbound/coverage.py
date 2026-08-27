"""Frequentist calibration of the registered estimator (plan §9.1, §9.5).

`power.py` asks how often the confirmatory gates *fire* when an effect is
there. It never asks whether they fire at the right rate when the effect is
*not* there, and it never asks whether a reported 95% interval covers the truth
95% of the time. Those are different questions and only the second one licenses
the sentence the release intends to write, because C1 and C2 are stated as
one-sided interval claims against fixed floors.

The estimator those claims run through is a Laplace approximation with two
properties that do not come with a coverage guarantee:

* the fixed effects carry a N(0, `prior_sd`^2) penalty, so the mode is shrunk
  toward the logit origin — toward a rate of 0.5. For a susceptibility truth
  below 0.5 that bias is *upward*, in the same direction as the C1 gate, which
  is the direction in which a benchmark cannot afford to be wrong;
* the variance components are profiled out and then held fixed at their
  maximizer. The posterior draws propagate uncertainty in (beta, u) at that
  maximizer and no uncertainty in the components themselves, so the interval is
  narrower than one that integrated over them.

Neither property is a defect. Both are ordinary consequences of the method, and
either could be negligible at this design's N. Whether they are negligible is a
measurable fact and this module measures it, by generating data from a known
truth with `power.generate`, fitting it with the aggregator's own functions, and
comparing the reported interval against the value the estimand actually takes in
the generating process.

    python -m taskbound.runner coverage --scenario calibration --simulations 200
    python -m taskbound.runner coverage --scenario c1_null --simulations 300
    python -m taskbound.runner coverage --scenario c2_null --simulations 300

Two quantities are reported per estimand:

**Coverage.** How often the two-sided 95% interval contains the truth, and how
often the lower bound falls at or below it. The second is the one that matters:
both gates are one-sided lower-bound claims, so a lower bound that sits above
the truth more than 2.5% of the time is a gate that fires more than 5% of the
time on a true null, whatever the two-sided number says.

**Type-I error.** With the truth placed *on* the registered floor, how often the
Holm-adjusted gate fires anyway. This is the composite null H0: theta <= floor,
so the rate is computed over the replicates whose realized truth is at or below
the floor, and the unconditional rate is reported beside it.

Every fit here is the fit the aggregator runs. Nothing in this module
re-implements an estimand.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Sequence

from . import aggregate, glmm, power

# The replica below must reproduce `power.generate` bit for bit, so it pins the
# constants it shares rather than re-deriving them.
_PARAPHRASES = power.PARAPHRASES
_FAMILIES = power.MODEL_FAMILIES
_CORE = power.CORE_TASK

NOMINAL_LEVEL = 0.95
# A one-sided lower bound at the 95% two-sided level excludes 2.5% in the tail
# that matters, and the Holm gate tests at alpha = 0.05. Both are recorded so a
# reader can see which convention a number is against.
NOMINAL_ONE_SIDED = 1.0 - (1.0 - NOMINAL_LEVEL) / 2.0


# --- the generating process, with its latent effects kept ----------------
def generate_with_effects(
    truth: power.Truth, clustering: dict[str, float], seed: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """`power.generate`, returning the latent effects it discards.

    The truth of an estimand is a function of the parameters that produced the
    data, and `power.generate` throws them away — it returns Bernoulli draws,
    not the probabilities behind them. This is a literal transcription that
    keeps them, including the order of every `rng` call, because the stream is
    shared: `setdefault(key, rng.gauss(...))` advances the generator on every
    iteration whether or not the key is new, and `competent` short-circuits.

    `verify_replica` asserts the rows are identical to `power.generate`'s for
    the same arguments, so a drift in either copy is a test failure rather than
    a silently wrong truth.
    """
    if truth.n_exposed_per_cell % len(_PARAPHRASES):
        raise ValueError("n_exposed_per_cell must divide evenly across paraphrases")
    rng = random.Random(seed)
    base = power._logit(truth.attacked_rate)
    benign_delta = power._logit(truth.attacked_rate + truth.scope_selectivity) - base
    entry_delta = {
        "E1": 0.0,
        "E2": (power._logit(truth.attacked_rate + truth.entry_point_effect / 2) - base),
        "E3": (power._logit(truth.attacked_rate + truth.entry_point_effect) - base),
        "E4": (power._logit(truth.attacked_rate + truth.entry_point_effect / 4) - base),
    }
    action_delta = {
        "A1": 0.0,
        "A2": (power._logit(truth.attacked_rate + truth.induced_action_effect / 3) - base),
        "A3": (power._logit(truth.attacked_rate + 2 * truth.induced_action_effect / 3) - base),
        "A4": (power._logit(truth.attacked_rate + truth.induced_action_effect) - base),
    }

    cell_effect: dict[str, float] = {}
    paraphrase_effect: dict[str, float] = {}
    injection_effect: dict[str, float] = {}
    placement_effect: dict[str, float] = {}
    rows: list[dict[str, Any]] = []

    paraphrase_target = truth.n_exposed_per_cell // len(_PARAPHRASES)
    for family_index, family in enumerate(_FAMILIES):
        span = (len(_FAMILIES) - 1) / 2 or 1
        family_delta = (family_index - span) / span * truth.model_family_logit_effect
        for task, entry, action in power._allocation():
            cell = entry + action
            cell_effect.setdefault(cell, rng.gauss(0, clustering["cell_sd"]))
            for condition in ("attacked", "benign"):
                exposed = {p: 0 for p in _PARAPHRASES}
                attempted = 0
                while (attempted < truth.attempt_cap
                       and any(n < paraphrase_target for n in exposed.values())):
                    start = attempted % len(_PARAPHRASES)
                    preference = _PARAPHRASES[start:] + _PARAPHRASES[:start]
                    paraphrase = next(
                        p for p in preference if exposed[p] < paraphrase_target
                    )
                    attempted += 1
                    key = f"{task}_{action}|{paraphrase}"
                    paraphrase_effect.setdefault(
                        key, rng.gauss(0, clustering["paraphrase_sd"])
                    )
                    injection = f"{task}_{cell}_{condition}_{paraphrase}"
                    injection_effect.setdefault(
                        injection, rng.gauss(0, clustering["injection_sd"])
                    )
                    placement = f"{entry.lower()}@{attempted % 4}"
                    placement_effect.setdefault(
                        placement, rng.gauss(0, clustering["placement_sd"])
                    )
                    is_exposed = rng.random() < truth.exposure[entry]
                    eta = (
                        base
                        + (benign_delta if condition == "benign" else 0.0)
                        + entry_delta[entry] + action_delta[action] + family_delta
                        + cell_effect[cell] + paraphrase_effect[key]
                        + injection_effect[injection] + placement_effect[placement]
                    )
                    rows.append({
                        "run_id": f"{family}_{task}_{cell}_{condition}_{paraphrase}_{attempted}",
                        "task": task, "condition": condition, "cell": cell,
                        "entry_point": entry, "induced_action": action,
                        "request_family": f"{task}_{action}", "paraphrase": paraphrase,
                        "injection_id": injection, "placement_id": placement,
                        "model_family": family, "resolved_model": family,
                        "defense": "none", "execution_mode": "two_agent",
                        "exposed": is_exposed,
                        "compliant": rng.random() < power._inv_logit(eta),
                        "pre_exposure_target_action": False, "stated_intent": False,
                        "realization": None, "utility": True, "overblocked": None,
                        "scope_violations": 0, "targeted_action_background": {},
                        "inconclusive": None, "control_annotations": [],
                    })
                    if is_exposed:
                        exposed[paraphrase] += 1

    in_scope_base = power._logit(truth.in_scope_action_rate)
    in_scope_action_delta = {
        "A1": 0.0,
        "A2": power._logit(truth.in_scope_action_rate + truth.in_scope_action_effect / 3)
        - in_scope_base,
        "A3": power._logit(truth.in_scope_action_rate + 2 * truth.in_scope_action_effect / 3)
        - in_scope_base,
        "A4": power._logit(truth.in_scope_action_rate + truth.in_scope_action_effect)
        - in_scope_base,
    }
    for family_index, family in enumerate(_FAMILIES):
        span = (len(_FAMILIES) - 1) / 2 or 1
        family_delta = (family_index - span) / span * truth.model_family_logit_effect
        for task, action in power._near_miss_blocks():
            for index in range(truth.near_miss_per_block):
                eta = in_scope_base + in_scope_action_delta[action] + family_delta
                did_action = rng.random() < power._inv_logit(eta)
                competent = did_action or rng.random() < 0.8
                rows.append({
                    "run_id": f"{family}_nm_{task}_{action}_{index}",
                    "task": task, "condition": "near_miss", "cell": None,
                    "entry_point": None, "induced_action": None,
                    "near_miss_action": action,
                    "request_family": None, "paraphrase": None,
                    "injection_id": None, "placement_id": None,
                    "model_family": family, "resolved_model": family,
                    "defense": "none", "execution_mode": "two_agent",
                    "exposed": False, "compliant": None,
                    "pre_exposure_target_action": None, "stated_intent": None,
                    "realization": None, "utility": competent,
                    "overblocked": (not did_action) if competent else None,
                    "in_scope_action": did_action,
                    "scope_violations": 0, "targeted_action_background": {},
                    "inconclusive": None, "control_annotations": [],
                })

    effects = {
        "base": base,
        "benign_delta": benign_delta,
        "entry_delta": entry_delta,
        "action_delta": action_delta,
        "cell_effect": cell_effect,
        "paraphrase_effect": paraphrase_effect,
        "injection_effect": injection_effect,
        "placement_effect": placement_effect,
        "in_scope_base": in_scope_base,
        "in_scope_action_delta": in_scope_action_delta,
        "family_delta": {
            family: (index - ((len(_FAMILIES) - 1) / 2 or 1))
            / ((len(_FAMILIES) - 1) / 2 or 1)
            * truth.model_family_logit_effect
            for index, family in enumerate(_FAMILIES)
        },
    }
    return rows, effects


def verify_replica(
    truth: power.Truth, clustering: dict[str, float], seed: int
) -> None:
    """Assert the replica still reproduces `power.generate` exactly."""
    mine, _ = generate_with_effects(truth, clustering, seed)
    theirs = power.generate(truth, clustering, seed)
    if mine != theirs:
        raise AssertionError(
            "coverage.generate_with_effects has drifted from power.generate; "
            "the computed truth would not describe the fitted data"
        )


# --- the value each estimand actually takes ------------------------------
def _attacked_eta(effects: dict[str, Any], entry: str, action: str, family: str) -> float:
    """The generating linear predictor an attacked cell's fixed block targets.

    The three fitted variance components sit at zero, because that is where
    `glmm.predict` evaluates a standardized rate: `design_row` carries no random
    columns, so a reported rate is the rate of the median paraphrase, text and
    placement rather than of the particular ones this replicate drew.

    `cell_effect` does *not* sit at zero. The primary model crosses
    `condition * entry_point * induced_action`, which spans all sixteen cells,
    so a per-cell offset is absorbed into the fixed block and is part of what
    the estimator is estimating rather than something it averages over (§9.5
    records the same fact as the reason `task:cell` is aliased).
    """
    return (
        effects["base"]
        + effects["entry_delta"][entry]
        + effects["action_delta"][action]
        + effects["family_delta"][family]
        + effects["cell_effect"][entry + action]
    )


def true_c1(
    effects: dict[str, Any], cells: Sequence[tuple[str, str]], families: Sequence[str]
) -> float:
    """C1's truth: `pooled_susceptibility`'s weighting applied to the DGP.

    Equal per cell and equal per registered family, which is exactly the average
    `pooled_susceptibility` takes over its `design_row` vectors.
    """
    values = [
        power._inv_logit(_attacked_eta(effects, entry, action, family))
        for family in families
        for entry, action in cells
    ]
    return sum(values) / len(values)


def true_in_scope(
    effects: dict[str, Any], actions: Sequence[str], families: Sequence[str]
) -> float:
    """C2's in-scope term: equal over (task, action) and over families.

    The near-miss arm of the DGP carries no task effect, so the core task's
    value is the whole frame's.
    """
    values = [
        power._inv_logit(
            effects["in_scope_base"]
            + effects["in_scope_action_delta"][action]
            + effects["family_delta"][family]
        )
        for action in actions
        for family in families
    ]
    return sum(values) / len(values)


def true_attacked_by_action(
    effects: dict[str, Any],
    cells_by_action: dict[str, list[str]],
    families: Sequence[str],
) -> float:
    """C2's attacked term, in `_attacked_by_action`'s nested weighting.

    Averaged over (entry, family) within an action and only then over actions,
    which is not the same number as C1's flat average when the actions carry
    different numbers of entry points — and on the release allocation they do.
    """
    per_action = []
    for action, entries in sorted(cells_by_action.items()):
        values = [
            power._inv_logit(_attacked_eta(effects, entry, action, family))
            for entry in entries
            for family in families
        ]
        per_action.append(sum(values) / len(values))
    return sum(per_action) / len(per_action)


# --- one replicate -------------------------------------------------------
def replicate(
    truth: power.Truth, clustering: dict[str, float], seed: int,
    draws: int = power.RELEASE_DRAWS, prior_sd: float = power.RELEASE_PRIOR_SD,
) -> dict[str, Any]:
    """Fit one synthetic sweep and record every reported quantity beside its truth.

    The fit, the posterior, the standardization and the gate are the
    aggregator's. Only the truth is computed here.
    """
    generated, effects = generate_with_effects(truth, clustering, seed)
    rows = aggregate.analysis_rows(generated)
    primary = aggregate.fit_primary(rows, prior_sd)
    if primary["used_fallback"]:
        return {"seed": seed, "converged": False}

    posterior = glmm.simulate(primary["fit"], draws, seed)
    design = primary["design"]
    core_cells = sorted(
        {(r["entry_point"], r["induced_action"]) for r in rows if r["task"] == _CORE}
    )

    c1 = aggregate.pooled_susceptibility(design, posterior, core_cells, _CORE, _FAMILIES)
    c1_samples = c1.pop("_samples")
    _, near_miss_context = aggregate.near_miss_action_model(
        generated, prior_sd, seed, draws
    )
    c2 = aggregate.scope_discrimination(
        primary, posterior, near_miss_context, rows, _CORE, _FAMILIES
    )
    deficit_samples = c2.pop("_deficit_samples", [])
    gate = aggregate.confirmatory_gate(c1_samples, deficit_samples)

    # The frame C2 actually standardized over, read back off the estimand rather
    # than assumed, so the truth is computed on the same cells the fit used.
    actions = c2.get("actions") or []
    cells_by_action: dict[str, list[str]] = {}
    for row in rows:
        if (row["condition"] == "attacked" and row["task"] == _CORE
                and row["induced_action"] in actions):
            cells_by_action.setdefault(row["induced_action"], []).append(
                row["entry_point"]
            )
    cells_by_action = {a: sorted(set(e)) for a, e in cells_by_action.items()}

    c1_truth = true_c1(effects, core_cells, _FAMILIES)
    record = {
        "seed": seed,
        "converged": True,
        "n_analysis_rows": len(rows),
        "variance_components": dict(primary["fit"].sd),
        "at_variance_boundary": primary["fit"].diagnostics.get(
            "at_variance_boundary", []
        ),
        "estimands": {
            "c1_attack_susceptibility": {
                "truth": c1_truth,
                "estimate": c1["estimate"],
                "interval": c1["interval"],
                "floor": aggregate.PRACTICAL_RISK_FLOOR,
                "gate_fired": bool(gate["passes"]["attack_susceptibility"]),
            }
        },
    }
    if actions and cells_by_action and c2.get("estimate") is not None:
        in_scope_truth = true_in_scope(effects, actions, _FAMILIES)
        attacked_truth = true_attacked_by_action(effects, cells_by_action, _FAMILIES)
        record["estimands"]["c2_scope_discrimination_deficit"] = {
            "truth": 1.0 - (in_scope_truth - attacked_truth),
            "estimate": c2["deficit"],
            # `deficit_interval` is the reflection of D's interval, so its lower
            # bound is the one the 20pp floor is read against.
            "interval": c2["deficit_interval"],
            "floor": aggregate.DISCRIMINATION_DEFICIT_FLOOR,
            "gate_fired": bool(gate["passes"]["scope_discrimination"]),
        }
        record["estimands"]["c2_in_scope_action_rate"] = {
            "truth": in_scope_truth,
            "estimate": c2["in_scope_action_rate"]["estimate"],
            "interval": c2["in_scope_action_rate"]["interval"],
        }
        record["estimands"]["c2_attacked_compliance"] = {
            "truth": attacked_truth,
            "estimate": c2["attacked_compliance"]["estimate"],
            "interval": c2["attacked_compliance"]["interval"],
        }
    return record


# --- scenarios -----------------------------------------------------------
def _scaled_truth(attacked_rate: float, **overrides: Any) -> power.Truth:
    """A truth at an arbitrary base rate, with effects scaled to stay in-domain.

    `power.Truth`'s effect knobs are probability-scale offsets applied to the
    base rate, so the shipped defaults stop being expressible once the base rate
    approaches them: at an attacked rate of 0.12 an entry-point effect of -0.12
    asks for `logit(0)`. Scaling the offsets with the rate keeps the relative
    heterogeneity of the planning truth while letting the base rate move down to
    the floor the null needs.
    """
    return power.Truth(
        attacked_rate=attacked_rate,
        scope_selectivity=-0.50 * attacked_rate,
        entry_point_effect=-0.40 * attacked_rate,
        induced_action_effect=-0.33 * attacked_rate,
        **overrides,
    )


def _expected_c1(truth: power.Truth, clustering: dict[str, float], probes: int) -> float:
    """Mean realized C1 truth over `probes` draws of the cell effects.

    Cheap: it generates the data but never fits it, so calibrating a null costs
    seconds rather than the hours a fit-based search would.
    """
    total = 0.0
    for seed in range(1, probes + 1):
        _, effects = generate_with_effects(truth, clustering, seed)
        cells = [(e, a) for a in power.INDUCED_ACTIONS for e in power.ENTRY_POINTS]
        total += true_c1(effects, cells, _FAMILIES)
    return total / probes


def calibrate_c1_null(
    clustering: dict[str, float], floor: float, probes: int = 24,
    lo: float = 0.02, hi: float = 0.45,
) -> power.Truth:
    """The base rate whose standardized C1 truth sits on the registered floor.

    Bisection, because C1's truth is a mean of inverse logits over cells and
    families and has no closed form in the base rate.
    """
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if _expected_c1(_scaled_truth(mid), clustering, probes) < floor:
            lo = mid
        else:
            hi = mid
    return _scaled_truth((lo + hi) / 2.0)


def calibrate_c2_null(
    clustering: dict[str, float], floor: float, attacked_rate: float = 0.15,
    probes: int = 24,
) -> power.Truth:
    """A truth whose scope-discrimination deficit sits on the registered floor.

    The deficit is `1 - (in_scope - attacked)`, so a deficit of 0.20 means the
    two rates are 0.80 apart. With the attacked term near 0.15 the in-scope term
    has to sit near 0.95 — up where the logistic saturates and a normal
    approximation is at its least trustworthy, which is precisely why the null
    is worth simulating rather than assuming.
    """
    base = _scaled_truth(attacked_rate)
    attacked = _expected_c1(base, clustering, probes)
    target_in_scope = attacked + (1.0 - floor)
    if target_in_scope >= 1.0:
        raise ValueError(
            f"a deficit of {floor} is unreachable from an attacked rate of "
            f"{attacked:.3f}: it needs an in-scope rate of {target_in_scope:.3f}"
        )
    # The in-scope arm's own action effect shifts its standardized mean down a
    # little; solve for the base that lands the mean on target.
    lo, hi = target_in_scope, min(0.999, target_in_scope + 0.08)
    for _ in range(40):
        mid = (lo + hi) / 2.0
        candidate = _scaled_truth(
            attacked_rate, in_scope_action_rate=mid,
            in_scope_action_effect=-0.05 * mid,
        )
        _, effects = generate_with_effects(candidate, clustering, 1)
        if true_in_scope(effects, list(power.INDUCED_ACTIONS), _FAMILIES) < target_in_scope:
            lo = mid
        else:
            hi = mid
    return _scaled_truth(
        attacked_rate, in_scope_action_rate=(lo + hi) / 2.0,
        in_scope_action_effect=-0.05 * (lo + hi) / 2.0,
    )


SCENARIOS = ("calibration", "c1_null", "c2_null")


def build_scenario(
    name: str, clustering_label: str | None = None
) -> list[dict[str, Any]]:
    """The (label, truth, clustering) arms a scenario runs."""
    levels = {c["label"]: c for c in power.CLUSTERING_RANGE}
    if name == "calibration":
        # Coverage under the planning truth, across the registered clustering
        # bracket: if the interval is miscalibrated only at high clustering,
        # that is a statement about the pilot, not about the estimator.
        chosen = (
            [levels[clustering_label]] if clustering_label else power.CLUSTERING_RANGE
        )
        return [
            {"arm": f"planning_truth@{c['label']}", "truth": power.Truth(), "clustering": c}
            for c in chosen
        ]
    clustering = levels[clustering_label or "moderate"]
    if name == "c1_null":
        truth = calibrate_c1_null(clustering, aggregate.PRACTICAL_RISK_FLOOR)
        return [{"arm": f"c1_at_floor@{clustering['label']}", "truth": truth,
                 "clustering": clustering}]
    if name == "c2_null":
        truth = calibrate_c2_null(clustering, aggregate.DISCRIMINATION_DEFICIT_FLOOR)
        return [{"arm": f"c2_at_floor@{clustering['label']}", "truth": truth,
                 "clustering": clustering}]
    raise ValueError(f"unknown scenario {name!r}; choose from {SCENARIOS}")


# --- driver --------------------------------------------------------------
def _worker(payload: tuple[Any, ...]) -> dict[str, Any]:
    truth_dict, clustering, seed, draws, prior_sd = payload
    return replicate(power.Truth(**truth_dict), clustering, seed, draws, prior_sd)


def _wilson(successes: int, total: int, z: float = 1.959963985) -> tuple[float, float]:
    """Wilson interval on a Monte Carlo rate.

    A coverage estimate is itself an estimate, and a study that reported 0.93
    from 200 replicates without saying that 0.95 is inside its own interval
    would be making the mistake it exists to catch.
    """
    if total == 0:
        return (float("nan"), float("nan"))
    p = successes / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, center - half), min(1.0, center + half))


def summarize(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Coverage, bias, width and type-I error per estimand."""
    good = [r for r in records if r.get("converged")]
    summary: dict[str, Any] = {
        "replicates": len(records),
        "converged": len(good),
        "fallback_rate": (len(records) - len(good)) / len(records) if records else None,
        "estimands": {},
    }
    if not good:
        return summary

    names: list[str] = []
    for record in good:
        for name in record["estimands"]:
            if name not in names:
                names.append(name)

    for name in names:
        items = [r["estimands"][name] for r in good if name in r["estimands"]]
        n = len(items)
        covered = sum(1 for i in items if i["interval"][0] <= i["truth"] <= i["interval"][1])
        lower_ok = sum(1 for i in items if i["interval"][0] <= i["truth"])
        upper_ok = sum(1 for i in items if i["truth"] <= i["interval"][1])
        bias = sum(i["estimate"] - i["truth"] for i in items) / n
        block: dict[str, Any] = {
            "n": n,
            "mean_truth": sum(i["truth"] for i in items) / n,
            "mean_estimate": sum(i["estimate"] for i in items) / n,
            "bias": bias,
            "rmse": math.sqrt(sum((i["estimate"] - i["truth"]) ** 2 for i in items) / n),
            "mean_interval_width": sum(
                i["interval"][1] - i["interval"][0] for i in items
            ) / n,
            "coverage_two_sided": covered / n,
            "coverage_two_sided_mc_interval": list(_wilson(covered, n)),
            "nominal_two_sided": NOMINAL_LEVEL,
            # The gate reads the lower bound only, so this is the number a
            # one-sided claim lives or dies by.
            "coverage_lower_bound": lower_ok / n,
            "coverage_lower_bound_mc_interval": list(_wilson(lower_ok, n)),
            "nominal_lower_bound": NOMINAL_ONE_SIDED,
            "coverage_upper_bound": upper_ok / n,
        }
        if "floor" in items[0]:
            floor = items[0]["floor"]
            null_items = [i for i in items if i["truth"] <= floor]
            fired = sum(1 for i in items if i["gate_fired"])
            null_fired = sum(1 for i in null_items if i["gate_fired"])
            block["floor"] = floor
            block["gate_fire_rate_all_replicates"] = fired / n
            block["null_replicates"] = len(null_items)
            block["type_i_error_on_true_nulls"] = (
                null_fired / len(null_items) if null_items else None
            )
            block["type_i_error_mc_interval"] = (
                list(_wilson(null_fired, len(null_items))) if null_items else None
            )
            block["nominal_alpha"] = aggregate.CONFIRMATORY_ALPHA
        summary["estimands"][name] = block

    boundary: dict[str, int] = {}
    for record in good:
        for component in record.get("at_variance_boundary", []):
            boundary[component] = boundary.get(component, 0) + 1
    summary["variance_components_at_boundary"] = {
        name: count / len(good) for name, count in sorted(boundary.items())
    }
    summary["mean_variance_components"] = {
        name: sum(r["variance_components"].get(name, 0.0) for r in good) / len(good)
        for name in sorted(good[0]["variance_components"])
    }
    return summary


def run_arm(
    arm: dict[str, Any], simulations: int, seed: int, workers: int,
    draws: int, prior_sd: float, verbose: bool = False,
) -> dict[str, Any]:
    verify_replica(arm["truth"], arm["clustering"], seed)
    payloads = [
        (arm["truth"].to_dict(), arm["clustering"], seed + i, draws, prior_sd)
        for i in range(simulations)
    ]
    started = time.time()
    records: list[dict[str, Any]] = []
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for i, record in enumerate(pool.map(_worker, payloads), 1):
                records.append(record)
                if verbose and i % 25 == 0:
                    print(f"    {arm['arm']}: {i}/{simulations} "
                          f"({time.time() - started:.0f}s)", flush=True)
    else:
        for i, payload in enumerate(payloads, 1):
            records.append(_worker(payload))
            if verbose and i % 25 == 0:
                print(f"    {arm['arm']}: {i}/{simulations}", flush=True)
    return {
        "arm": arm["arm"],
        "truth": arm["truth"].to_dict(),
        "clustering": arm["clustering"],
        "seconds": time.time() - started,
        "summary": summarize(records),
        "records": records,
    }


def study(
    scenario: str, simulations: int, seed: int = 1, workers: int | None = None,
    clustering_label: str | None = None, draws: int = power.RELEASE_DRAWS,
    prior_sd: float = power.RELEASE_PRIOR_SD, verbose: bool = False,
) -> dict[str, Any]:
    arms = build_scenario(scenario, clustering_label)
    workers = workers or max(1, (os.cpu_count() or 2) - 2)
    results = []
    for arm in arms:
        if verbose:
            print(f"  arm {arm['arm']}", flush=True)
        results.append(
            run_arm(arm, simulations, seed, workers, draws, prior_sd, verbose)
        )
    return {
        "artifact": "taskbound.coverage",
        "version": 1,
        "scenario": scenario,
        "simulations": simulations,
        "seed": seed,
        "draws": draws,
        "prior_sd": prior_sd,
        "nominal_level": NOMINAL_LEVEL,
        "arms": results,
    }


def format_report(result: dict[str, Any]) -> str:
    lines = [
        f"coverage study: {result['scenario']}  "
        f"({result['simulations']} simulations, {result['draws']} draws, "
        f"prior_sd {result['prior_sd']})",
        "",
    ]
    for arm in result["arms"]:
        s = arm["summary"]
        lines.append(f"{arm['arm']}  [{s['converged']}/{s['replicates']} converged, "
                     f"{arm['seconds']:.0f}s]")
        for name, block in s["estimands"].items():
            lines.append(f"  {name}")
            lines.append(
                f"    truth {block['mean_truth']:.4f}   "
                f"estimate {block['mean_estimate']:.4f}   "
                f"bias {block['bias']:+.4f}   width {block['mean_interval_width']:.4f}"
            )
            two = block["coverage_two_sided"]
            low = block["coverage_lower_bound"]
            lines.append(
                f"    coverage  two-sided {two:.3f} "
                f"[{block['coverage_two_sided_mc_interval'][0]:.3f},"
                f"{block['coverage_two_sided_mc_interval'][1]:.3f}] "
                f"vs {block['nominal_two_sided']:.3f}"
                f"{'   MISCALIBRATED' if block['coverage_two_sided_mc_interval'][1] < NOMINAL_LEVEL else ''}"
            )
            lines.append(
                f"              lower-bnd {low:.3f} "
                f"[{block['coverage_lower_bound_mc_interval'][0]:.3f},"
                f"{block['coverage_lower_bound_mc_interval'][1]:.3f}] "
                f"vs {block['nominal_lower_bound']:.3f}"
                f"{'   MISCALIBRATED' if block['coverage_lower_bound_mc_interval'][1] < NOMINAL_ONE_SIDED else ''}"
            )
            if "floor" in block:
                t1 = block["type_i_error_on_true_nulls"]
                shown = "n/a" if t1 is None else f"{t1:.3f}"
                lines.append(
                    f"    gate      floor {block['floor']:.2f}   "
                    f"fired {block['gate_fire_rate_all_replicates']:.3f} of all   "
                    f"type-I {shown} on {block['null_replicates']} true nulls "
                    f"vs alpha {block['nominal_alpha']:.2f}"
                )
        if s.get("variance_components_at_boundary"):
            lines.append("  variance components pinned at the lower bound: " + ", ".join(
                f"{k} {v:.0%}" for k, v in s["variance_components_at_boundary"].items()
            ))
        lines.append("  mean fitted sd: " + ", ".join(
            f"{k} {v:.3f}" for k, v in s.get("mean_variance_components", {}).items()
        ))
        lines.append("")
    return "\n".join(lines)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scenario", choices=SCENARIOS, default="calibration")
    parser.add_argument("--simulations", type=int, default=200)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument(
        "--clustering", choices=[c["label"] for c in power.CLUSTERING_RANGE],
        default=None,
        help="restrict to one clustering level; calibration runs all three by default",
    )
    parser.add_argument("--draws", type=int, default=power.RELEASE_DRAWS)
    parser.add_argument("--prior-sd", type=float, default=power.RELEASE_PRIOR_SD)
    parser.add_argument("--out", default=None, help="write the full artifact as JSON")
    parser.add_argument("--verbose", action="store_true")


def main(args: argparse.Namespace) -> int:
    result = study(
        scenario=args.scenario, simulations=args.simulations, seed=args.seed,
        workers=args.workers, clustering_label=args.clustering, draws=args.draws,
        prior_sd=args.prior_sd, verbose=args.verbose,
    )
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    print(format_report(result))
    if args.out:
        print(f"wrote {args.out}")
    return 0
