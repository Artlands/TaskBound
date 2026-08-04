"""H1 host-authoring acceptance checks (plan §11.3, milestone 3).

The core host must supply, for every cell it declares, a target satisfying the
central invariant, a matched in-scope target, a near-miss task whose policy
widens the *layer that action lives in*, and — for A3 — a manifest twin.
"""

from __future__ import annotations

import json
import os

from taskbound.policy import Policy
from taskbound.validate import invariant_holds

ROOT = os.path.join(os.path.dirname(__file__), "..")
HOST_DIR = os.path.join(ROOT, "hosts", "h1_failed_job")


def host() -> dict:
    with open(os.path.join(HOST_DIR, "host.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_h1_carries_the_twelve_v05_cells():
    cells = host()["cells"]
    assert set(cells) == {f"E{e}A{a}" for e in (1, 2, 3) for a in (1, 2, 3, 4)}
    assert len(cells) == 12


def test_every_action_target_satisfies_the_invariant_and_its_benign_mirror():
    h = host()
    policy = Policy.from_dict(h["policy"])
    for action, spec in h["action_targets"].items():
        account, task, what = invariant_holds(policy, spec["compliance_predicate"])
        assert account, f"{action}: {what} is not account-permitted (off-thesis)"
        assert not task, f"{action}: {what} is inside task scope (off-thesis)"
        account, task, what = invariant_holds(policy, spec["benign_predicate"])
        assert account and task, f"{action}: benign {what} is not an in-scope mirror"


def test_a3_crosses_a_state_boundary_and_not_a_path_boundary():
    """The A3 edge case: the path is in scope, the value written there is not."""
    h = host()
    policy = Policy.from_dict(h["policy"])
    target = h["action_targets"]["A3"]["compliance_predicate"]
    assert policy.task_allows_path(target["path"])  # params.json is project_a's
    _, task_ok, _ = invariant_holds(policy, target)
    assert task_ok is False  # but the declared value violates the state constraint


def test_a1_and_a4_are_told_apart_by_marker_not_by_path():
    h = host()
    assert h["action_targets"]["A1"]["canary_slot"] in h["policy"]["canary_slots"]
    assert h["action_targets"]["A4"]["marker_slot"]
    # Both instruments live under the same shared scratch root, which is exactly
    # why the two ladders are separated by which value the bytes carry (plan §5.2).
    egress = h["oracle"]["egress_paths"][0]
    assert h["action_targets"]["A4"]["compliance_predicate"]["path"].startswith(egress)


def test_each_near_miss_widens_the_layer_its_action_crosses():
    h = host()
    base = Policy.from_dict(h["policy"])
    for action, spec in h["action_targets"].items():
        widened = base.widened(h["near_miss_policies"][action])
        _, task_ok, what = invariant_holds(widened, spec["compliance_predicate"])
        assert task_ok, f"{action}: near-miss policy does not make {what} in scope"
        assert os.path.isfile(os.path.join(HOST_DIR, h["tasks"][f"near_miss_{action}"]))


def test_the_a3_near_miss_ships_its_own_manifest():
    h = host()
    with open(os.path.join(HOST_DIR, h["manifests"]["default"]), encoding="utf-8") as fh:
        default = {p["selector"]: p["value"] for p in json.load(fh)["parameters"]}
    with open(os.path.join(HOST_DIR, h["manifests"]["near_miss_A3"]), encoding="utf-8") as fh:
        near_miss = {p["selector"]: p["value"] for p in json.load(fh)["parameters"]}
    target = h["action_targets"]["A3"]["compliance_predicate"]
    # Scoring the near-miss against the default manifest would count correct
    # work as an integrity violation (plan §7.4).
    assert default[target["selector"]] != target["value"]
    assert near_miss[target["selector"]] == target["value"]


def test_every_vehicle_is_present_and_clean_in_the_base_workspace():
    """R1: a run differs by which vehicle carries text, never by whether it exists."""
    h = host()
    for name, spec in h["placement_classes"].items():
        rel = spec["file"].replace("/workspace/", "")
        path = os.path.join(HOST_DIR, "workspace", rel)
        assert os.path.isfile(path), f"{name}: vehicle {spec['file']} is missing"
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        assert "post-mortem-helper" not in body, f"{name}: vehicle is not clean"
