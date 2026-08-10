"""Shared accessors for the shipped host and its tasks.

One host carries several tasks (plan §6.1), so a test that wants a policy, an
action target, or success criteria has to say *which task* — those are task
material, because the task layer is what makes an action out of scope. The host
owns the workspace, the account boundary, the vehicles, and the A4 consumer.

Every test names its task explicitly rather than defaulting, so that adding
T2–T5 does not silently change what an existing test is asserting about.
"""

from __future__ import annotations

import json
import os
from typing import Any

from taskbound.policy import Policy

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HOST_DIR = os.path.join(ROOT, "hosts", "site_a")
CORE_TASK = "t1_failed_job"


def host() -> dict[str, Any]:
    with open(os.path.join(HOST_DIR, "host.json"), encoding="utf-8") as fh:
        return json.load(fh)


def task_dir(task_id: str = CORE_TASK) -> str:
    return os.path.join(HOST_DIR, host()["tasks"][task_id])


def task(task_id: str = CORE_TASK) -> dict[str, Any]:
    with open(os.path.join(task_dir(task_id), "task.json"), encoding="utf-8") as fh:
        return json.load(fh)


def policy(task_id: str = CORE_TASK) -> Policy:
    """The policy a run actually evaluates: account layer plus this task's layer."""
    return Policy.from_layers(host()["account_policy"], task(task_id)["policy"])
