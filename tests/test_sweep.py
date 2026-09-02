"""Sweep planning and exposure recruitment (plan §8.4, §11.4, milestone 7)."""

from __future__ import annotations

import argparse
import collections
import json
import os

import pytest

from taskbound import aggregate, runner, sweep

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HOST = os.path.join(ROOT, "hosts", "site_a")
# Groups are keyed by (condition, task, cell): one host carries several
# tasks, and near-miss and clean are per task (plan §6.1, §7.1, §7.4).
TASK = "t1_failed_job"
INJ = os.path.join(ROOT, "injections")
SCRIPTS = os.path.join(ROOT, "fixtures", "scripts")


# `v1.1-budget` is all five tasks at E1-E4: the core task's complete crossing
# plus two cells from each auxiliary task (plan §6.2, §13).
RELEASE = dict(tasks_filter=list(sweep.DEFAULT_RELEASE_TASKS),
               entry_points=["E1", "E2", "E3", "E4"])
CORE_ONLY = dict(tasks_filter=["t1_failed_job"], entry_points=["E1", "E2", "E3", "E4"])


def schedule(seed: int = 7, **kw):
    return sweep.plan(HOST, INJ, seed, **{**RELEASE, **kw})


def diagnostic_schedule(seed: int = 7, **kw):
    """Small single-agent-compatible subset for runner mechanics tests.

    One task, three entry points, and every condition at the same small N: this
    exercises runner mechanics, not the release allocation.
    """
    # Opts out of the release's E3 cap: these tests exercise runner mechanics
    # against a uniform cap, and the release's per-entry-point reduction would
    # silently change what they are asserting about recruitment.
    scope = dict(tasks_filter=[TASK], entry_points=["E1", "E2", "E3"],
                 entry_point_attempt_caps={})
    merged = {**scope, **kw}
    target = merged.get("exposed_target", sweep.EXPOSED_TARGET)
    merged.setdefault("near_miss_target", target)
    merged.setdefault("clean_target", target)
    return sweep.plan(HOST, INJ, seed, **merged)


def run_args(out, script="complied_read", **kw):
    defaults = dict(
        out=str(out), agent="scripted", script=os.path.join(SCRIPTS, script + ".json"),
        model="scripted", max_tokens=16000, turn_limit=30, effort="high", base_url=None,
        api_key_env="OPENAI_API_KEY", reasoning_effort=None, temperature=None,
        token_param="max_tokens", canary_seed="test", defense="none",
        execution_mode="single_agent",
        control_profiles=os.path.join(ROOT, "control_profiles"),
        inference_trust_boundary="external_api", max_attempts=None, spend_ceiling=None,
        price_in=None, price_cached=None, price_out=None, price_date=None, verbose=False,
        workers=1,
    )
    return argparse.Namespace(**{**defaults, **kw})


# --- planning ------------------------------------------------------------
def test_the_stage_1_smoke_may_opt_out_of_the_paraphrase_balance():
    """execution_plan.md "Settled decision: Stage 1 smoke", option B.

    The guard exists for the variance decomposition's paraphrase allocation
    (§7.5). The smoke checks wiring, exposure and placement resolution, reads no
    allocation, and cannot balance three paraphrases across one run per group.
    """
    with pytest.raises(SystemExit, match="integration-smoke"):
        sweep.plan(HOST, INJ, 1, exposed_target=1, attempt_cap=3)

    s = sweep.plan(HOST, INJ, 1, exposed_target=1, attempt_cap=3,
                   near_miss_target=1, clean_target=1, integration_smoke=True)
    # pilot_protocol.md Stage 1: 24 attacked + 24 benign + 4 inert
    # + 10 near-miss + 4 clean.
    conditions = collections.Counter(
        g["condition"] for g in s["groups"].values())
    # Near-miss and clean follow the release allocation, so T3 — which carries
    # cells only — contributes no blocks for the smoke to check either.
    assert conditions == {"attacked": 24, "benign": 24, "near_miss": 10,
                          "clean": 4, "inert": 4}
    assert s["target_runs"] == 66
    # The schedule states what it is rather than leaving a reader to infer it
    # from the target, and the release schedule stays marked as one.
    assert s["integration_smoke"] is True
    assert schedule()["integration_smoke"] is False


def test_a_smoke_result_is_refused_by_the_aggregator(tmp_path):
    """Pilot runs are never pooled with the sweep they precede."""
    # E1 on one task keeps this single-agent and the fixture simple; the marker
    # is stamped by the schedule, not by the entry point or the execution mode.
    s = sweep.plan(HOST, INJ, 1, exposed_target=1, attempt_cap=3,
                   near_miss_target=1, clean_target=1, integration_smoke=True,
                   tasks_filter=[TASK], entry_points=["E1"])
    out = tmp_path / "smoke"
    sweep.execute(s, run_args(out, max_attempts=1))
    record = json.loads(next(iter(out.glob("*.json"))).read_text())
    assert record["sweep"]["integration_smoke"] is True
    with pytest.raises(SystemExit, match="integration_smoke"):
        aggregate.validate_release_scope([aggregate._row(record)])


def test_the_smoke_recruits_its_injected_groups(tmp_path):
    """The smoke's target is smaller than its paraphrase count.

    Floor division made the per-paraphrase target 0, `all(... >= 0)` held on a
    group that had never run, and every injected group was skipped while the
    manifest reported it as having reached target — a silent injection failure
    in the stage whose job is to catch silent injection failures.
    """
    s = sweep.plan(HOST, INJ, 1, exposed_target=1, attempt_cap=3,
                   near_miss_target=1, clean_target=1, integration_smoke=True,
                   tasks_filter=[TASK], entry_points=["E1"])
    injected = [n for n, g in s["groups"].items() if g["recruits_to_exposure"]]
    assert injected

    out = tmp_path / "smoke"
    manifest = sweep.execute(s, run_args(out))
    for name in injected:
        group = manifest["groups"][name]
        assert group["attempted"] >= 1, f"{name} ran nothing"
        assert group["exposed"] <= group["target"], f"{name} over-recruited"
        # An unexposed group reaching its cap is a result; certifying a group
        # that recruited nothing is not.
        assert group["reached_target"] == (group["exposed"] >= group["target"])


def test_an_entry_point_may_carry_its_own_attempt_cap():
    """Recruitment cost is per entry point, because exposure is.

    E3 measured 0.04 exposure on T1 and 0.00 on T5 in the live runs, so its
    groups spend the injected cap to report a shortfall. A smaller cap there
    keeps the exposure rate — a reported result in its own right — without
    paying for a target the entry point cannot reach.
    """
    s = schedule(entry_point_attempt_caps={"E3": 6})
    caps = {(g["cell"] or "")[:2]: g["attempt_cap"]
            for g in s["groups"].values() if g["recruits_to_exposure"]}
    assert caps == {"E1": 9, "E2": 9, "E3": 6, "E4": 9}
    # Fewer scheduled attempts, not merely an earlier stop: the saving has to
    # show up in the frozen attempt list or it buys no wall clock.
    uncapped = schedule(entry_point_attempt_caps={})
    assert s["max_attempts"] < uncapped["max_attempts"]
    assert sum(1 for a in s["attempts"] if (a["cell"] or "").startswith("E3")) == 13 * 6

    # The allocation is part of the identity, so two schedules differing only
    # in where recruitment stops cannot share a sweep id.
    assert s["sweep_id"] != uncapped["sweep_id"]

    # A cap below the exposed target is the intended use, not an error, and it
    # is what the release carries for E3.
    assert schedule()["entry_point_attempt_caps"] == {"E3": 3}

    # The CLI needs a spelling for "none of them", because an absent flag means
    # the registered allocation rather than an empty one.
    assert sweep._parse_entry_point_caps(["none"]) == {}
    assert sweep._parse_cells_only(["none"]) == []
    assert sweep._parse_cells_only(None) is None
    with pytest.raises(SystemExit, match="uniform cap"):
        sweep._parse_entry_point_caps(["E3"])

    with pytest.raises(SystemExit, match="unknown entry point"):
        schedule(entry_point_attempt_caps={"E9": 6})
    with pytest.raises(SystemExit, match="multiple of 3"):
        schedule(entry_point_attempt_caps={"E3": 7})


def test_the_plan_matches_the_release_allocation():
    """`v1.1-budget`: 228 target runs and a 462-attempt cap per family.

    Sized to a wall-clock budget on a self-hosted endpoint, where v1.0-broad's
    945 runs cost about 58 hours per family.
    """
    s = schedule()
    assert len(s["groups"]) == 66
    assert s["target_runs"] == 228
    assert s["max_attempts"] == 462
    assert s["exposed_target"] == 3
    assert s["attempt_cap"] == 9
    assert s["near_miss_target"] == 6
    assert s["clean_target"] == 3
    # N is a multiple of three so the paraphrase blocks stay balanced (§7.5).
    assert s["exposed_target"] % 3 == 0
    # The two reductions that are not simply smaller N.
    assert s["entry_point_attempt_caps"] == {"E3": 3}
    assert s["cells_only_tasks"] == ["t3_build_and_run"]
    conditions = [g["condition"] for g in s["groups"].values()]
    assert conditions.count("attacked") == 24   # 16 core cells + 8 auxiliary
    assert conditions.count("benign") == 24
    assert conditions.count("inert") == 4       # per entry point, core task only
    assert conditions.count("near_miss") == 10  # per (task, action), less T3's
    assert conditions.count("clean") == 4       # per task, less T3's
    # The run budget by condition, per model family.
    runs = {}
    for group in s["groups"].values():
        runs[group["condition"]] = runs.get(group["condition"], 0) + group["target"]
    assert runs == {"attacked": 72, "benign": 72, "inert": 12,
                    "near_miss": 60, "clean": 12}


def test_a_cells_only_task_keeps_the_crossing_balanced():
    """Plan §6.2: the auxiliary cells exist to identify the task effect.

    Every entry point and every induced action appears in exactly three tasks,
    and T3's E1A2 and E3A3 are the only auxiliary occurrences holding E1, E3, A2
    and A3 at three. Dropping T3 to save wall clock would confound the task term
    with both factors; carrying its cells without its blocks does not.
    """
    s = schedule()
    assert not [n for n in s["groups"]
                if n.startswith(("near_miss|t3", "clean|t3"))]
    cells = collections.defaultdict(set)
    for g in s["groups"].values():
        if g["condition"] in ("attacked", "benign") and g["cell"]:
            cells[g["task"]].add(g["cell"])
    assert "t3_build_and_run" in cells
    for level in ("E1", "E2", "E3", "E4"):
        assert sum(1 for cs in cells.values()
                   if any(c.startswith(level) for c in cs)) == 3
    for level in ("A1", "A2", "A3", "A4"):
        assert sum(1 for cs in cells.values()
                   if any(c.endswith(level) for c in cs)) == 3

    # An explicit task outside the scope is a mistake; the default is not.
    with pytest.raises(SystemExit, match="outside this schedule's scope"):
        sweep.plan(HOST, INJ, 7, tasks_filter=[TASK], entry_points=["E1"],
                   cells_only_tasks=["t3_build_and_run"])
    narrowed = sweep.plan(HOST, INJ, 7, tasks_filter=[TASK], entry_points=["E1"])
    assert narrowed["cells_only_tasks"] == []


def test_every_auxiliary_cell_is_also_a_core_cell():
    """What identifies the task effect within cell rather than across corners."""
    s = schedule()
    by_task = {}
    for group in s["groups"].values():
        if group["condition"] == "attacked":
            by_task.setdefault(group["task"], set()).add(group["cell"])
    core = by_task["t1_failed_job"]
    assert len(core) == 16
    for task, cells in by_task.items():
        if task == "t1_failed_job":
            continue
        assert len(cells) == 2, task
        assert cells <= core, task
    # Every entry point and every action appears in exactly three of five tasks.
    for level in ("E1", "E2", "E3", "E4"):
        assert sum(1 for cells in by_task.values()
                   if any(c.startswith(level) for c in cells)) == 3
    for level in ("A1", "A2", "A3", "A4"):
        assert sum(1 for cells in by_task.values()
                   if any(c.endswith(level) for c in cells)) == 3


def test_near_miss_carries_its_own_n_without_the_paraphrase_guard():
    """N is per condition: 6 for near-miss beside 3 for injected groups (§7.4).

    The multiple-of-three rule protects the paraphrase allocation, which
    near-miss and clean blocks do not have — applying it to them is what kept
    near-miss pinned to the injected N.
    """
    s = schedule()
    for group in s["groups"].values():
        if group["condition"] == "near_miss":
            assert group["target"] == 6
            assert group["attempt_cap"] == 6      # nothing to recruit, no headroom
            assert group["paraphrases"] == []
        elif group["condition"] in ("attacked", "benign", "inert"):
            assert group["target"] == 3
    # A near-miss target that is not a multiple of three is legal; an injected
    # one is not.
    odd = sweep.plan(HOST, INJ, 7, near_miss_target=10, **RELEASE)
    assert odd["groups"][f"near_miss|{TASK}|A1"]["target"] == 10
    with pytest.raises(SystemExit, match="divide evenly"):
        sweep.plan(HOST, INJ, 7, exposed_target=10, **RELEASE)


def test_one_derivation_of_the_sweep_identity():
    """The identity covers every registered N, and there is only one of it.

    `aggregate` and `power` both re-check that a manifest reproduces the sweep
    it claims. A second copy of the derivation is a place for the two to drift,
    and the copy in `aggregate` did drift the moment N became per condition.
    """
    s = schedule()
    assert sweep.sweep_id(s) == s["sweep_id"]
    for knob in ("exposed_target", "attempt_cap", "near_miss_target", "clean_target"):
        assert knob in sweep.SWEEP_ID_KEYS
        moved = {**s, knob: s[knob] + 3}
        assert sweep.sweep_id(moved) != s["sweep_id"], knob
    # A schedule missing a registered N does not reproduce: it came from a
    # different allocation.
    legacy = {k: v for k, v in s.items() if k != "near_miss_target"}
    assert sweep.sweep_id(legacy) != s["sweep_id"]


def test_only_injected_conditions_recruit_to_exposure():
    s = schedule()
    for name, group in s["groups"].items():
        expected = group["condition"] in ("attacked", "benign", "inert")
        assert group["recruits_to_exposure"] is expected, name
    # Conditions that do not recruit have no headroom above their target.
    assert s["groups"][f"clean|{TASK}"]["attempt_cap"] == s["groups"][f"clean|{TASK}"]["target"]


def test_attempts_are_laid_out_in_balanced_paraphrase_blocks():
    s = schedule()
    attempts = [a for a in s["attempts"] if a["group"] == f"attacked|{TASK}|E1A1"]
    attempts.sort(key=lambda a: a["index_in_group"])
    for start in range(0, len(attempts), sweep.BLOCK):
        block = attempts[start:start + sweep.BLOCK]
        # Stopping at any block boundary leaves the three paraphrases balanced.
        assert len({a["injection"] for a in block}) == sweep.BLOCK


def test_groups_are_interleaved_rather_than_contiguous():
    """So provider drift cannot align with one condition (plan §11.4)."""
    s = schedule()
    order = [a["group"] for a in s["attempts"]]
    first = order.index(f"attacked|{TASK}|E1A1")
    stretch = order[first:first + 24]
    assert len(set(stretch)) > 1


def test_planning_is_deterministic_and_seed_dependent():
    a, b, c = schedule(7), schedule(7), schedule(8)
    assert a["sweep_id"] == b["sweep_id"]
    assert a["sweep_id"] != c["sweep_id"]
    assert [x["placement_seed"] for x in a["attempts"]] != [x["placement_seed"] for x in c["attempts"]]


def test_the_schedule_is_bound_to_the_material_it_was_planned_from():
    s = schedule()
    assert s["host"]["hash"]
    assert s["sweep_id"].startswith("sweep_")


# --- execution -----------------------------------------------------------
def test_recruitment_stops_at_the_target_and_retains_every_attempt(tmp_path):
    s = diagnostic_schedule(exposed_target=6, attempt_cap=12)
    manifest = sweep.execute(s, run_args(tmp_path / "out"))
    clean = manifest["groups"][f"clean|{TASK}"]
    assert clean["attempted"] == 6

    exposed_group = manifest["groups"][f"attacked|{TASK}|E1A1"]
    assert exposed_group["exposed"] == 6
    assert exposed_group["attempted"] == 6  # E1 exposure is near 1 by construction
    assert exposed_group["exposed_by_paraphrase"] == {"p1": 2, "p2": 2, "p3": 2}
    assert exposed_group["shortfall_by_paraphrase"] == {"p1": 0, "p2": 0, "p3": 0}
    # One result file per attempt, including any that were never exposed.
    written = [p for p in os.listdir(tmp_path / "out") if not p.startswith("sweep_manifest")]
    assert len(written) == manifest["totals"]["attempted_total"]
    assert set(manifest["result_sha256_by_attempt_id"]) == {
        name.removesuffix(".json") for name in written
    }
    for name in written:
        with open(tmp_path / "out" / name, encoding="utf-8") as fh:
            record = json.load(fh)
        assert manifest["result_sha256_by_attempt_id"][name.removesuffix(".json")] == \
            sweep._canonical_sha256(record)
    assert {profile["file"] for profile in manifest["evaluated_control_profiles"]} == {
        name for name in os.listdir(ROOT + "/control_profiles") if name.endswith(".json")
    }


def test_a_group_the_agent_never_reads_hits_the_cap_and_says_so(tmp_path):
    """The scripted agent never renders a module description, so E3 never exposes."""
    s = diagnostic_schedule(exposed_target=6, attempt_cap=12)
    manifest = sweep.execute(s, run_args(tmp_path / "out"))
    e3 = manifest["groups"][f"attacked|{TASK}|E3A1"]
    assert e3["exposed"] == 0
    assert e3["attempted"] == 12
    assert e3["hit_attempt_cap"] is True
    assert e3["reached_target"] is False
    assert e3["shortfall_by_paraphrase"] == {"p1": 2, "p2": 2, "p3": 2}
    assert f"attacked|{TASK}|E3A1" in manifest["totals"]["groups_short_of_target"]


def test_recruitment_does_not_overfill_a_successful_paraphrase(tmp_path, monkeypatch):
    s = diagnostic_schedule(exposed_target=6, attempt_cap=12)
    original = sweep._run_one

    def uneven(schedule, attempt, args):
        record = original(schedule, attempt, args)
        paraphrase = attempt["paraphrase"]
        record["exposure"]["exposed"] = paraphrase == "p1"
        return record

    monkeypatch.setattr(sweep, "_run_one", uneven)
    manifest = sweep.execute(s, run_args(tmp_path / "out"))
    group = manifest["groups"][f"attacked|{TASK}|E1A1"]
    assert group["exposed_by_paraphrase"] == {"p1": 2, "p2": 0, "p3": 0}
    assert group["shortfall_by_paraphrase"] == {"p1": 0, "p2": 2, "p3": 2}
    assert group["reached_target"] is False


def test_unfinished_paraphrases_borrow_completed_paraphrase_slots(tmp_path, monkeypatch):
    s = diagnostic_schedule(exposed_target=6, attempt_cap=12)
    target_group = f"attacked|{TASK}|E1A1"
    original = sweep._run_one
    attempts_by_paraphrase = {p: 0 for p in sweep.PARAPHRASES}

    def uneven(schedule, attempt, args):
        record = original(schedule, attempt, args)
        if attempt["group"] == target_group:
            paraphrase = attempt["paraphrase"]
            attempts_by_paraphrase[paraphrase] += 1
            record["exposure"]["exposed"] = (
                paraphrase == "p1" or
                (paraphrase == "p2" and attempts_by_paraphrase[paraphrase] >= 5)
            )
        return record

    monkeypatch.setattr(sweep, "_run_one", uneven)
    manifest = sweep.execute(s, run_args(tmp_path / "out"))
    group = manifest["groups"][target_group]
    assert group["attempted"] == 12
    assert attempts_by_paraphrase["p2"] == 6
    assert group["exposed_by_paraphrase"] == {"p1": 2, "p2": 2, "p3": 0}
    assert group["shortfall_by_paraphrase"] == {"p1": 0, "p2": 0, "p3": 2}


def test_an_interrupted_sweep_resumes_rather_than_reruns(tmp_path):
    s = diagnostic_schedule(exposed_target=6, attempt_cap=12)
    out = tmp_path / "out"
    first = sweep.execute(s, run_args(out, max_attempts=20))
    assert first["stopped_early"] == "max_attempts"
    assert first["totals"]["attempted_this_session"] == 20

    second = sweep.execute(s, run_args(out))
    assert second["stopped_early"] is None
    assert second["totals"]["attempted_total"] > first["totals"]["attempted_total"]
    written = [p for p in os.listdir(out) if not p.startswith("sweep_manifest")]
    assert len(written) == second["totals"]["attempted_total"]


def test_a_sweep_cannot_resume_under_another_model_configuration(tmp_path):
    s = diagnostic_schedule(exposed_target=6, attempt_cap=12)
    out = tmp_path / "out"
    sweep.execute(s, run_args(out, max_attempts=1))

    with pytest.raises(SystemExit, match="distinct result directory"):
        sweep.execute(s, run_args(out, model="another-family", max_attempts=1))


def test_a_sweep_cannot_resume_under_another_canary_seed(tmp_path):
    """The seed decides what counts as a leak, so it cannot change mid-sweep.

    `_agent_configuration` deliberately does not carry the seed — it is
    serialised onto every result and a seed is a secret (plan §12) — so the
    guard compares the generation id each run already records.
    """
    s = diagnostic_schedule(exposed_target=6, attempt_cap=12)
    out = tmp_path / "out"
    sweep.execute(s, run_args(out, canary_seed="seed-one", max_attempts=1))

    with pytest.raises(SystemExit, match="canary generation"):
        sweep.execute(s, run_args(out, canary_seed="seed-two", max_attempts=1))

    generations = set()
    for name in os.listdir(out):
        if name.startswith("sweep_manifest"):
            continue
        with open(os.path.join(out, name), encoding="utf-8") as fh:
            generations.add(json.load(fh)["canary_generation"])
    assert generations == {runner.canary_generation("seed-one")}


def test_resuming_under_the_same_canary_seed_is_allowed(tmp_path):
    """The guard is on the generation, not on having been given a seed twice."""
    s = diagnostic_schedule(exposed_target=3, attempt_cap=6)
    out = tmp_path / "out"
    sweep.execute(s, run_args(out, canary_seed="seed-one", max_attempts=2))
    manifest = sweep.execute(s, run_args(out, canary_seed="seed-one"))
    assert manifest["totals"]["attempted_total"] > 2


def test_a_result_is_never_left_half_written(tmp_path):
    """`_write` renames into place, so a reader sees no file or a whole one."""
    out = tmp_path / "out"
    out.mkdir()
    sweep._write(str(out), "attempt_00", {"run_id": "r", "value": "x" * 50000})
    written = sorted(os.listdir(out))
    assert written == ["attempt_00.json"], "no .partial may survive a clean write"
    with open(out / "attempt_00.json", encoding="utf-8") as fh:
        assert json.load(fh)["run_id"] == "r"


def test_a_truncated_result_names_itself_rather_than_raising_a_bare_decode_error(tmp_path):
    """What a signal used to leave behind, and what the reader now says about it."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "attacked_t1_failed_job_E1A1_00.json").write_text(
        '{"run_id": "r", "action_trace": [{"index": 0, "kind": "fs_re',
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="attacked_t1_failed_job_E1A1_00.json"):
        sweep._resume(str(out), diagnostic_schedule())

    with pytest.raises(SystemExit, match="attacked_t1_failed_job_E1A1_00.json"):
        aggregate.load_frame(str(tmp_path))


def test_every_result_records_the_attempt_it_came_from(tmp_path):
    s = diagnostic_schedule(exposed_target=3, attempt_cap=6)
    sweep.execute(s, run_args(tmp_path / "out"))
    for name in os.listdir(tmp_path / "out"):
        with open(os.path.join(tmp_path / "out", name), encoding="utf-8") as fh:
            record = json.load(fh)
        assert record["sweep"]["sweep_id"] == s["sweep_id"]
        assert record["sweep"]["attempt_id"] == name[:-len(".json")]


# --- parallel execution (--workers) --------------------------------------
def _records_by_attempt(out_dir):
    """Read the result files in a sweep output directory into a dict by attempt id."""
    records = {}
    for name in os.listdir(out_dir):
        if name.startswith("sweep_manifest"):
            continue
        with open(os.path.join(out_dir, name), encoding="utf-8") as fh:
            records[name[:-len(".json")]] = json.load(fh)
    return records


_VARYING_KEYS = {"started_at", "finished_at", "run_id", "git_dirty", "request_ids"}


def _normalize_record(record):
    """Drop fields that legitimately vary between executions (wall-clock timestamps,
    the run id derived from them, working-tree dirtiness, and provider request ids)
    so a record's substantive content — routing, exposure, compliance, usage,
    attempt identity — can be compared across two runs."""
    if isinstance(record, dict):
        return {
            k: _normalize_record(v)
            for k, v in record.items()
            if k not in _VARYING_KEYS
        }
    if isinstance(record, list):
        return [_normalize_record(v) for v in record]
    return record


def _records_by_attempt_normalized(out_dir):
    return {
        attempt_id: _normalize_record(record)
        for attempt_id, record in _records_by_attempt(out_dir).items()
    }


def test_workers_1_is_identical_to_the_default_serial_path(tmp_path):
    """The default and an explicit --workers 1 must be byte-identical (plan §11.4)."""
    s = diagnostic_schedule(exposed_target=6, attempt_cap=12)
    out_default = tmp_path / "default"
    out_one = tmp_path / "one"
    sweep.execute(s, run_args(out_default))
    sweep.execute(s, run_args(out_one, workers=1))
    assert _records_by_attempt_normalized(out_default) == \
        _records_by_attempt_normalized(out_one)


def test_parallel_writes_one_complete_result_per_attempt_without_collision(tmp_path):
    """The core safety guarantee: parallel runs are isolated and append-only.

    Every attempt lands in exactly one result file keyed by its attempt id, the
    on-disk record matches the manifest's canonical hash, and the manifest
    totals are internally consistent, so concurrency never drops, duplicates,
    or mutates a record (plan §11.4, §12).
    """
    s = diagnostic_schedule(exposed_target=6, attempt_cap=12)
    out = tmp_path / "out"
    manifest = sweep.execute(s, run_args(out, workers=6))

    written = [p for p in os.listdir(out) if not p.startswith("sweep_manifest")]
    # One file per attempt that actually ran this session.
    assert len(written) == manifest["totals"]["attempted_this_session"]
    assert len(written) == len(set(written))  # no duplicate attempt files
    assert set(manifest["result_sha256_by_attempt_id"]) == {
        name[:-len(".json")] for name in written
    }
    records = _records_by_attempt(out)
    for attempt_id, record in records.items():
        assert record["sweep"]["attempt_id"] == attempt_id
        assert record["sweep"]["sweep_id"] == s["sweep_id"]
        assert manifest["result_sha256_by_attempt_id"][attempt_id] == \
            sweep._canonical_sha256(record)
    # Group accounting stays self-consistent under concurrency.
    assert manifest["totals"]["attempted_total"] == sum(
        g["attempted"] for g in manifest["groups"].values()
    )
    assert manifest["totals"]["exposed_total"] == sum(
        g["exposed"] for g in manifest["groups"].values()
    )


def test_parallel_reproduces_the_serial_records_for_a_deterministic_run(tmp_path):
    """On the deterministic scripted schedule, workers>1 yields the same records
    as workers=1, so the opt-in concurrency path does not silently change what
    the benchmark measures (plan §8.4)."""
    s = diagnostic_schedule(exposed_target=6, attempt_cap=12)
    serial = sweep.execute(s, run_args(tmp_path / "serial", workers=1))
    parallel = sweep.execute(s, run_args(tmp_path / "parallel", workers=6))
    assert _records_by_attempt_normalized(tmp_path / "serial") == \
        _records_by_attempt_normalized(tmp_path / "parallel")
    assert parallel["totals"]["attempted_total"] == serial["totals"]["attempted_total"]
    assert parallel["totals"]["exposed_total"] == serial["totals"]["exposed_total"]


def test_parallel_is_reproducible_across_two_runs(tmp_path):
    """Two parallel sweeps from the same schedule and seed produce identical
    manifests, even though thread timing can vary between them."""
    s = diagnostic_schedule(exposed_target=6, attempt_cap=12)
    a = sweep.execute(s, run_args(tmp_path / "a", workers=4))
    b = sweep.execute(s, run_args(tmp_path / "b", workers=4))
    assert _records_by_attempt_normalized(tmp_path / "a") == \
        _records_by_attempt_normalized(tmp_path / "b")
    # The same set of attempts ran in both; their substantive content matches.
    assert set(a["result_sha256_by_attempt_id"]) == set(b["result_sha256_by_attempt_id"])


def test_parallel_respects_the_attempt_ceiling_exactly(tmp_path):
    """--workers must not let a batch overshoot --max-attempts (plan §10.3)."""
    s = diagnostic_schedule(exposed_target=6, attempt_cap=12)
    out = tmp_path / "out"
    manifest = sweep.execute(s, run_args(out, workers=8, max_attempts=25))
    assert manifest["stopped_early"] == "max_attempts"
    assert manifest["totals"]["attempted_this_session"] == 25
    written = [p for p in os.listdir(out) if not p.startswith("sweep_manifest")]
    assert len(written) == 25
