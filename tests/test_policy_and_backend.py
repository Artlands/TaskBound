"""Runner and backend acceptance checks (plan §11.3).

A clean run isolates the workspace, records every action, refuses account
crossings, records task crossings, and replays deterministically from its seed.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from taskbound.backend import LocalSimBackend
from taskbound.inject import PlacementError, apply_injection, sample_placement
from taskbound.policy import PathEscape, Policy, resolve, under

HOST = os.path.join(os.path.dirname(__file__), "..", "hosts", "h1_failed_job")


def load_policy() -> Policy:
    with open(os.path.join(HOST, "host.json"), encoding="utf-8") as fh:
        return Policy.from_dict(json.load(fh)["policy"])


def make_backend(tmp: str) -> LocalSimBackend:
    return LocalSimBackend.materialize(HOST, os.path.join(tmp, "run"), load_policy(), {})


def test_under_matches_on_component_boundaries():
    assert under("/workspace/projects/project_a/x", "/workspace/projects/project_a")
    # A string-prefix check would wrongly accept this one.
    assert not under("/workspace/projects/project_a_backup", "/workspace/projects/project_a")


def test_path_escape_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        backend = make_backend(tmp)
        out = backend.call("read_file", {"path": "/workspace/../../etc/passwd"})
        assert "error" in out
        action = backend.actions[-1]
        assert action.outcome == "refused" and action.account_allowed is False


def test_symlink_escape_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        backend = make_backend(tmp)
        outside = os.path.join(tmp, "outside.txt")
        with open(outside, "w", encoding="utf-8") as fh:
            fh.write("secret")
        os.symlink(outside, os.path.join(backend.workspace, "logs", "link.txt"))
        backend.call("read_file", {"path": "/workspace/logs/link.txt"})
        assert backend.actions[-1].outcome == "refused"


def test_account_crossing_refused_task_crossing_recorded():
    policy = load_policy()
    with tempfile.TemporaryDirectory() as tmp:
        backend = make_backend(tmp)
        # Out of task scope, inside account scope: permitted and recorded.
        backend.call("read_file", {"path": "/workspace/projects/project_b/results/summary.csv"})
        recorded = backend.actions[-1]
        assert recorded.outcome == "ok"
        assert recorded.account_allowed is True and recorded.task_allowed is False

        # Out of account scope: refused by the backend.
        assert policy.account_allows_verb("submit")
        backend.policy.account_allowed_scheduler_actions = ["inspect"]
        backend.call("sbatch", {"script": "/workspace/projects/project_a/run_1842/job.slurm"})
        refused = backend.actions[-1]
        assert refused.outcome == "refused" and refused.account_allowed is False


def test_listing_an_ancestor_of_a_task_root_is_not_a_crossing():
    with tempfile.TemporaryDirectory() as tmp:
        backend = make_backend(tmp)
        backend.call("list_dir", {"path": "/workspace"})
        assert backend.actions[-1].task_allowed is True
        backend.call("list_dir", {"path": "/workspace/projects/project_b"})
        assert backend.actions[-1].task_allowed is False


def test_workspace_is_isolated_from_the_host_directory():
    with tempfile.TemporaryDirectory() as tmp:
        backend = make_backend(tmp)
        backend.call("write_file", {"path": "/workspace/logs/scratch.txt", "content": "x"})
        assert not os.path.exists(os.path.join(HOST, "workspace", "logs", "scratch.txt"))


def test_scheduler_state_is_deterministic():
    with tempfile.TemporaryDirectory() as tmp:
        a, b = make_backend(os.path.join(tmp, "a")), make_backend(os.path.join(tmp, "b"))
        for backend in (a, b):
            backend.call("sbatch", {"script": "/workspace/projects/project_a/run_1842/job.slurm"})
        assert a.actions[-1].result_text == b.actions[-1].result_text == "Submitted batch job 9001"


def test_placement_is_seeded_and_reproducible():
    with open(os.path.join(HOST, "host.json"), encoding="utf-8") as fh:
        classes = json.load(fh)["placement_classes"]
    with tempfile.TemporaryDirectory() as tmp:
        backend = make_backend(tmp)
        first = sample_placement(backend.workspace, classes, "e1_job_stderr", seed=7)
        second = sample_placement(backend.workspace, classes, "e1_job_stderr", seed=7)
        assert first == second
        seeds = {sample_placement(backend.workspace, classes, "e1_job_stderr", s).line
                 for s in range(40)}
        assert len(seeds) > 1, "placement should vary across attempt seeds"


def test_unresolvable_placement_fails_loudly():
    with tempfile.TemporaryDirectory() as tmp:
        backend = make_backend(tmp)
        classes = {"broken": {"file": "/workspace/logs/nope.err", "line_positions": [1]}}
        with pytest.raises(PlacementError):
            sample_placement(backend.workspace, classes, "broken", seed=1)


def test_injection_changes_only_the_sampled_vehicle():
    with open(os.path.join(HOST, "host.json"), encoding="utf-8") as fh:
        classes = json.load(fh)["placement_classes"]
    with tempfile.TemporaryDirectory() as tmp:
        backend = make_backend(tmp)
        before = _snapshot(backend.workspace)
        placement = sample_placement(backend.workspace, classes, "e1_job_stderr", seed=3)
        apply_injection(backend.workspace, placement, "injected line")
        after = _snapshot(backend.workspace)
        changed = {k for k in before if before[k] != after[k]}
        assert changed == {"logs/job_1842.err"}


def test_canary_placeholder_is_substituted_at_load_time():
    with tempfile.TemporaryDirectory() as tmp:
        backend = LocalSimBackend.materialize(
            HOST, os.path.join(tmp, "run"), load_policy(),
            {"{{canary:project_b_summary}}": "TB-CANARY-TEST"},
        )
        body = backend.call("read_file", {"path": "/workspace/projects/project_b/results/summary.csv"})
        assert "TB-CANARY-TEST" in body and "{{canary:" not in body


def _snapshot(root: str) -> dict[str, str]:
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8", errors="replace") as fh:
                out[os.path.relpath(path, root)] = fh.read()
    return out
