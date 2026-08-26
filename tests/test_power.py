"""The power gate (plan §9.5, milestone 8).

The gate itself is a long-running simulation and is not run here. What is
checked here is that the thing it simulates is the design: the exact
allocation, recruitment included, and the same fit the aggregator uses.
"""

from __future__ import annotations

import argparse
import copy
import json

import pytest

from taskbound import glmm, power


def _valid_refusal_artifact():
    return power._unnarrowed(
        {
            "runs": 10,
            "analysis_rows": 8,
            "settings": {
                "prior_sd": power.RELEASE_PRIOR_SD,
                "seed": 1,
                "level": power.RELEASE_INTERVAL_LEVEL,
            },
        },
        "pilot did not resolve the clustering components",
    )


def _valid_narrowed_artifact():
    estimates = {"paraphrase_sd": 0.4, "injection_sd": 0.2, "placement_sd": 0.15}
    intervals = {
        "paraphrase_sd": [0.3, 0.6],
        "injection_sd": [0.1, 0.3],
        "placement_sd": [0.1, 0.2],
    }
    components = {
        knob: {"estimate": estimate, "interval": intervals[knob]}
        for knob, estimate in estimates.items()
    }
    components["cell_sd"] = {
        "estimate": None,
        "interval": [None, None],
        "measurable": False,
    }
    return power._seal_artifact({
        "artifact_type": power.CLUSTERING_ARTIFACT_TYPE,
        "artifact_version": power.CLUSTERING_ARTIFACT_VERSION,
        "measured": True,
        "narrowed": True,
        "level": power.RELEASE_INTERVAL_LEVEL,
        "point_estimate": estimates,
        "source": {
            "runs": 10,
            "analysis_rows": 8,
            "settings": {
                "prior_sd": power.RELEASE_PRIOR_SD,
                "seed": power.RELEASE_SEED,
                "level": power.RELEASE_INTERVAL_LEVEL,
            },
        },
        "components": components,
        "unmeasurable_knobs": ["cell_sd"],
        "range": [
            {"label": "measured_low", "paraphrase_sd": 0.3, "cell_sd": 0.3,
             "injection_sd": 0.1, "placement_sd": 0.1},
            {"label": "measured", "paraphrase_sd": 0.4, "cell_sd": 0.5,
             "injection_sd": 0.2, "placement_sd": 0.15},
            {"label": "measured_high", "paraphrase_sd": 0.6, "cell_sd": 0.6,
             "injection_sd": 0.3, "placement_sd": 0.2},
        ],
    })


def test_power_defaults_match_the_release_allocation():
    truth = power.Truth()
    assert truth.n_exposed_per_cell == 9
    assert truth.attempt_cap == 27
    assert len(power.MODEL_FAMILIES) == 8


def test_the_simulated_allocation_is_the_release_allocation():
    truth = power.Truth(n_exposed_per_cell=6, attempt_cap=18, near_miss_per_block=6)
    all_rows = power.generate(truth, power.CLUSTERING_RANGE[1], seed=1)
    # Near-miss blocks are generated too — C2 needs them — but they carry no
    # cell, so the injected allocation is what this test is about.
    rows = [r for r in all_rows if r["condition"] != "near_miss"]
    cells = {r["cell"] for r in rows}
    groups = {(r["task"], r["cell"]) for r in rows}
    # Sixteen distinct cell labels, twenty-four (task, cell) groups: the eight
    # auxiliary cells are a subset of the core task's (plan §6.2).
    assert len(cells) == 16
    assert len(groups) == 24
    assert {r["task"] for r in rows} == {power.CORE_TASK, *power.AUXILIARY_CELLS}
    assert {r["model_family"] for r in rows} == set(power.MODEL_FAMILIES)
    borrowed_slot = False
    for family in power.MODEL_FAMILIES:
        for task, cell in groups:
            for condition in ("attacked", "benign"):
                subset = [r for r in rows if r["model_family"] == family
                          and r["task"] == task and r["cell"] == cell
                          and r["condition"] == condition]
                exposed_by_paraphrase = {
                    p: sum(1 for r in subset if r["paraphrase"] == p and r["exposed"])
                    for p in power.PARAPHRASES
                }
                attempts_by_paraphrase = {
                    p: sum(1 for r in subset if r["paraphrase"] == p)
                    for p in power.PARAPHRASES
                }
                assert all(n <= 2 for n in exposed_by_paraphrase.values())
                if any(n < 2 for n in exposed_by_paraphrase.values()):
                    assert len(subset) == 18
                borrowed_slot |= any(n > 6 for n in attempts_by_paraphrase.values())
                assert len(subset) <= 18
    assert borrowed_slot


def test_the_near_miss_arm_is_generated_for_c2():
    """C2's in-scope term needs the near-miss blocks, so a simulation that
    omitted them could not discharge its gate (plan §9.5)."""
    truth = power.Truth(n_exposed_per_cell=6, attempt_cap=18, near_miss_per_block=6)
    rows = power.generate(truth, power.CLUSTERING_RANGE[1], seed=1)
    near_miss = [r for r in rows if r["condition"] == "near_miss"]
    blocks = {(r["task"], r["near_miss_action"]) for r in near_miss}
    # Four on the core task, two on each of the four auxiliary tasks.
    assert len(blocks) == 12
    assert len(near_miss) == 12 * len(power.MODEL_FAMILIES) * 6
    assert {r["model_family"] for r in near_miss} == set(power.MODEL_FAMILIES)
    # The two near-miss rates live on different denominators, and the generator
    # has to produce runs that separate them: some runs neither did the job nor
    # declined, and they leave overblocking's denominator while staying in the
    # in-scope one (plan §7.4).
    assert all(r["in_scope_action"] is not None for r in near_miss)
    assert any(r["overblocked"] is None for r in near_miss)
    dropped = [r for r in near_miss if r["overblocked"] is None]
    assert all(r["in_scope_action"] is False for r in dropped)


def test_low_exposure_entry_points_cost_attempts_rather_than_sample():
    truth = power.Truth(n_exposed_per_cell=6, attempt_cap=60)
    rows = power.generate(truth, power.CLUSTERING_RANGE[0], seed=2)
    attempts = {entry: sum(1 for r in rows if r["entry_point"] == entry)
                for entry in ("E1", "E2", "E3")}
    assert attempts["E3"] > attempts["E2"] > attempts["E1"]


def test_the_truth_is_recovered_in_direction_by_the_analysis_function():
    truth = power.Truth(n_exposed_per_cell=6, attempt_cap=18)
    result = power.one_simulation(truth, power.CLUSTERING_RANGE[0], seed=5,
                                  draws=200, prior_sd=2.5)
    assert result["converged"]
    estimates = result["estimates"]
    assert 0.0 < estimates["attack_susceptibility"] < 1.0
    assert estimates["scope_selectivity"] < 0  # benign followed less often than attacked


def test_the_gate_is_the_worst_case_across_the_clustering_range():
    """Not the best guess within it: the pilot has not yet said where we are."""
    result = power.run(power.Truth(n_exposed_per_cell=3, attempt_cap=6), simulations=1, seed=9,
                       clustering_range=power.CLUSTERING_RANGE[:2], draws=100)
    for name, worst in result["worst_case_power"].items():
        observed = [b["power"][name] for b in result["by_clustering"].values()
                    if b["power"][name] is not None]
        assert worst == min(observed)
    assert result["required_power"] == 0.80
    assert result["confirmatory_estimands"] == [
        "attack_susceptibility", "scope_discrimination"
    ]
    assert set(result["exploratory_estimands"]) == {
        "scope_selectivity", "entry_point_effect", "induced_action_effect"
    }
    susceptibility = result["worst_case_power"]["attack_susceptibility"]
    assert result["power_requirement_met"] == (
        susceptibility is not None and susceptibility >= 0.80
    )
    assert result["evaluation_type"] == "diagnostic"
    assert result["gate_passed"] is False


def test_a_non_converging_simulation_is_a_power_failure_not_a_discard(monkeypatch):
    outcomes = iter([
        {"converged": True, "attack_susceptibility": True,
         "scope_discrimination": True, "scope_selectivity": True,
         "entry_point_effect": True, "induced_action_effect": True},
        {"converged": False},
    ])
    monkeypatch.setattr(power, "one_simulation", lambda *args, **kwargs: next(outcomes))
    result = power.run(power.Truth(n_exposed_per_cell=3, attempt_cap=6), simulations=2, seed=1,
                       clustering_range=power.CLUSTERING_RANGE[:1], draws=100)
    block = result["by_clustering"]["low"]
    assert block["simulations"] == 2
    assert block["converged"] == 1
    assert block["power"]["attack_susceptibility"] == 0.5


def test_only_the_exact_release_configuration_can_pass_the_gate(monkeypatch):
    detected = {"converged": True, "attack_susceptibility": True,
                "scope_discrimination": True, "scope_selectivity": True,
                "entry_point_effect": True, "induced_action_effect": True}
    monkeypatch.setattr(power, "one_simulation", lambda *args, **kwargs: detected)
    monkeypatch.setattr(power, "_pilot_binding_problems", lambda *args: [])
    exact = power.run(power.Truth(), simulations=500, seed=1,
                      clustering_range=power.CLUSTERING_RANGE,
                      clustering_provenance=_valid_refusal_artifact())
    diagnostic = power.run(power.Truth(), simulations=499, seed=1,
                           clustering_range=power.CLUSTERING_RANGE[:1])
    assert exact["evaluation_type"] == "release_gate"
    assert exact["gate_passed"] is True
    assert diagnostic["evaluation_type"] == "diagnostic"
    assert diagnostic["power_requirement_met"] is True
    assert diagnostic["gate_passed"] is False


@pytest.mark.parametrize("kwargs,mismatch", [
    ({"seed": 2}, "seed"),
    ({"draws": 1}, "draws"),
    ({"prior_sd": 1.0}, "prior_sd"),
])
def test_altered_release_analysis_is_diagnostic(monkeypatch, kwargs, mismatch):
    detected = {"converged": True, "attack_susceptibility": True,
                "scope_discrimination": True, "scope_selectivity": True,
                "entry_point_effect": True, "induced_action_effect": True}
    monkeypatch.setattr(power, "one_simulation", lambda *args, **kw: detected)
    run_kwargs = {name: value for name, value in kwargs.items() if name != "seed"}
    result = power.run(
        power.Truth(), simulations=power.RELEASE_SIMULATIONS,
        seed=kwargs.get("seed", power.RELEASE_SEED),
        clustering_range=power.CLUSTERING_RANGE,
        clustering_provenance=_valid_refusal_artifact(), **run_kwargs,
    )
    assert result["gate_passed"] is False
    assert mismatch in result["release_analysis_mismatches"]


def test_release_gate_requires_a_clustering_step_artifact(monkeypatch):
    detected = {"converged": True, "attack_susceptibility": True,
                "scope_discrimination": True, "scope_selectivity": True,
                "entry_point_effect": True, "induced_action_effect": True}
    monkeypatch.setattr(power, "one_simulation", lambda *args, **kwargs: detected)
    omitted = power.run(power.Truth(), power.RELEASE_SIMULATIONS, seed=1)
    ad_hoc = power.run(
        power.Truth(), power.RELEASE_SIMULATIONS, seed=1,
        clustering_range=[dict(power.CLUSTERING_RANGE[0])],
        clustering_provenance={"measured": True, "range": [dict(power.CLUSTERING_RANGE[0])]},
    )
    assert omitted["evaluation_type"] == "diagnostic"
    assert ad_hoc["evaluation_type"] == "diagnostic"
    assert omitted["clustering_artifact_problems"]
    assert ad_hoc["clustering_artifact_problems"]


def test_unchanged_range_refusal_is_release_eligible_provenance(monkeypatch):
    detected = {"converged": True, "attack_susceptibility": True,
                "scope_discrimination": True, "scope_selectivity": True,
                "entry_point_effect": True, "induced_action_effect": True}
    monkeypatch.setattr(power, "one_simulation", lambda *args, **kwargs: detected)
    monkeypatch.setattr(power, "_pilot_binding_problems", lambda *args: [])
    artifact = _valid_refusal_artifact()
    result = power.run(
        power.Truth(), power.RELEASE_SIMULATIONS, seed=1,
        clustering_range=artifact["range"], clustering_provenance=artifact,
    )
    assert result["clustering_artifact_problems"] == []
    assert result["gate_passed"] is True


@pytest.mark.parametrize("mutate,problem", [
    (lambda artifact: artifact.update(components={}, point_estimate={}), "components"),
    (lambda artifact: artifact["components"]["paraphrase_sd"].update(estimate=-0.1),
     "invalid estimate"),
    (lambda artifact: artifact["components"]["injection_sd"].update(interval=[0.1, float("inf")]),
     "invalid interval"),
    (lambda artifact: artifact["range"][0].update(paraphrase_sd=0.01), "not derived"),
    (lambda artifact: artifact["range"][1].update(label="measured_low"), "unique"),
    (lambda artifact: artifact["range"][1].update(cell_sd=0.01), "not derived"),
])
def test_forged_narrowed_artifacts_are_diagnostic(monkeypatch, mutate, problem):
    detected = {"converged": True, "attack_susceptibility": True,
                "scope_discrimination": True, "scope_selectivity": True,
                "entry_point_effect": True, "induced_action_effect": True}
    monkeypatch.setattr(power, "one_simulation", lambda *args, **kwargs: detected)
    artifact = copy.deepcopy(_valid_narrowed_artifact())
    mutate(artifact)
    result = power.run(
        power.Truth(), power.RELEASE_SIMULATIONS, power.RELEASE_SEED,
        artifact["range"], clustering_provenance=artifact,
    )
    assert result["evaluation_type"] == "diagnostic"
    assert result["gate_passed"] is False
    assert any(problem in message for message in result["clustering_artifact_problems"])


def test_valid_narrowed_artifact_is_reconstructed_without_problems(monkeypatch):
    monkeypatch.setattr(power, "_pilot_binding_problems", lambda *args: [])
    assert power.clustering_artifact_problems(_valid_narrowed_artifact()) == []


def test_clustering_artifact_must_reproduce_from_hashed_pilot_inputs(monkeypatch):
    rows = _pilot_rows()
    inputs = {
        "results_path": "sizing",
        "files": [{"path": "run.json", "sha256": "a" * 64}],
        "combined_sha256": "b" * 64,
    }
    artifact = power.measure_clustering(
        rows, power.RELEASE_PRIOR_SD, power.RELEASE_SEED,
        power.RELEASE_INTERVAL_LEVEL, inputs,
    )
    monkeypatch.setattr(power, "load_pilot_frame", lambda *args: (rows, inputs))
    assert power.clustering_artifact_problems(artifact, "/bundle") == []

    forged = copy.deepcopy(artifact)
    forged["source"]["runs"] += 1
    power._seal_artifact(forged)
    assert "does not reproduce" in " ".join(
        power.clustering_artifact_problems(forged, "/bundle")
    )

    changed_inputs = {**inputs, "combined_sha256": "c" * 64}
    monkeypatch.setattr(power, "load_pilot_frame", lambda *args: (rows, changed_inputs))
    assert "input hashes differ" in " ".join(
        power.clustering_artifact_problems(artifact, "/bundle")
    )


def test_clustering_pilot_inputs_resolve_relative_to_artifact_root(monkeypatch):
    rows = _pilot_rows()
    inputs = {
        "results_path": "sizing",
        "files": [{"path": "run.json", "sha256": "a" * 64}],
        "combined_sha256": "b" * 64,
    }
    artifact = power.measure_clustering(
        rows, power.RELEASE_PRIOR_SD, power.RELEASE_SEED,
        power.RELEASE_INTERVAL_LEVEL, inputs,
    )

    def relocated(path, artifact_root):
        assert path == "/new/bundle/sizing"
        assert artifact_root == "/new/bundle"
        return rows, inputs

    monkeypatch.setattr(power, "load_pilot_frame", relocated)
    assert power.clustering_artifact_problems(artifact, "/new/bundle") == []


def test_sizing_pilot_requires_complete_frozen_schedule(monkeypatch):
    schedule = {
        "host": {"id": "site_a", "hash": "host"}, "seed": 2,
        "exposed_target": 6, "attempt_cap": 18, "attempts": [],
        # The sizing pilot runs every group at six, near-miss and clean
        # included: it is measuring exposure, clustering, cost, and the
        # overblocking null-denominator drop rate.
        "near_miss_target": 6, "clean_target": 6,
    }
    sweep_id = "sweep_" + power.hashlib.sha256(
        json.dumps(schedule, sort_keys=True).encode()
    ).hexdigest()[:12]
    manifest = {
        "sweep_id": sweep_id, "schedule": schedule, "attempt_ids": [],
        "groups": {}, "totals": {},
    }
    monkeypatch.setattr(
        power.aggregate, "_execution_binding_problems",
        lambda *args, **kwargs: ["configuration is incomplete at attempt_17"],
    )
    problems = power._pilot_allocation_problems(
        [{"model_configuration_sha256": "a" * 64}], [manifest]
    )
    assert any("incomplete" in problem for problem in problems)


@pytest.mark.parametrize("field,value", [
    ("attacked_rate", 0.31),
    ("scope_selectivity", -0.14),
    ("entry_point_effect", -0.11),
    ("induced_action_effect", -0.09),
    ("model_family_logit_effect", 0.0),
    ("exposure", {"E1": 1.0, "E2": 0.55, "E3": 0.40, "E4": 0.98}),
    ("n_exposed_per_cell", 12),
    ("attempt_cap", 36),
])
def test_altered_release_truth_is_diagnostic(monkeypatch, field, value):
    detected = {"converged": True, "attack_susceptibility": True,
                "scope_discrimination": True, "scope_selectivity": True,
                "entry_point_effect": True, "induced_action_effect": True}
    monkeypatch.setattr(power, "one_simulation", lambda *args, **kwargs: detected)
    truth = power.Truth(**{field: value})
    result = power.run(truth, simulations=power.RELEASE_SIMULATIONS, seed=1,
                       clustering_range=power.CLUSTERING_RANGE[:1])
    assert result["evaluation_type"] == "diagnostic"
    assert result["gate_passed"] is False
    assert field in result["release_truth_mismatches"]


# --- the pilot -> clustering handoff (pilot_protocol.md Stage 2) ----------
def _pilot_rows(n=6, cap=18, clustering=1, seed=2):
    truth = power.Truth(n_exposed_per_cell=n, attempt_cap=cap)
    return power.generate(truth, power.CLUSTERING_RANGE[clustering], seed)


def test_a_pilot_that_did_not_resolve_the_components_does_not_narrow_the_range():
    """The gate must never become easier to pass on the strength of a pilot
    that measured nothing. A sizing pilot is small enough that the variance
    components land on the fit's lower boundary, and that is a refusal."""
    result = power.measure_clustering(_pilot_rows(), glmm.DEFAULT_PRIOR_SD, seed=1)

    assert result["narrowed"] is False
    assert result["measured"] is False
    assert "boundary" in result["reason"] or "resolve" in result["reason"]
    # The a-priori bracket is handed back unchanged, rungs and values alike.
    assert [r["label"] for r in result["range"]] == [c["label"] for c in power.CLUSTERING_RANGE]
    for returned, apriori in zip(result["range"], power.CLUSTERING_RANGE):
        for knob in power.KNOBS:
            assert returned[knob] == apriori[knob]


def test_a_refusal_still_reports_what_it_saw():
    """Refusing to narrow is not refusing to inform: the point estimates and
    the fit's provenance travel with the refusal so the reason is auditable."""
    result = power.measure_clustering(_pilot_rows(), glmm.DEFAULT_PRIOR_SD, seed=1)
    assert result["source"]["runs"] > 0
    assert result["source"]["converged"] is True
    # Every knob a fitted component maps to. `cell_sd` is absent by design: no
    # component maps to it since `host:cell` was dropped as aliased (§9.5).
    assert set(result["point_estimate"]) == set(power.KNOBS) - set(power.UNMEASURABLE_KNOBS)


def test_a_measured_range_carries_every_knob_the_simulation_needs():
    """Whatever the range's provenance, `generate` indexes these four by name,
    so a rung missing one would fail deep inside a simulation instead of here."""
    result = power.measure_clustering(_pilot_rows(), glmm.DEFAULT_PRIOR_SD, seed=1)
    for rung in result["range"]:
        assert set(power.KNOBS) <= set(rung)
        assert "label" in rung


def test_a_narrowed_range_carries_the_apriori_values_for_unmeasurable_knobs():
    """`generate` still draws a per-cell effect because between-cell variation is
    real; the fitted model just absorbs it into fixed effects. So `cell_sd` is
    simulated but unmeasurable, and its a-priori rungs must survive narrowing
    rather than being replaced by a number no fit produced."""
    # A deliberately larger diagnostic frame resolves the three measurable
    # components; this tests the narrowed branch, not the release allocation.
    #
    # The *high* rung, not the moderate one. `injection_id` separates from
    # `request_family:paraphrase` only across the cells that share a paraphrase
    # slot, so a small true injection sd sits close enough to zero that the
    # profile is flat and `measure_clustering` correctly refuses (the branch the
    # refusal half of this file's other test covers). Asserting narrowing on a
    # component the frame cannot resolve would be asserting a lucky draw.
    rows = power.generate(
        power.Truth(n_exposed_per_cell=36, attempt_cap=108,
                    exposure={entry: 1.0 for entry in power.ENTRY_POINTS}),
        power.CLUSTERING_RANGE[2], seed=5,
    )
    result = power.measure_clustering(rows, glmm.DEFAULT_PRIOR_SD, seed=1)
    assert result["narrowed"] is True
    assert result["components"]["cell_sd"]["measurable"] is False

    for rung, apriori in zip(result["range"], power.CLUSTERING_RANGE):
        for knob in power.UNMEASURABLE_KNOBS:
            assert rung[knob] == apriori[knob]
        # and the measurable ones did move off the a-priori values
        assert rung["paraphrase_sd"] != apriori["paraphrase_sd"]


def test_load_clustering_rejects_a_range_missing_a_knob(tmp_path):
    path = tmp_path / "clustering.json"
    path.write_text(json.dumps({"range": [{"label": "measured", "paraphrase_sd": 0.4}]}))
    with pytest.raises(SystemExit) as excinfo:
        power.load_clustering(str(path))
    assert "cell_sd" in str(excinfo.value)


@pytest.mark.parametrize("mutate,problem", [
    (lambda rung: rung.update(label=None), "non-string label"),
    (lambda rung: rung.update(paraphrase_sd=float("nan")), "invalid paraphrase_sd"),
    (lambda rung: rung.update(cell_sd=-0.1), "invalid cell_sd"),
])
def test_diagnostic_clustering_ranges_are_validated_before_simulation(
    tmp_path, mutate, problem
):
    rung = dict(power.CLUSTERING_RANGE[0])
    mutate(rung)
    path = tmp_path / "range.json"
    path.write_text(json.dumps([rung]))
    with pytest.raises(SystemExit, match=problem):
        power.load_clustering_input(str(path))


def test_ad_hoc_clustering_range_can_run_only_as_a_diagnostic(tmp_path, monkeypatch):
    path = tmp_path / "range.json"
    path.write_text(json.dumps([power.CLUSTERING_RANGE[0]]))
    clustering_range, payload = power.load_clustering_input(str(path))
    provenance = {"path": str(path), "range": clustering_range,
                  "input_type": "hand_authored_range"}
    detected = {"converged": True, "attack_susceptibility": True,
                "scope_discrimination": True, "scope_selectivity": True,
                "entry_point_effect": True, "induced_action_effect": True}
    monkeypatch.setattr(power, "one_simulation", lambda *args, **kwargs: detected)
    result = power.run(
        power.Truth(), power.RELEASE_SIMULATIONS, power.RELEASE_SEED,
        clustering_range, clustering_provenance=provenance,
    )
    assert payload == [power.CLUSTERING_RANGE[0]]
    assert result["evaluation_type"] == "diagnostic"
    assert result["gate_passed"] is False


def test_ad_hoc_clustering_cli_reports_diagnostic_without_crashing(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "range.json"
    path.write_text(json.dumps([power.CLUSTERING_RANGE[0]]))
    detected = {"converged": True, "attack_susceptibility": True,
                "scope_discrimination": True, "scope_selectivity": True,
                "entry_point_effect": True, "induced_action_effect": True}
    monkeypatch.setattr(power, "one_simulation", lambda *args, **kwargs: detected)
    parser = argparse.ArgumentParser()
    power.add_arguments(parser)
    args = parser.parse_args(["--simulations", "1", "--clustering", str(path)])

    assert power.main(args) == 0
    output = capsys.readouterr().out
    assert "ad-hoc clustering range" in output
    assert "DIAGNOSTIC ONLY" in output


def test_failed_release_gate_blocks_nine_run_design(monkeypatch, capsys):
    estimands = (
        "attack_susceptibility", "scope_discrimination", "scope_selectivity",
        "entry_point_effect", "induced_action_effect",
    )
    monkeypatch.setattr(power, "run", lambda *args, **kwargs: {
        "clustering_artifact_problems": [],
        "by_clustering": {
            "conservative": {
                "converged": 500,
                "simulations": 500,
                "power": {name: 0.79 for name in estimands},
            }
        },
        "worst_case_power": {name: 0.79 for name in estimands},
        "gate_eligible": True,
        "gate_passed": False,
    })
    parser = argparse.ArgumentParser()
    power.add_arguments(parser)
    args = parser.parse_args(["--simulations", "500"])

    assert power.main(args) == 1
    output = capsys.readouterr().out
    assert "Release is blocked at the registered N = 9" in output
    assert "separately versioned design" in output
    assert "pilot may raise" not in output


def test_load_clustering_accepts_what_measure_clustering_writes(tmp_path, monkeypatch):
    """The writer and the reader are the same contract, on both branches: a
    refusal hands back the a-priori bracket and a narrowed range mixes measured
    values with a carried-through `cell_sd`, and `run` has to accept either."""
    refused_rows = _pilot_rows()
    refused_inputs = {"results_path": "refused", "files": [],
                      "combined_sha256": power._canonical_sha256([])}
    refused = power.measure_clustering(
        refused_rows, glmm.DEFAULT_PRIOR_SD, seed=1, pilot_inputs=refused_inputs
    )
    narrowed_rows = power.generate(
        power.Truth(n_exposed_per_cell=36, attempt_cap=108,
                    exposure={entry: 1.0 for entry in power.ENTRY_POINTS}),
        power.CLUSTERING_RANGE[2], seed=5,
    )
    narrowed_inputs = {"results_path": "narrowed", "files": [],
                       "combined_sha256": power._canonical_sha256([])}
    narrowed = power.measure_clustering(
        narrowed_rows, glmm.DEFAULT_PRIOR_SD, seed=1, pilot_inputs=narrowed_inputs,
    )
    assert refused["narrowed"] is False and narrowed["narrowed"] is True

    for measured, rows, inputs in (
        (refused, refused_rows, refused_inputs),
        (narrowed, narrowed_rows, narrowed_inputs),
    ):
        monkeypatch.setattr(
            power, "load_pilot_frame", lambda *args, r=rows, i=inputs: (r, i)
        )
        path = tmp_path / f"clustering_{measured['narrowed']}.json"
        path.write_text(json.dumps(measured))
        assert power.load_clustering(str(path)) == measured["range"]


def test_the_gate_records_whether_its_clustering_was_measured_or_assumed():
    """A pass at measured clustering and a pass at the a-priori bracket are
    different claims; only the recorded provenance tells them apart."""
    assumed = power.run(power.Truth(n_exposed_per_cell=3, attempt_cap=6), simulations=1, seed=3,
                        clustering_range=power.CLUSTERING_RANGE[:1], draws=100)
    assert assumed["clustering_provenance"]["measured"] is False

    stated = power.run(power.Truth(n_exposed_per_cell=3, attempt_cap=6), simulations=1, seed=3,
                       clustering_range=power.CLUSTERING_RANGE[:1], draws=100,
                       clustering_provenance={"measured": True, "path": "pilot/clustering.json"})
    assert stated["clustering_provenance"]["measured"] is True
