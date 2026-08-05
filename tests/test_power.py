"""The power gate (plan §9.5, milestone 8).

The gate itself is a long-running simulation and is not run here. What is
checked here is that the thing it simulates is the design: the exact
allocation, recruitment included, and the same fit the aggregator uses.
"""

from __future__ import annotations

from taskbound import power


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
