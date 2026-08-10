"""Power simulation under the exact allocation and analysis model (plan §9.5).

"Before the main pre-registration is signed, a simulation using the exact
allocation and analysis model must name the minimum effect of interest for
attack susceptibility, scope selectivity, and both main effects, and
demonstrate at least 80% power across the pilot-informed conservative
clustering range."

That is a gate, not a table of planning ranges, so it is code. The simulation
generates datasets from a named truth, fits them with the *same* function the
aggregator uses, and counts how often the pre-registered interval excludes the
null. Clustering is the parameter that matters most and is the one the pilot
supplies: a design that has power at a paraphrase sd of 0.3 and none at 0.8 is
a design whose power claim depends on a number nobody has measured yet, which
is why the gate is "across the range" rather than "at our best guess".

    python -m taskbound.runner clustering --results pilot/sizing --out pilot/clustering.json
    python -m taskbound.runner power --simulations 200 --clustering pilot/clustering.json

Both commands live here, because the range and the gate evaluated across it are
one argument. `clustering` may refuse to narrow — see `measure_clustering` — and
`power` records which range it used under `clustering_provenance`, so a pass at
measured clustering is never mistaken for a pass at an assumed one.

Every simulation is a full mixed-effects fit, so this is minutes-to-hours work
rather than seconds. It runs once, before signing.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass, field
from typing import Any, Sequence

from . import aggregate, glmm

ENTRY_POINTS = ("E1", "E2", "E3")
INDUCED_ACTIONS = ("A1", "A2", "A3", "A4")
PARAPHRASES = ("p1", "p2", "p3")

# The conservative range the gate is evaluated across. The pilot replaces these
# with measured values; until then they bracket "wording barely matters" and
# "wording matters as much as the design does".
CLUSTERING_RANGE = [
    {"label": "low", "paraphrase_sd": 0.2, "cell_sd": 0.3, "injection_sd": 0.1, "placement_sd": 0.1},
    {"label": "moderate", "paraphrase_sd": 0.5, "cell_sd": 0.5, "injection_sd": 0.2, "placement_sd": 0.15},
    {"label": "high", "paraphrase_sd": 0.9, "cell_sd": 0.6, "injection_sd": 0.35, "placement_sd": 0.25},
]
REQUIRED_POWER = 0.80

# The fitted random effects that correspond to the simulation's clustering
# knobs. Three of the four map; `cell_sd` no longer does, because `host:cell`
# was dropped from the primary model as aliased with the fixed block (§9.5).
COMPONENT_TO_KNOB = {
    "request_family:paraphrase": "paraphrase_sd",
    "injection_id": "injection_sd",
    "placement_id": "placement_sd",
}
KNOBS = ("paraphrase_sd", "cell_sd", "injection_sd", "placement_sd")

# `generate` still draws a per-cell effect, because between-cell heterogeneity is
# real in the data-generating process even though the fitted model absorbs it
# into fixed effects. It is therefore simulated but no longer measurable, and a
# pilot cannot narrow it: the a-priori bracket is carried through for this knob
# while the other three narrow to what was measured. Reporting a measured
# `cell_sd` would be reporting a number no fit produced.
UNMEASURABLE_KNOBS = ("cell_sd",)

# A standard deviation this large on the logit scale is not a measurement, it is
# a flat likelihood: the profiled surface has no curvature in that direction and
# the pilot has not constrained the component. Draws are clamped here so the
# arithmetic stays finite, and any component whose interval reaches the ceiling
# is treated as unresolved.
SD_CEILING = 5.0


def measure_clustering(
    rows: Sequence[dict[str, Any]], prior_sd: float, seed: int, level: float = 0.95
) -> dict[str, Any]:
    """Turn a sizing pilot into the clustering range the gate is evaluated across.

    `pilot_protocol.md` Stage 2 says the measured variance components replace
    `CLUSTERING_RANGE`. Doing that by hand-editing a literal in this file, after
    the pilot's numbers are visible, is the one step of the gate that would
    leave no record of what was measured versus what was typed. So it is code,
    and it writes its own provenance.

    The result is still a *range*, not a point: a sizing pilot sees few levels
    of each grouping factor, so the components carry real uncertainty and the
    gate must hold at the pessimistic end of what the pilot supports. The rungs
    are the interval's ends and its centre.

    When the profiled surface has no usable curvature — components pinned at
    their lower boundary, or a non-positive-definite Hessian — no interval can
    be drawn, and the function **refuses to narrow the range**. It returns
    `CLUSTERING_RANGE` unchanged, rung for rung, and says why. A pilot that could
    not resolve the clustering must not be able to make the gate easier to pass.

    Every branch returns the same keys, `point_estimate` among them, so a caller
    need not know which one produced the result.
    """
    analysis = aggregate.analysis_rows(rows)
    primary = aggregate.fit_primary(analysis, prior_sd)
    fit = primary["fit"]

    source = {
        "runs": len(rows),
        "analysis_rows": len(analysis),
        "used_fallback": primary["used_fallback"],
        "converged": getattr(fit, "converged", False),
        "at_variance_boundary": (fit.diagnostics.get("at_variance_boundary") or []
                                 if not primary["used_fallback"] else None),
    }

    if primary["used_fallback"] or not getattr(fit, "log_sd", None):
        return _unnarrowed(source, "the fallback fit has no variance components", None)

    point = {knob: fit.sd.get(name, 0.0) for name, knob in COMPONENT_TO_KNOB.items()}
    unmapped = {n: v for n, v in fit.sd.items() if n not in COMPONENT_TO_KNOB}

    # A component pinned at its lower boundary is not a measurement of zero
    # clustering; it is the fit reporting that this pilot could not see the
    # component at all. Narrowing the gate onto a floor artifact would make it
    # easier to pass on the strength of a pilot that measured nothing.
    pinned = [COMPONENT_TO_KNOB[n] for n in (source["at_variance_boundary"] or [])
              if n in COMPONENT_TO_KNOB]
    if pinned:
        result = _unnarrowed(
            source,
            "these components sit at the fit's lower variance boundary: "
            + ", ".join(sorted(pinned))
            + " — the pilot did not resolve them, so their point estimates are "
              "floor artifacts rather than measurements", point)
        result["unmapped_components"] = unmapped
        return result

    drawn = aggregate.log_sd_samples(primary, prior_sd, seed)
    if drawn is None:
        result = _unnarrowed(
            source, "the profiled surface has no usable curvature, so no interval "
                    "can be drawn around the measured components", point)
        result["unmapped_components"] = unmapped
        return result

    names, draws = drawn
    ceiling = math.log(SD_CEILING)
    components: dict[str, Any] = {}
    unresolved: list[str] = []
    for name, knob in COMPONENT_TO_KNOB.items():
        if name not in names:
            components[knob] = {"estimate": 0.0, "interval": [0.0, 0.0],
                                "note": "not a factor in this fit"}
            continue
        index = names.index(name)
        values = [math.exp(min(d[index], ceiling)) for d in draws]
        low, high = glmm.interval(values, level)
        components[knob] = {"estimate": point[knob], "interval": [low, high]}
        # The clamp lands a hair under the ceiling in floating point, so compare
        # with a tolerance rather than exactly.
        if high >= SD_CEILING * (1 - 1e-9):
            components[knob]["unresolved"] = True
            unresolved.append(knob)

    if unresolved:
        # Narrowing on a component the pilot could not pin down would hand the
        # gate a friendlier range than the data support. Refuse the whole range
        # rather than the offending rung: the components are fitted jointly, so
        # a flat direction in one contaminates the others' intervals too.
        result = _unnarrowed(
            source,
            "the sizing pilot did not resolve " + ", ".join(unresolved)
            + f" (interval reaches the {SD_CEILING} ceiling, i.e. a flat likelihood); "
              "a larger pilot is needed before the range can narrow", point)
        result["components"] = components
        result["unmapped_components"] = unmapped
        return result

    # The unmeasurable knobs keep their a-priori values, rung for rung, so the
    # gate still spans the bracket nobody has narrowed instead of pretending a
    # measurement exists for them.
    for knob in UNMEASURABLE_KNOBS:
        components[knob] = {
            "estimate": None,
            "interval": [None, None],
            "measurable": False,
            "note": "no fitted component maps to this knob since host:cell was "
                    "dropped (§9.5); the a-priori range is carried through",
        }

    # The a-priori values for an unmeasurable knob, taken as the ends and middle
    # of whatever CLUSTERING_RANGE holds rather than by position, so this does
    # not silently mis-pair if the bracket is ever reordered or resized.
    def apriori(knob: str) -> tuple[float, float, float]:
        values = sorted(c[knob] for c in CLUSTERING_RANGE)
        return values[0], values[len(values) // 2], values[-1]

    def rung(label: str, which: int, pick) -> dict[str, Any]:
        values = {}
        for knob in KNOBS:
            if knob in UNMEASURABLE_KNOBS:
                values[knob] = apriori(knob)[which]
            else:
                values[knob] = pick(components[knob])
        return {"label": label, **values}

    return {
        "measured": True,
        "narrowed": True,
        "level": level,
        "point_estimate": point,
        "source": source,
        "components": components,
        "unmapped_components": unmapped,
        "unmeasurable_knobs": list(UNMEASURABLE_KNOBS),
        "range": [
            rung("measured_low", 0, lambda c: c["interval"][0]),
            rung("measured", 1, lambda c: c["estimate"]),
            rung("measured_high", 2, lambda c: c["interval"][1]),
        ],
    }


def _unnarrowed(source: dict[str, Any], reason: str,
                point: dict[str, float] | None = None) -> dict[str, Any]:
    """The refusal branch: keep the a-priori bracket and say so.

    Every refusal returns the same shape, `point_estimate` included, so a caller
    reading the result does not have to know which branch produced it. It is
    None only when the fit produced no components to report.
    """
    return {
        "measured": False,
        "narrowed": False,
        "reason": reason,
        "point_estimate": point,
        "source": source,
        "range": [dict(c) for c in CLUSTERING_RANGE],
        "note": "the a-priori CLUSTERING_RANGE is retained unchanged; the gate is "
                "no easier to pass than it was before the pilot ran",
    }


def load_clustering(path: str) -> list[dict[str, Any]]:
    """Read a measured range, rejecting one that does not carry every knob."""
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    entries = payload.get("range") if isinstance(payload, dict) else payload
    if not entries:
        raise SystemExit(f"{path!r} carries no clustering range")
    for entry in entries:
        missing = [k for k in KNOBS if k not in entry]
        if missing:
            raise SystemExit(f"{path!r}: rung {entry.get('label')!r} is missing {missing}")
    return list(entries)


@dataclass
class Truth:
    """The data-generating parameters a power claim is made against."""

    attacked_rate: float = 0.30          # attacked compliance at the reference cell
    scope_selectivity: float = -0.15     # benign minus attacked, on the probability scale
    entry_point_effect: float = -0.12    # E3 minus E1, on the probability scale
    induced_action_effect: float = -0.10  # A4 minus A1, on the probability scale
    exposure: dict[str, float] = field(
        default_factory=lambda: {"E1": 0.98, "E2": 0.55, "E3": 0.40}
    )
    n_exposed_per_cell: int = 48
    attempt_cap: int = 144

    def to_dict(self) -> dict[str, Any]:
        return {**vars(self)}


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _inv_logit(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def generate(truth: Truth, clustering: dict[str, float], seed: int) -> list[dict[str, Any]]:
    """One synthetic sweep under the exact v0.5 allocation.

    Recruitment is simulated too, not assumed away: a cell draws attempts until
    it has its exposed target or hits the cap, so an entry point the agent
    rarely opens contributes the smaller sample it would really contribute.
    """
    rng = random.Random(seed)
    base = _logit(truth.attacked_rate)
    benign_delta = _logit(truth.attacked_rate + truth.scope_selectivity) - base
    entry_delta = {
        "E1": 0.0,
        "E2": (_logit(truth.attacked_rate + truth.entry_point_effect / 2) - base),
        "E3": (_logit(truth.attacked_rate + truth.entry_point_effect) - base),
    }
    action_delta = {
        "A1": 0.0,
        "A2": (_logit(truth.attacked_rate + truth.induced_action_effect / 3) - base),
        "A3": (_logit(truth.attacked_rate + 2 * truth.induced_action_effect / 3) - base),
        "A4": (_logit(truth.attacked_rate + truth.induced_action_effect) - base),
    }

    cell_effect: dict[str, float] = {}
    paraphrase_effect: dict[str, float] = {}
    injection_effect: dict[str, float] = {}
    placement_effect: dict[str, float] = {}
    rows: list[dict[str, Any]] = []

    for action in INDUCED_ACTIONS:
        for entry in ENTRY_POINTS:
            cell = entry + action
            cell_effect.setdefault(cell, rng.gauss(0, clustering["cell_sd"]))
            for condition in ("attacked", "benign"):
                exposed = attempted = 0
                while exposed < truth.n_exposed_per_cell and attempted < truth.attempt_cap:
                    for paraphrase in PARAPHRASES:  # blocks of three (plan §8.4)
                        attempted += 1
                        key = f"t1_{action}|{paraphrase}"
                        paraphrase_effect.setdefault(key, rng.gauss(0, clustering["paraphrase_sd"]))
                        injection = f"{cell}_{condition}_{paraphrase}"
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
                            + entry_delta[entry] + action_delta[action]
                            + cell_effect[cell] + paraphrase_effect[key]
                            + injection_effect[injection] + placement_effect[placement]
                        )
                        rows.append({
                            "run_id": f"{cell}_{condition}_{paraphrase}_{attempted}",
                            "task": "t1", "condition": condition, "cell": cell,
                            "entry_point": entry, "induced_action": action,
                            "request_family": f"t1_{action}", "paraphrase": paraphrase,
                            "injection_id": injection, "placement_id": placement,
                            "model_family": "family_x", "resolved_model": "family_x",
                            "defense": "none", "execution_mode": "single_agent",
                            "exposed": is_exposed,
                            "compliant": rng.random() < _inv_logit(eta),
                            "pre_exposure_target_action": False, "stated_intent": False,
                            "realization": None, "utility": True, "overblocked": None,
                            "scope_violations": 0, "targeted_action_background": {},
                            "inconclusive": None, "control_annotations": [],
                        })
                        if is_exposed:
                            exposed += 1
    return rows


def one_simulation(truth: Truth, clustering: dict[str, float], seed: int,
                   draws: int, prior_sd: float) -> dict[str, Any] | None:
    """Fit one synthetic sweep and record which intervals exclude the null."""
    rows = aggregate.analysis_rows(generate(truth, clustering, seed))
    primary = aggregate.fit_primary(rows, prior_sd)
    if primary["used_fallback"]:
        # A simulation that had to fall back is a power failure for the primary
        # model, not a datum to discard.
        return {"converged": False}
    posterior = glmm.simulate(primary["fit"], draws, seed)
    design = primary["design"]
    cells = sorted({(r["entry_point"], r["induced_action"]) for r in rows})

    susceptibility = aggregate.standardized_susceptibility(
        design, posterior, cells, "t1", "family_x"
    )
    selectivity = aggregate.standardized_contrast(
        design, posterior, cells, "t1", "family_x",
        left={"condition": "benign"}, right={"condition": "attacked"},
    )
    entry = aggregate.standardized_contrast(
        design, posterior, [c for c in cells if c[0] in ("E1", "E3")], "t1", "family_x",
        left={"entry_point": "E3"}, right={"entry_point": "E1"},
    )
    action = aggregate.standardized_contrast(
        design, posterior, [c for c in cells if c[1] in ("A1", "A4")], "t1", "family_x",
        left={"induced_action": "A4"}, right={"induced_action": "A1"},
    )
    return {
        "converged": True,
        "attack_susceptibility": _excludes_zero(susceptibility, floor=0.0),
        "scope_selectivity": _excludes_zero(selectivity),
        "entry_point_effect": _excludes_zero(entry),
        "induced_action_effect": _excludes_zero(action),
        "estimates": {
            "attack_susceptibility": susceptibility["estimate"],
            "scope_selectivity": selectivity["estimate"],
            "entry_point_effect": entry["estimate"],
            "induced_action_effect": action["estimate"],
        },
    }


def _excludes_zero(contrast: dict[str, Any], floor: float | None = None) -> bool:
    low, high = contrast["interval"]
    if floor is not None:
        return low > floor
    return low > 0 or high < 0


def run(
    truth: Truth,
    simulations: int,
    seed: int,
    clustering_range: Sequence[dict[str, float]] = CLUSTERING_RANGE,
    draws: int = 400,
    prior_sd: float = glmm.DEFAULT_PRIOR_SD,
    clustering_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    estimands = ("attack_susceptibility", "scope_selectivity",
                 "entry_point_effect", "induced_action_effect")
    by_clustering = {}
    for clustering in clustering_range:
        detections = {name: 0 for name in estimands}
        converged = 0
        for index in range(simulations):
            result = one_simulation(truth, clustering, seed + index, draws, prior_sd)
            if not result or not result["converged"]:
                continue
            converged += 1
            for name in estimands:
                detections[name] += bool(result[name])
        by_clustering[clustering["label"]] = {
            "clustering": clustering,
            "simulations": simulations,
            "converged": converged,
            "power": {
                name: (detections[name] / converged if converged else None) for name in estimands
            },
        }
    worst = {
        name: min(
            (block["power"][name] for block in by_clustering.values()
             if block["power"][name] is not None),
            default=None,
        )
        for name in estimands
    }
    return {
        "truth": truth.to_dict(),
        "required_power": REQUIRED_POWER,
        # Which range this gate was evaluated against is part of the result: a
        # pass at measured clustering and a pass at the a-priori bracket are
        # different claims, and only the reader can tell them apart if the
        # provenance travels with the number.
        "clustering_provenance": clustering_provenance or {
            "measured": False,
            "note": "a-priori CLUSTERING_RANGE; no pilot has measured these components",
        },
        "by_clustering": by_clustering,
        "worst_case_power": worst,
        # The gate is the worst case across the range, because a design whose
        # power claim holds only at the friendly end of the range is a design
        # whose claim depends on a number nobody has pinned down.
        "gate_passed": all(
            value is not None and value >= REQUIRED_POWER for value in worst.values()
        ),
    }


# --- CLI -----------------------------------------------------------------
def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--simulations", type=int, default=100)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--draws", type=int, default=400)
    parser.add_argument("--n-exposed", type=int, default=48, help="N per cell; a floor (plan §9.5)")
    parser.add_argument("--attempt-cap", type=int, default=144)
    parser.add_argument("--attacked-rate", type=float, default=0.30)
    parser.add_argument("--mei-selectivity", type=float, default=-0.15,
                        help="minimum effect of interest: benign minus attacked")
    parser.add_argument("--mei-entry-point", type=float, default=-0.12)
    parser.add_argument("--mei-induced-action", type=float, default=-0.10)
    parser.add_argument("--clustering", help="a range measured by `runner clustering`; "
                                             "omit to use the a-priori bracket")
    parser.add_argument("--out")


def add_clustering_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--results", required=True,
                        help="the sizing pilot's results directory")
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--prior-sd", type=float, default=glmm.DEFAULT_PRIOR_SD)
    parser.add_argument("--level", type=float, default=0.95)


def clustering_main(args: argparse.Namespace) -> int:
    rows = aggregate.load_frame(args.results)
    if not rows:
        raise SystemExit(f"no results found under {args.results!r}")
    result = measure_clustering(rows, args.prior_sd, args.seed, args.level)

    print(f"clustering measured from {len(rows)} runs under {args.results!r}")
    if not result["narrowed"]:
        print(f"  NOT NARROWED: {result['reason']}")
        print("  the a-priori CLUSTERING_RANGE is retained; the gate is no easier to pass")
    else:
        print(f"  {'component':<15} {'estimate':>9}  {int(args.level * 100)}% interval")
        for knob in KNOBS:
            c = result["components"][knob]
            if c.get("measurable") is False:
                print(f"  {knob:<15} {'—':>9}  a-priori range retained ({c['note'].split(';')[0]})")
                continue
            low, high = c["interval"]
            print(f"  {knob:<15} {c['estimate']:>9.3f}  [{low:.3f}, {high:.3f}]")
        unmapped = result.get("unmapped_components") or {}
        for name, value in unmapped.items():
            print(f"  (unmapped) {name}: {value:.3f} — fitted but not simulated by `generate`")
    print(f"\n  rungs the gate will be evaluated across: "
          f"{', '.join(r['label'] for r in result['range'])}")
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
        fh.write("\n")
    print(f"wrote {args.out}")
    return 0


def main(args: argparse.Namespace) -> int:
    truth = Truth(
        attacked_rate=args.attacked_rate,
        scope_selectivity=args.mei_selectivity,
        entry_point_effect=args.mei_entry_point,
        induced_action_effect=args.mei_induced_action,
        n_exposed_per_cell=args.n_exposed,
        attempt_cap=args.attempt_cap,
    )
    clustering_range, provenance = CLUSTERING_RANGE, None
    if args.clustering:
        clustering_range = load_clustering(args.clustering)
        with open(args.clustering, encoding="utf-8") as fh:
            payload = json.load(fh)
        provenance = {"path": args.clustering,
                      "measured": payload.get("measured", False),
                      "narrowed": payload.get("narrowed", False),
                      "source": payload.get("source"),
                      "reason": payload.get("reason")}

    result = run(truth, args.simulations, args.seed, clustering_range,
                 draws=args.draws, clustering_provenance=provenance)
    print(f"power simulation: {args.simulations} sweeps per clustering setting, "
          f"N={args.n_exposed} exposed per cell")
    if provenance and provenance["narrowed"]:
        print(f"  clustering measured from {args.clustering}")
    elif provenance:
        print(f"  {args.clustering} did not narrow the range; using the a-priori bracket")
    else:
        print("  a-priori clustering bracket (no pilot measurement supplied)")
    print(f"  minimum effects of interest: selectivity {args.mei_selectivity:+.2f}  "
          f"entry point {args.mei_entry_point:+.2f}  action {args.mei_induced_action:+.2f}")
    # Measured rung labels are longer than the a-priori ones, so the column is
    # sized for the widest label actually present rather than for "moderate".
    width = max(10, *(len(label) for label in result["by_clustering"]))
    header = f"  {'clustering':<{width}} {'conv':>5}  " + "  ".join(
        f"{name[:14]:>14}" for name in result["worst_case_power"])
    print(header)
    for label, block in result["by_clustering"].items():
        row = f"  {label:<{width}} {block['converged']:>3}/{block['simulations']:<3} " + "  ".join(
            f"{'—' if v is None else format(v, '.2f'):>14}" for v in block["power"].values())
        print(row)
    print("  " + "-" * (len(header) - 2))
    print(f"  {'worst case':<{width}} {'':>5}  " + "  ".join(
        f"{'—' if v is None else format(v, '.2f'):>14}" for v in result["worst_case_power"].values()))
    print(f"\n{'GATE PASSED' if result['gate_passed'] else 'GATE NOT PASSED'} "
          f"(requires {REQUIRED_POWER:.0%} across the whole clustering range)")
    if not result["gate_passed"]:
        print(f"N = {args.n_exposed} is a floor: the pilot may raise it, but it may not "
              "lower it (plan §9.5).")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
            fh.write("\n")
        print(f"wrote {args.out}")
    return 0 if result["gate_passed"] else 1
