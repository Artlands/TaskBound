"""The power gate (plan §9.5, milestone 8).

The gate itself is a long-running simulation and is not run here. What is
checked here is that the thing it simulates is the design: the exact
allocation, recruitment included, and the same fit the aggregator uses.
"""

from __future__ import annotations

import json

import pytest

from taskbound import glmm, power


def test_the_simulated_allocation_is_the_v05_allocation():
    truth = power.Truth(n_exposed_per_cell=6, attempt_cap=18)
    rows = power.generate(truth, power.CLUSTERING_RANGE[1], seed=1)
    cells = {r["cell"] for r in rows}
    assert len(cells) == 12
    for cell in cells:
        for condition in ("attacked", "benign"):
            subset = [r for r in rows if r["cell"] == cell and r["condition"] == condition]
            exposed = sum(1 for r in subset if r["exposed"])
            assert exposed >= 6 or len(subset) >= 18  # target or cap, never neither
            # Recruitment is simulated in blocks of three, so a group that
            # stopped mid-recruitment still has balanced paraphrases.
            counts = {p: sum(1 for r in subset if r["paraphrase"] == p) for p in ("p1", "p2", "p3")}
            assert len(set(counts.values())) == 1


def test_low_exposure_entry_points_cost_attempts_rather_than_sample():
    truth = power.Truth(n_exposed_per_cell=6, attempt_cap=60)
    rows = power.generate(truth, power.CLUSTERING_RANGE[0], seed=2)
    attempts = {
        entry: sum(1 for r in rows if r["entry_point"] == entry) for entry in ("E1", "E2", "E3")
    }
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
    assert result["gate_passed"] == all(
        v is not None and v >= 0.80 for v in result["worst_case_power"].values()
    )


def test_a_non_converging_simulation_is_a_power_failure_not_a_discard():
    result = power.run(power.Truth(n_exposed_per_cell=3, attempt_cap=6), simulations=2, seed=1,
                       clustering_range=power.CLUSTERING_RANGE[:1], draws=100)
    block = result["by_clustering"]["low"]
    assert block["simulations"] == 2
    assert block["converged"] <= block["simulations"]


# --- the pilot -> clustering handoff (pilot_protocol.md Stage 2) ----------
def _pilot_rows(n=6, cap=18, clustering=1, seed=42):
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
    assert set(result["point_estimate"]) == set(power.KNOBS)


def test_a_measured_range_carries_every_knob_the_simulation_needs():
    """Whatever the range's provenance, `generate` indexes these four by name,
    so a rung missing one would fail deep inside a simulation instead of here."""
    result = power.measure_clustering(_pilot_rows(), glmm.DEFAULT_PRIOR_SD, seed=1)
    for rung in result["range"]:
        assert set(power.KNOBS) <= set(rung)
        assert "label" in rung


def test_load_clustering_rejects_a_range_missing_a_knob(tmp_path):
    path = tmp_path / "clustering.json"
    path.write_text(json.dumps({"range": [{"label": "measured", "paraphrase_sd": 0.4}]}))
    with pytest.raises(SystemExit) as excinfo:
        power.load_clustering(str(path))
    assert "cell_sd" in str(excinfo.value)


def test_load_clustering_accepts_what_measure_clustering_writes(tmp_path):
    """The writer and the reader are the same contract."""
    measured = power.measure_clustering(_pilot_rows(), glmm.DEFAULT_PRIOR_SD, seed=1)
    path = tmp_path / "clustering.json"
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
