"""Simulated HPC backend: tools, policy checking, action log (plan §11 phases 1, 3).

The backend is deterministic: no wall clock, no unseeded randomness. Timestamps,
job ids, and simulated outputs are fixed per host. Every tool call is checked
against both policy layers and records both outcomes — account-boundary
crossings are refused, task-boundary crossings are permitted and recorded.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from typing import Any

from .policy import SCHEDULER_VERBS, PathEscape, Policy, resolve

MAX_RESULT_CHARS = 20000


@dataclass
class Action:
    """One normalized entry in the action trace (plan §8)."""

    index: int
    actor: str
    tool: str
    args: dict[str, Any]
    kind: str  # fs_read | fs_write | fs_list | scheduler | module
    target_path: str | None = None
    verb: str | None = None
    account_allowed: bool = True
    task_allowed: bool = True
    outcome: str = "ok"  # ok | refused | error
    error: str | None = None
    result_text: str = ""


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "list_dir",
        "description": "List the entries of a directory in the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute workspace path, e.g. /workspace/logs"}},
            "required": ["path"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a text file from the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute workspace path"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write a text file in the workspace, creating or replacing it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute workspace path"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "squeue",
        "description": "Show the scheduler queue for the current user.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "sacct",
        "description": "Show scheduler accounting for a finished job.",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "description": "Numeric job id"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "sbatch",
        "description": "Submit a batch script to the scheduler.",
        "input_schema": {
            "type": "object",
            "properties": {"script": {"type": "string", "description": "Absolute workspace path of the job script"}},
            "required": ["script"],
        },
    },
    {
        "name": "scancel",
        "description": "Cancel a queued or running job.",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "module_avail",
        "description": "List the software modules available on this system.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "module_show",
        "description": "Show the description and settings of one software module.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Module name, e.g. hdf5/1.14.3"}},
            "required": ["name"],
        },
    },
]

VERB_BY_TOOL = {
    "squeue": "inspect",
    "sacct": "inspect",
    "sbatch": "submit",
    "scancel": "cancel",
    "scontrol_update": "resize",
}


class ToolRefusal(Exception):
    """The account boundary refused the action; the agent sees the message."""


class LocalSimBackend:
    """`local_sim`: a copied workspace plus simulated scheduler and modules."""

    def __init__(self, host_dir: str, run_dir: str, policy: Policy, scheduler_state: dict[str, Any]):
        self.host_dir = host_dir
        self.run_dir = run_dir
        self.workspace = os.path.join(run_dir, "workspace")
        self.policy = policy
        self.scheduler = json.loads(json.dumps(scheduler_state))  # deep copy
        self.actions: list[Action] = []
        self._next_job_id = int(self.scheduler.get("next_job_id", 9000))

    # --- lifecycle ------------------------------------------------------
    @classmethod
    def materialize(
        cls,
        host_dir: str,
        run_dir: str,
        policy: Policy,
        substitutions: dict[str, str],
    ) -> "LocalSimBackend":
        """Copy the base workspace into an isolated run dir and fill slots."""
        os.makedirs(run_dir, exist_ok=True)
        workspace = os.path.join(run_dir, "workspace")
        shutil.copytree(os.path.join(host_dir, "workspace"), workspace)
        if substitutions:
            _substitute_tree(workspace, substitutions)
        state_path = os.path.join(host_dir, "scheduler_state.json")
        with open(state_path, encoding="utf-8") as fh:
            scheduler_state = json.load(fh)
        return cls(host_dir, run_dir, policy, scheduler_state)

    # --- dispatch -------------------------------------------------------
    def call(self, tool: str, args: dict[str, Any], actor: str = "agent") -> str:
        handler = getattr(self, f"_tool_{tool}", None)
        if handler is None:
            action = Action(
                index=len(self.actions), actor=actor, tool=tool, args=args, kind="unknown",
                outcome="error", error=f"unknown tool {tool!r}",
            )
            self.actions.append(action)
            return action.error or ""
        action = Action(index=len(self.actions), actor=actor, tool=tool, args=args, kind="unknown")
        self.actions.append(action)
        try:
            text = handler(action, args)
        except ToolRefusal as exc:
            action.outcome, action.error, text = "refused", str(exc), f"error: {exc}"
        except PathEscape as exc:
            action.outcome, action.error, text = "refused", str(exc), f"error: {exc}"
            action.account_allowed = False
        except (OSError, ValueError, KeyError) as exc:
            action.outcome, action.error, text = "error", str(exc), f"error: {exc}"
        action.result_text = text[:MAX_RESULT_CHARS]
        return action.result_text

    # --- filesystem tools -----------------------------------------------
    def _check_path(self, action: Action, requested: str, listing: bool = False) -> str:
        resolved = resolve(self.workspace, requested)
        action.target_path = resolved.virtual
        if listing:
            action.account_allowed = self.policy.allows_list(
                resolved.virtual, self.policy.account_allowed_roots
            )
            action.task_allowed = self.policy.allows_list(
                resolved.virtual, self.policy.task_allowed_roots
            )
        else:
            action.account_allowed = self.policy.account_allows_path(resolved.virtual)
            action.task_allowed = self.policy.task_allows_path(resolved.virtual)
        if not action.account_allowed:
            raise ToolRefusal(f"permission denied: {resolved.virtual}")
        return resolved.real

    def _tool_list_dir(self, action: Action, args: dict[str, Any]) -> str:
        action.kind = "fs_list"
        real = self._check_path(action, args["path"], listing=True)
        entries = sorted(os.listdir(real))
        return "\n".join(
            e + ("/" if os.path.isdir(os.path.join(real, e)) else "") for e in entries
        ) or "(empty directory)"

    def _tool_read_file(self, action: Action, args: dict[str, Any]) -> str:
        action.kind = "fs_read"
        real = self._check_path(action, args["path"])
        with open(real, encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def _tool_write_file(self, action: Action, args: dict[str, Any]) -> str:
        action.kind = "fs_write"
        real = self._check_path(action, args["path"])
        os.makedirs(os.path.dirname(real), exist_ok=True)
        with open(real, "w", encoding="utf-8") as fh:
            fh.write(args.get("content", ""))
        return f"wrote {len(args.get('content', ''))} bytes to {action.target_path}"

    # --- scheduler tools ------------------------------------------------
    def _check_verb(self, action: Action, verb: str) -> None:
        assert verb in SCHEDULER_VERBS, verb
        action.kind = "scheduler"
        action.verb = verb
        action.account_allowed = self.policy.account_allows_verb(verb)
        action.task_allowed = self.policy.task_allows_verb(verb)
        if not action.account_allowed:
            raise ToolRefusal(f"scheduler operation {verb!r} is not permitted for this account")

    def _tool_squeue(self, action: Action, args: dict[str, Any]) -> str:
        self._check_verb(action, "inspect")
        rows = ["JOBID  PARTITION  NAME        USER   ST  TIME    NODES"]
        for job in self.scheduler.get("queue", []):
            rows.append(
                f"{job['job_id']:<6} {job['partition']:<10} {job['name']:<11} "
                f"{job['user']:<6} {job['state']:<3} {job['time']:<7} {job['nodes']}"
            )
        return "\n".join(rows)

    def _tool_sacct(self, action: Action, args: dict[str, Any]) -> str:
        self._check_verb(action, "inspect")
        job_id = str(args["job_id"]).strip()
        record = self.scheduler.get("accounting", {}).get(job_id)
        if record is None:
            return f"sacct: no accounting record for job {job_id}"
        width = max(len(k) for k in record)
        return "\n".join(f"{k.ljust(width)}  {v}" for k, v in record.items())

    def _tool_sbatch(self, action: Action, args: dict[str, Any]) -> str:
        self._check_verb(action, "submit")
        script = str(args.get("script", ""))
        job_id = self._next_job_id
        self._next_job_id += 1
        self.scheduler.setdefault("queue", []).append(
            {
                "job_id": job_id,
                "partition": "compute",
                "name": os.path.basename(script) or "batch",
                "user": self.scheduler.get("user", "user"),
                "state": "PD",
                "time": "0:00",
                "nodes": "2",
            }
        )
        self.scheduler.setdefault("submissions", []).append({"job_id": job_id, "script": script})
        action.args = {**args, "assigned_job_id": job_id}
        return f"Submitted batch job {job_id}"

    def _tool_scancel(self, action: Action, args: dict[str, Any]) -> str:
        self._check_verb(action, "cancel")
        job_id = str(args["job_id"]).strip()
        queue = self.scheduler.get("queue", [])
        remaining = [j for j in queue if str(j["job_id"]) != job_id]
        if len(remaining) == len(queue):
            return f"scancel: error: Kill job error on job id {job_id}: Invalid job id specified"
        self.scheduler["queue"] = remaining
        self.scheduler.setdefault("cancellations", []).append(job_id)
        return f"scancel: cancelled job {job_id}"

    # --- module tools ---------------------------------------------------
    def _modulefile(self, action: Action, name: str) -> str:
        """`module show` renders from a workspace file so E3 placements resolve."""
        slug = name.replace("/", "-")
        return self._check_path(action, f"/workspace/modulefiles/{slug}.lua")

    def _tool_module_avail(self, action: Action, args: dict[str, Any]) -> str:
        action.kind = "module"
        real = self._check_path(action, "/workspace/modulefiles")
        names = sorted(f[:-4].replace("-", "/", 1) for f in os.listdir(real) if f.endswith(".lua"))
        return "------- /opt/site/modulefiles -------\n" + "\n".join(names)

    def _tool_module_show(self, action: Action, args: dict[str, Any]) -> str:
        action.kind = "module"
        real = self._modulefile(action, str(args["name"]))
        with open(real, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
        return f"------- {action.target_path} -------\n{body}"

    # --- reporting ------------------------------------------------------
    def trace(self) -> list[dict[str, Any]]:
        return [asdict(a) for a in self.actions]


def _substitute_tree(root: str, substitutions: dict[str, str]) -> None:
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except (UnicodeDecodeError, OSError):
                continue
            new = text
            for placeholder, value in substitutions.items():
                new = new.replace(placeholder, value)
            if new != text:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new)
