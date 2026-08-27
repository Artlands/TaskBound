"""The estimator-calibration study (plan §9.1, §9.5).

The study itself is a long-running simulation and is not run here. What is
checked here is that the two things it depends on are right: that its copy of
the generating process is the *same* generating process the power gate uses —
so the truth it computes describes the data that was actually fitted — and that
its coverage and type-I arithmetic says what it claims on records whose answers
are known by hand.
"""

from __future__ import annotations

import pytest

from taskbound import aggregate, coverage, power


# --- the replica --------------------------------------------------------
@pytest.mark.parametrize("level", [c["label"] for c in power.CLUSTERING_RANGE])
@pytest.mark.parametrize("seed", [1, 2, 13])
def test_replica_reproduces_the_power_generator_exactly(level, seed):
    """The whole study is void if these two generators diverge.

    `generate_with_effects` is a transcription, and a transcription can drift.
    It shares one RNG stream with everything it draws, so a single reordered or
    dropped call — `setdefault` evaluates its default eagerly, `competent`
    short-circuits — silently produces different data with a truth attached to
    it that describes neither.
    """
    clustering = next(c for c in power.CLUSTERING_RANGE if c["label"] == level)
    coverage.verify_replica(power.Truth(), clustering, seed)


def test_verify_replica_raises_when_the_generators_disagree(monkeypatch):
    def different(truth, clustering, seed):
        rows = power.generate(truth, clustering, seed)
        return rows[1:], {}

    monkeypatch.setattr(coverage, "generate_with_effects", different)
    with pytest.raises(AssertionError, match="drifted"):
        coverage.verify_replica(power.Truth(), power.CLUSTERING_RANGE[0], 1)


def test_effects_cover_every_group_the_rows_use():
    rows, effects = coverage.generate_with_effects(
        power.Truth(), power.CLUSTERING_RANGE[1], 1
    )
    injected = [r for r in rows if r["condition"] != "near_miss"]
    assert {r["cell"] for r in injected} <= set(effects["cell_effect"])
    assert {r["injection_id"] for r in injected} <= set(effects["injection_effect"])
    assert {r["placement_id"] for r in injected} <= set(effects["placement_effect"])
    assert set(effects["family_delta"]) == set(power.MODEL_FAMILIES)


# --- the computed truth -------------------------------------------------
def test_true_c1_is_the_generating_rate_when_nothing_perturbs_it():
    """With no clustering, no design effects and no family spread, C1's truth is
    the base rate itself — the one case where the answer is known outright."""
    truth = power.Truth(
        attacked_rate=0.3, scope_selectivity=-0.05, entry_point_effect=0.0,
        induced_action_effect=0.0, model_family_logit_effect=0.0,
    )
    flat = {"paraphrase_sd": 0.0, "cell_sd": 0.0, "injection_sd": 0.0,
            "placement_sd": 0.0}
    _, effects = coverage.generate_with_effects(truth, flat, 1)
    cells = [(e, a) for a in power.INDUCED_ACTIONS for e in power.ENTRY_POINTS]
    assert coverage.true_c1(effects, cells, power.MODEL_FAMILIES) == pytest.approx(
        0.3, abs=1e-9
    )


def test_true_c1_carries_the_realized_cell_effects():
    """`cell_effect` is absorbed into the fixed block, so it belongs in C1's
    truth rather than being averaged out like the fitted components."""
    truth = power.Truth(model_family_logit_effect=0.0)
    flat = {"paraphrase_sd": 0.0, "cell_sd": 0.0, "injection_sd": 0.0,
            "placement_sd": 0.0}
    clustered = {**flat, "cell_sd": 0.6}
    cells = [(e, a) for a in power.INDUCED_ACTIONS for e in power.ENTRY_POINTS]
    _, without = coverage.generate_with_effects(truth, flat, 1)
    _, with_cells = coverage.generate_with_effects(truth, clustered, 1)
    assert coverage.true_c1(without, cells, power.MODEL_FAMILIES) != pytest.approx(
        coverage.true_c1(with_cells, cells, power.MODEL_FAMILIES), abs=1e-6
    )


def test_true_in_scope_is_the_generating_rate_when_flat():
    truth = power.Truth(
        in_scope_action_rate=0.75, in_scope_action_effect=0.0,
        model_family_logit_effect=0.0,
    )
    _, effects = coverage.generate_with_effects(truth, power.CLUSTERING_RANGE[0], 1)
    value = coverage.true_in_scope(
        effects, list(power.INDUCED_ACTIONS), power.MODEL_FAMILIES
    )
    assert value == pytest.approx(0.75, abs=1e-9)


def test_attacked_by_action_uses_the_nested_weighting():
    """`_attacked_by_action` averages within an action before averaging across
    them, which differs from a flat average when the actions carry different
    numbers of entry points. The truth has to use the same order or it is a
    different estimand."""
    truth = power.Truth(model_family_logit_effect=0.0)
    _, effects = coverage.generate_with_effects(truth, power.CLUSTERING_RANGE[2], 1)
    lopsided = {"A1": ["E1"], "A2": ["E1", "E2", "E3", "E4"]}
    nested = coverage.true_attacked_by_action(effects, lopsided, ["family_1"])
    flat = [
        power._inv_logit(coverage._attacked_eta(effects, entry, action, "family_1"))
        for action, entries in lopsided.items() for entry in entries
    ]
    assert nested != pytest.approx(sum(flat) / len(flat), abs=1e-9)
    per_action = [
        power._inv_logit(coverage._attacked_eta(effects, "E1", "A1", "family_1")),
        sum(
            power._inv_logit(coverage._attacked_eta(effects, e, "A2", "family_1"))
            for e in ("E1", "E2", "E3", "E4")
        ) / 4,
    ]
    assert nested == pytest.approx(sum(per_action) / 2, abs=1e-12)


# --- null calibration ---------------------------------------------------
def test_c1_null_calibrates_onto_the_registered_floor():
    clustering = power.CLUSTERING_RANGE[1]
    truth = coverage.calibrate_c1_null(
        clustering, aggregate.PRACTICAL_RISK_FLOOR, probes=8
    )
    realized = coverage._expected_c1(truth, clustering, 8)
    assert realized == pytest.approx(aggregate.PRACTICAL_RISK_FLOOR, abs=0.01)


def test_c2_null_calibration_refuses_an_unreachable_deficit():
    """A deficit floor of 0.20 means the two rates are 0.80 apart. From a high
    attacked rate there is no in-scope rate below 1 that reaches it, and the
    calibration says so rather than returning a truth that misses the floor."""
    with pytest.raises(ValueError, match="unreachable"):
        coverage.calibrate_c2_null(
            power.CLUSTERING_RANGE[1], aggregate.DISCRIMINATION_DEFICIT_FLOOR,
            attacked_rate=0.45, probes=4,
        )


def test_scaled_truth_stays_in_the_logit_domain_at_a_low_base_rate():
    """`power.Truth`'s shipped offsets ask for `logit(0)` once the base rate
    falls to them, which is the reason the null needs its own scaling."""
    truth = coverage._scaled_truth(0.05)
    rows, _ = coverage.generate_with_effects(truth, power.CLUSTERING_RANGE[0], 1)
    assert rows
    assert truth.attacked_rate + truth.entry_point_effect > 0
    assert truth.attacked_rate + truth.induced_action_effect > 0
    assert truth.attacked_rate + truth.scope_selectivity > 0


# --- the arithmetic the study reports -----------------------------------
def _record(truth, low, high, fired=False, floor=None):
    estimand = {"truth": truth, "estimate": (low + high) / 2,
                "interval": [low, high]}
    if floor is not None:
        estimand["floor"] = floor
        estimand["gate_fired"] = fired
    return {
        "seed": 1, "converged": True, "n_analysis_rows": 10,
        "variance_components": {"injection_id": 0.2}, "at_variance_boundary": [],
        "estimands": {"c1_attack_susceptibility": estimand},
    }


def test_summarize_counts_two_sided_and_one_sided_coverage_separately():
    """An interval that sits entirely below the truth misses two-sided *and*
    lower-bound coverage; one that sits entirely above misses two-sided only.
    The gate reads the lower bound, so the two numbers cannot be collapsed."""
    records = [
        _record(0.5, 0.4, 0.6),   # covered both ways
        _record(0.5, 0.6, 0.7),   # interval above the truth: lower bound fails
        _record(0.5, 0.3, 0.4),   # interval below the truth: upper bound fails
    ]
    block = coverage.summarize(records)["estimands"]["c1_attack_susceptibility"]
    assert block["n"] == 3
    assert block["coverage_two_sided"] == pytest.approx(1 / 3)
    assert block["coverage_lower_bound"] == pytest.approx(2 / 3)
    assert block["coverage_upper_bound"] == pytest.approx(2 / 3)


def test_summarize_reports_bias_against_the_truth():
    records = [_record(0.20, 0.25, 0.35), _record(0.20, 0.15, 0.25)]
    block = coverage.summarize(records)["estimands"]["c1_attack_susceptibility"]
    assert block["bias"] == pytest.approx(0.05)
    assert block["mean_truth"] == pytest.approx(0.20)


def test_type_i_error_is_computed_only_over_replicates_whose_truth_is_null():
    """H0 is composite — theta <= floor — so a replicate whose realized truth
    landed above the floor is not a false positive when its gate fires, and
    counting it as one would understate the error rate."""
    records = [
        _record(0.08, 0.09, 0.20, fired=True, floor=0.10),    # null, fired: type-I
        _record(0.09, 0.02, 0.15, fired=False, floor=0.10),   # null, did not fire
        _record(0.30, 0.25, 0.40, fired=True, floor=0.10),    # not null: true positive
    ]
    block = coverage.summarize(records)["estimands"]["c1_attack_susceptibility"]
    assert block["null_replicates"] == 2
    assert block["type_i_error_on_true_nulls"] == pytest.approx(0.5)
    assert block["gate_fire_rate_all_replicates"] == pytest.approx(2 / 3)
    assert block["nominal_alpha"] == aggregate.CONFIRMATORY_ALPHA


def test_summarize_reports_the_fallback_rate_rather_than_dropping_it():
    """A replicate that fell back to the fixed-effects fit is a calibration
    result, not a datum to discard — the same rule §9.5 applies to power."""
    records = [_record(0.2, 0.1, 0.3), {"seed": 2, "converged": False}]
    summary = coverage.summarize(records)
    assert summary["replicates"] == 2
    assert summary["converged"] == 1
    assert summary["fallback_rate"] == pytest.approx(0.5)


def test_summarize_tracks_components_pinned_at_the_variance_boundary():
    records = [_record(0.2, 0.1, 0.3), _record(0.2, 0.1, 0.3)]
    records[0]["at_variance_boundary"] = ["placement_id"]
    summary = coverage.summarize(records)
    assert summary["variance_components_at_boundary"] == {"placement_id": 0.5}


def test_wilson_interval_brackets_the_rate_and_narrows_with_n():
    low, high = coverage._wilson(95, 100)
    assert low < 0.95 < high
    wide = coverage._wilson(19, 20)
    assert (high - low) < (wide[1] - wide[0])
    assert coverage._wilson(0, 0) == pytest.approx((float("nan"), float("nan")), nan_ok=True)


def test_nominal_one_sided_level_matches_the_two_sided_interval():
    """The gate is a one-sided read of a two-sided interval, so its nominal
    lower-bound coverage is 0.975 and not 0.95."""
    assert coverage.NOMINAL_ONE_SIDED == pytest.approx(0.975)
    assert coverage.NOMINAL_LEVEL == pytest.approx(0.95)


# --- scenarios and wiring -----------------------------------------------
def test_build_scenario_covers_the_whole_clustering_range_by_default():
    arms = coverage.build_scenario("calibration")
    assert [a["clustering"]["label"] for a in arms] == [
        c["label"] for c in power.CLUSTERING_RANGE
    ]


def test_build_scenario_can_be_restricted_to_one_clustering_level():
    arms = coverage.build_scenario("calibration", "high")
    assert len(arms) == 1 and arms[0]["clustering"]["label"] == "high"


def test_build_scenario_rejects_an_unknown_name():
    with pytest.raises(ValueError, match="unknown scenario"):
        coverage.build_scenario("whatever")


def test_format_report_flags_miscalibration_in_the_text():
    """The report has to say so in words: a study whose finding is only visible
    by comparing two decimals is a study that gets skimmed past."""
    records = [_record(0.5, 0.6, 0.7, floor=0.10) for _ in range(40)]
    text = coverage.format_report({
        "scenario": "calibration", "simulations": 40, "draws": 100,
        "prior_sd": 2.5,
        "arms": [{"arm": "a", "seconds": 1.0, "summary": coverage.summarize(records)}],
    })
    assert "MISCALIBRATED" in text
    assert "coverage" in text


def test_runner_exposes_the_coverage_command():
    from taskbound import runner

    with pytest.raises(SystemExit):
        runner.main(["coverage", "--scenario", "nonsense"])
