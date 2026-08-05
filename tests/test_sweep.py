"""Sweep planning and exposure recruitment (plan §8.4, §11.4, milestone 7)."""

from __future__ import annotations

import argparse
import json
import os

from taskbound import sweep

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HOST = os.path.join(ROOT, "hosts", "h1_failed_job")
INJ = os.path.join(ROOT, "injections")
SCRIPTS = os.path.join(ROOT, "fixtures", "scripts")


def schedule(seed: int = 7, **kw):
    return sweep.plan(HOST, INJ, seed, **kw)


def run_args(out, script="complied_read", **kw):
    defaults = dict(
        out=str(out), agent="scripted", script=os.path.join(SCRIPTS, script + ".json"),
        model="scripted", max_tokens=16000, turn_limit=30, effort="high", base_url=None,
        api_key_env="OPENAI_API_KEY", reasoning_effort=None, temperature=None,
        token_param="max_tokens", canary_seed="test", defense="none",
        control_profiles=os.path.join(ROOT, "control_profiles"),
        inference_trust_boundary="external_api", max_attempts=None, spend_ceiling=None,
        price_in=None, price_cached=None, price_out=None, price_date=None, verbose=False,
    )
    return argparse.Namespace(**{**defaults, **kw})


# --- planning ------------------------------------------------------------
def test_the_plan_matches_the_v05_allocation():
    """Plan §10.1: 768 target runs, 768-1,416 attempts, per model family."""
    s = schedule()
    assert s["target_runs"] == 768
    assert s["max_attempts"] == 1416
    conditions = [g["condition"] for g in s["groups"].values()]
    assert conditions.count("attacked") == 12
    assert conditions.count("benign") == 12
    assert conditions.count("inert") == 3      # per entry point, not per cell
    assert conditions.count("near_miss") == 4  # per induced action
    assert conditions.count("clean") == 1


def test_only_injected_conditions_recruit_to_exposure():
    s = schedule()
    for name, group in s["groups"].items():
        expected = group["condition"] in ("attacked", "benign", "inert")
        assert group["recruits_to_exposure"] is expected, name
    # Conditions that do not recruit have no headroom above their target.
    assert s["groups"]["clean|host"]["attempt_cap"] == s["groups"]["clean|host"]["target"]


def test_attempts_are_laid_out_in_balanced_paraphrase_blocks():
    s = schedule()
    attempts = [a for a in s["attempts"] if a["group"] == "attacked|E1A1"]
    attempts.sort(key=lambda a: a["index_in_group"])
    for start in range(0, len(attempts), sweep.BLOCK):
        block = attempts[start:start + sweep.BLOCK]
        # Stopping at any block boundary leaves the three paraphrases balanced.
        assert len({a["injection"] for a in block}) == sweep.BLOCK


def test_groups_are_interleaved_rather_than_contiguous():
    """So provider drift cannot align with one condition (plan §11.4)."""
    s = schedule()
    order = [a["group"] for a in s["attempts"]]
    first = order.index("attacked|E1A1")
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
    s = schedule(exposed_target=6, attempt_cap=12)
    manifest = sweep.execute(s, run_args(tmp_path / "out"))
    clean = manifest["groups"]["clean|host"]
    assert clean["attempted"] == 6

    exposed_group = manifest["groups"]["attacked|E1A1"]
    assert exposed_group["exposed"] == 6
    assert exposed_group["attempted"] == 6  # E1 exposure is near 1 by construction
    # One result file per attempt, including any that were never exposed.
    written = [p for p in os.listdir(tmp_path / "out") if not p.startswith("sweep_manifest")]
    assert len(written) == manifest["totals"]["attempted_total"]


def test_a_group_the_agent_never_reads_hits_the_cap_and_says_so(tmp_path):
    """The scripted agent never renders a module description, so E3 never exposes."""
    s = schedule(exposed_target=6, attempt_cap=12)
    manifest = sweep.execute(s, run_args(tmp_path / "out"))
    e3 = manifest["groups"]["attacked|E3A1"]
    assert e3["exposed"] == 0
    assert e3["attempted"] == 12
    assert e3["hit_attempt_cap"] is True
    assert e3["reached_target"] is False
    assert "attacked|E3A1" in manifest["totals"]["groups_short_of_target"]


def test_an_interrupted_sweep_resumes_rather_than_reruns(tmp_path):
    s = schedule(exposed_target=6, attempt_cap=12)
    out = tmp_path / "out"
    first = sweep.execute(s, run_args(out, max_attempts=20))
    assert first["stopped_early"] == "max_attempts"
    assert first["totals"]["attempted_this_session"] == 20

    second = sweep.execute(s, run_args(out))
    assert second["stopped_early"] is None
    assert second["totals"]["attempted_total"] > first["totals"]["attempted_total"]
    written = [p for p in os.listdir(out) if not p.startswith("sweep_manifest")]
    assert len(written) == second["totals"]["attempted_total"]


def test_every_result_records_the_attempt_it_came_from(tmp_path):
    s = schedule(exposed_target=3, attempt_cap=6)
    sweep.execute(s, run_args(tmp_path / "out"))
    for name in os.listdir(tmp_path / "out"):
        with open(os.path.join(tmp_path / "out", name), encoding="utf-8") as fh:
            record = json.load(fh)
        assert record["sweep"]["sweep_id"] == s["sweep_id"]
        assert record["sweep"]["attempt_id"] == name[:-len(".json")]
