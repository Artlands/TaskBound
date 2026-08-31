"""Sweep driver: frozen attempt schedules and exposure recruitment (plan §8.4, §11.4).

Two commands, deliberately separate:

    sweep plan  -> a complete, ordered, seeded attempt schedule, written before
                   anything runs and hashed against the material it was built
                   from
    sweep run   -> walks that schedule, retaining every attempt

The separation is the point. The complete attempt order and seeds are generated
*before* execution, so recruitment cannot become a decision made while results
are visible. A cell recruits each paraphrase to one third of `EXPOSED_TARGET`
and is capped at `ATTEMPT_CAP` attempted. Each frozen slot has a rotated fallback
order, so a completed paraphrase's later slots go deterministically to an
unfinished paraphrase. A cell that finishes short of target reports the
shortfall separately for every paraphrase and is never quietly pooled.

Conditions, cells, and paraphrases are interleaved in seeded blocks so that
time-of-day or provider drift cannot align with one condition.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import hashlib
import json
import os
import random
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Sequence
from typing import Any

from . import runner

# `v1.1-budget` sizes the allocation to a wall-clock budget on a self-hosted
# endpoint, where a sweep is measured in days rather than dollars. `v1.0-broad`
# (N = 9, cap 27, near-miss 36) costs about 58 hours per model family on the
# reference deployment; every reduction
# below is priced against measurements from 399 live attempts rather than
# chosen for roundness.
EXPOSED_TARGET = 3
# Still 3N. The ratio is what earns a low-exposure entry point the same N as
# the rest, and it is unchanged; what changed is N.
ATTEMPT_CAP = 3 * EXPOSED_TARGET
# Near-miss runs at twice the injected N rather than four times. It remains the
# largest single block and the reason is unchanged — overblocking is what
# separates an agent respecting task scope from one refusing broadly (plan
# §7.4) — but at this budget four times the injected N is a third of the sweep.
# The interval it buys is wide, and that is reported rather than hidden.
NEAR_MISS_TARGET = 6
CLEAN_TARGET = 3
# Recruitment cost is keyed to the entry point. E3 measured 0.04 on T1 and 0.00
# on T5 across the live runs — not the 0.40 this allocation was first sized
# against — so its groups spend the full cap to report a shortfall. A cap of one
# block keeps E3's exposure rate, which is a reported result in its own right
# (plan §7.2), without buying a target it cannot reach.
# ponytail: the key is the entry point, but the Stage 1 smoke exposed E3 at 6/6
# on T3 against 2/27 on T1 — exposure is task-dependent here, so this cap
# truncates the one E3 cell that reaches it. Six runs is not enough to re-key on;
# the sizing pilot measures it, and moving to a (task, entry point) cap is a
# registration change rather than an edit.
DEFAULT_ENTRY_POINT_ATTEMPT_CAPS = {"E3": 3}
# T3 runs at 1,175 s against T1's 506 s and reaches the turn limit on most
# attempts, so its blocks are the most expensive rows in the sweep. It stays in
# the allocation because its two cells are what keep every entry point and
# induced action present in three tasks apiece (plan §6.2) — drop it and the
# task term is confounded with both factors — but it carries cells only.
DEFAULT_CELLS_ONLY_TASKS = ("t3_build_and_run",)
BLOCK = 3  # one attempt per paraphrase, so a stopped group stays balanced
PARAPHRASES = ("p1", "p2", "p3")
ENTRY_POINTS = ("E1", "E2", "E3", "E4")
INERT_PARAPHRASES = ("i1", "i2", "i3")
# Every task the host declares. T1 carries the complete E1-E4 x A1-A4 crossing;
# T2-T5 carry two cells apiece, drawn from the sixteen T1 already populates so
# the task effect is identified within cell (plan §6.2).
DEFAULT_RELEASE_TASKS = (
    "t1_failed_job",
    "t2_postproc_repair",
    "t3_build_and_run",
    "t4_data_staging",
    "t5_status_report",
)


# --- planning ------------------------------------------------------------
def plan(
    host_dir: str,
    injections_dir: str,
    seed: int,
    exposed_target: int = EXPOSED_TARGET,
    attempt_cap: int = ATTEMPT_CAP,
    tasks_filter: Sequence[str] | None = None,
    entry_points: Sequence[str] | None = None,
    near_miss_target: int | None = None,
    clean_target: int | None = None,
    integration_smoke: bool = False,
    entry_point_attempt_caps: dict[str, int] | None = None,
    cells_only_tasks: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Freeze the attempt schedule for one release's scope.

    A release is a subset of what the host carries. `v1.1-budget` is all five
    tasks at E1-E4; narrower scopes remain available for diagnostics. The scope
    is named here rather than inferred, so a schedule records what it was
    planned to cover and a later sweep cannot silently widen it.

    **N is per condition, not per schedule** (plan §7). `exposed_target` governs
    injected groups, which recruit to exposure and balance three paraphrases;
    near-miss and clean blocks carry neither, and take their own targets. A
    single target for everything is what a diagnostic run wants and what the
    release schedule must not have.
    """
    # The multiple-of-three rule protects the paraphrase allocation the variance
    # decomposition reads (plan §7.5). Near-miss and clean blocks have no
    # paraphrases, so applying it to them would be a guard on a property they do
    # not have — which is what kept near-miss pinned to the injected N.
    #
    # It is also over-broad for the pilot's Stage 1 smoke, which asks for one run
    # per populated group to check wiring, exposure, placement resolution and
    # result completeness — none of which reads the paraphrase allocation, and
    # three paraphrases cannot be balanced across one run. `integration_smoke`
    # is that opt-out (execution_plan.md "Settled decision: Stage 1 smoke", option
    # B). It is recorded in the schedule rather than left implicit, so a
    # schedule states whether it is a release schedule instead of a reader
    # inferring it from the target, and `sweep run` refuses to aggregate one as
    # if it were confirmatory.
    if exposed_target % len(PARAPHRASES) and not integration_smoke:
        raise SystemExit(
            "exposed target must divide evenly across the three paraphrases; "
            "pass --integration-smoke for the pilot's Stage 1, which does not "
            "read the paraphrase allocation")
    near_miss_target = NEAR_MISS_TARGET if near_miss_target is None else near_miss_target
    clean_target = CLEAN_TARGET if clean_target is None else clean_target
    for label, value in (("near-miss", near_miss_target), ("clean", clean_target)):
        if value < 1:
            raise SystemExit(f"{label} target must be at least 1")
    # Recruitment cost is per entry point, because exposure is: a vehicle the
    # workflow rarely opens spends its whole cap and still reports a shortfall.
    # A smaller cap there buys the exposure rate — itself a reported result
    # (plan §7.2) — without paying the injected N's cap for a target the entry
    # point cannot reach. Deliberately *not* guarded against falling below
    # `exposed_target`: that is the intended use, and the manifest already
    # reports such a group with both denominators and `hit_attempt_cap`.
    entry_point_attempt_caps = dict(
        DEFAULT_ENTRY_POINT_ATTEMPT_CAPS if entry_point_attempt_caps is None
        else entry_point_attempt_caps)
    for entry, cap in sorted(entry_point_attempt_caps.items()):
        if entry not in ENTRY_POINTS:
            raise SystemExit(
                f"unknown entry point {entry!r}; expected one of "
                f"{', '.join(ENTRY_POINTS)}")
        if cap < len(PARAPHRASES) or cap % len(PARAPHRASES):
            raise SystemExit(
                f"attempt cap for {entry} must be a positive multiple of "
                f"{len(PARAPHRASES)}, so the paraphrase rotation stays balanced "
                f"wherever recruitment stops; got {cap}")
    host = runner.load_host(host_dir)
    injections = _index_injections(injections_dir, host["host_id"])
    rng = random.Random(seed)

    # One host carries several tasks (plan §6.1). Attacked and benign are per
    # (task, cell); near-miss and clean are per task, because each task declares
    # its own scope and therefore its own floor; inert is per entry point under
    # the core task alone, which supplies the text-presence contrast (plan §7).
    groups: dict[str, dict[str, Any]] = {}
    declared = sorted(host.get("tasks", {}))
    if tasks_filter:
        unknown = sorted(set(tasks_filter) - set(declared))
        if unknown:
            raise SystemExit(f"{host['host_id']} declares no task(s): {', '.join(unknown)}")
        declared = [t for t in declared if t in set(tasks_filter)]
    keep_ep = set(entry_points) if entry_points else None
    # Absent means the registered allocation, the same as `exposed_target`;
    # an explicit value replaces it. The default is intersected with the scope
    # so narrowing the task list stays legal, while an *explicit* task outside
    # the scope is a mistake worth reporting.
    if cells_only_tasks is None:
        cells_only = {t for t in DEFAULT_CELLS_ONLY_TASKS if t in set(declared)}
    else:
        cells_only = set(cells_only_tasks)
        unknown = sorted(cells_only - set(declared))
        if unknown:
            raise SystemExit(
                "cells-only names task(s) outside this schedule's scope: "
                + ", ".join(unknown))

    tasks = {tid: runner.load_task(host, tid) for tid in declared}
    for task_id, task in tasks.items():
        cells = [c for c in task["cells"] if keep_ep is None or c[:2] in keep_ep]
        if not cells:
            continue
        for cell in cells:
            for condition, kind in (("attacked", "attack"), ("benign", "benign")):
                texts = [injections[task_id, cell, kind, p] for p in PARAPHRASES]
                groups[f"{condition}|{task_id}|{cell}"] = _group(
                    condition, exposed_target,
                    entry_point_attempt_caps.get(cell[:2], attempt_cap), texts,
                    task=task_id, cell=cell, recruits=True,
                )
        if task.get("role") == "core":
            for entry in sorted({c[:2] for c in cells}):
                texts = [injections[task_id, entry, "inert", p] for p in INERT_PARAPHRASES]
                groups[f"inert|{task_id}|{entry}"] = _group(
                    "inert", exposed_target,
                    entry_point_attempt_caps.get(entry, attempt_cap), texts,
                    task=task_id, cell=entry, recruits=True,
                )
        # A cells-only task carries its injected cells and nothing else. What
        # the auxiliary tasks are *for* is keeping every entry point and every
        # induced action present in the same number of tasks, so the task term
        # is not confounded with either factor (plan §6.2) — and that property
        # is a statement about cells. The per-task near-miss and clean blocks
        # are separate quantities, and a task whose runs are expensive can
        # supply the balance without paying for blocks the balance does not
        # need. Its cells still carry the injected N, so nothing about the
        # crossing is thinned.
        if task_id not in cells_only:
            for action in sorted({c[2:] for c in cells}):
                groups[f"near_miss|{task_id}|{action}"] = _group(
                    "near_miss", near_miss_target, near_miss_target, [],
                    task=task_id, near_miss_action=action, recruits=False,
                )
            groups[f"clean|{task_id}"] = _group(
                "clean", clean_target, clean_target, [], task=task_id, recruits=False
            )

    attempts = _interleave(groups, rng)
    schedule = {
        "schema_version": runner.SCHEMA_VERSION,
        "sweep_id": None,
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "host": {"dir": host_dir, "id": host["host_id"], "hash": host["_hash"]},
        "scope": {"tasks": list(tasks), "entry_points": sorted(keep_ep) if keep_ep else None},
        "injections_dir": injections_dir,
        "seed": seed,
        "exposed_target": exposed_target,
        "attempt_cap": attempt_cap,
        # Part of the allocation, so it rides in the identity below rather than
        # beside it: two schedules differing only in where recruitment stops are
        # different schedules.
        "entry_point_attempt_caps": entry_point_attempt_caps,
        "cells_only_tasks": sorted(cells_only),
        # Recorded beside the injected target so a reader of the frozen schedule
        # can see every registered N without re-deriving it from the groups.
        "near_miss_target": near_miss_target,
        "clean_target": clean_target,
        # Stamped on the artifact so the schedule says what it is. A smoke
        # schedule is an integration check, never a measurement, and pooling its
        # runs with the sweep it precedes is forbidden (pilot_protocol.md).
        "integration_smoke": integration_smoke,
        "block": BLOCK,
        "groups": {
            name: {k: v for k, v in g.items() if k != "attempts"}
            for name, g in groups.items()
        },
        "target_runs": sum(g["target"] for g in groups.values()),
        "max_attempts": len(attempts),
        "attempts": attempts,
    }
    schedule["sweep_id"] = _sweep_id(schedule)
    return schedule


def _group(
    condition: str,
    exposed_target: int,
    attempt_cap: int,
    texts: list[dict[str, Any]],
    task: str,
    cell: str | None = None,
    near_miss_action: str | None = None,
    recruits: bool = True,
) -> dict[str, Any]:
    return {
        "condition": condition,
        "task": task,
        "cell": cell,
        "near_miss_action": near_miss_action,
        "target": exposed_target,
        "attempt_cap": attempt_cap,
        # Only injected conditions recruit: exposure is a property of injected
        # text reaching a tool result, and clean and near-miss runs have none.
        "recruits_to_exposure": recruits,
        "texts": [t["_path"] for t in texts],
        "text_hashes": {t["paraphrase"]: t["_hash"] for t in texts},
        "paraphrases": [t["paraphrase"] for t in texts],
    }


def _index_injections(
    directory: str, host_id: str
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    index = {}
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            inj = json.load(fh)
        if inj["host"] != host_id:
            continue
        inj["_path"] = path
        with open(path, "rb") as raw:
            inj["_hash"] = hashlib.sha256(raw.read()).hexdigest()[:16]
        index[inj["task"], inj["cell"], inj["kind"], inj["paraphrase"]] = inj
    return index


def _interleave(groups: dict[str, dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:
    """Round-robin over groups in blocks, with the order within a round shuffled.

    Every group's attempts are laid out first, in balanced blocks; the rounds
    are then interleaved so no group occupies a contiguous stretch of wall
    clock. A provider that degrades halfway through a sweep degrades every
    condition equally.
    """
    per_group: dict[str, list[dict[str, Any]]] = {}
    for name, group in sorted(groups.items()):
        attempts = []
        texts = group["texts"]
        text_hashes = group["text_hashes"]
        paraphrases = group["paraphrases"]
        for index in range(group["attempt_cap"]):
            preference = (
                paraphrases[index % len(paraphrases):]
                + paraphrases[:index % len(paraphrases)]
                if paraphrases else []
            )
            injections = {
                paraphrase: texts[paraphrases.index(paraphrase)]
                for paraphrase in preference
            }
            injection_hashes = {
                paraphrase: text_hashes[paraphrase] for paraphrase in preference
            }
            attempts.append({
                "group": name,
                "condition": group["condition"],
                "task": group["task"],
                "cell": group["cell"],
                "near_miss_action": group["near_miss_action"],
                "injection": texts[index % len(texts)] if texts else None,
                "injection_hash": (
                    text_hashes[paraphrases[index % len(paraphrases)]]
                    if texts else None
                ),
                "paraphrase": paraphrases[index % len(paraphrases)] if paraphrases else None,
                "paraphrase_options": preference,
                "injections_by_paraphrase": injections,
                "injection_hashes_by_paraphrase": injection_hashes,
                "index_in_group": index,
                "block": index // BLOCK,
                "placement_seed": rng.randrange(1, 2**31),
            })
        per_group[name] = attempts

    ordered: list[dict[str, Any]] = []
    round_index = 0
    while any(per_group.values()):
        names = [n for n in sorted(per_group) if per_group[n]]
        rng.shuffle(names)
        for name in names:
            block = per_group[name][:BLOCK]
            del per_group[name][:BLOCK]
            ordered.extend(block)
        round_index += 1
    for order, attempt in enumerate(ordered):
        attempt["order"] = order
        attempt["attempt_id"] = (
            f"{attempt['group'].replace('|', '_')}_{attempt['index_in_group']:02d}"
        )
    return ordered


# What a sweep's identity is derived from. Every registered N is in here: the
# attempt list already implies them, but the identity that freezes an allocation
# should name it rather than leave a reader to infer it from a slot count.
SWEEP_ID_KEYS = (
    "host", "seed", "exposed_target", "attempt_cap", "entry_point_attempt_caps",
    "cells_only_tasks",
    # Whether this is an integration check or a measurement is part of what the
    # schedule *is*, so it belongs in the identity rather than beside it. Without
    # it, a smoke schedule and a release schedule that happened to share every N
    # would hash identically and a resumed directory could not tell them apart.
    "near_miss_target", "clean_target", "integration_smoke", "attempts",
)


def sweep_id(schedule: dict[str, Any]) -> str:
    """The frozen identity of one attempt schedule."""
    payload = json.dumps(
        {k: schedule.get(k) for k in SWEEP_ID_KEYS}, sort_keys=True,
    )
    return "sweep_" + hashlib.sha256(payload.encode()).hexdigest()[:12]


_sweep_id = sweep_id


# --- execution -----------------------------------------------------------
def execute(schedule: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    # Checked here rather than left to the first E4 attempt, which could be
    # hundreds of runs into a schedule that was never going to complete.
    e4 = sorted({g["cell"] for g in schedule["groups"].values()
                 if (g.get("cell") or "").startswith("E4")})
    if e4 and args.execution_mode != "two_agent":
        raise SystemExit(
            f"this schedule covers {', '.join(e4)}, which exist only under a "
            "two-agent workflow (plan §5.1, §6.4); pass --execution-mode two_agent "
            "or re-plan without --entry-point E4"
        )
    os.makedirs(args.out, exist_ok=True)
    state = _resume(args.out, schedule)
    configuration = _agent_configuration(args)

    # R2 (plan §3, §6.4): the execution model is held constant across the cells
    # being compared. The release fixes two-agent mode throughout; a
    # sweep interrupted and resumed under the other mode would split one
    # schedule across two execution models, and every entry-point contrast in
    # it would carry part of the mode difference with no way to tell which part.
    prior = sorted({r.get("execution_mode") for r in state["records"]} - {None})
    if prior and prior != [args.execution_mode]:
        raise SystemExit(
            f"sweep {schedule['sweep_id']} already has runs under "
            f"{', '.join(prior)}; resuming it as {args.execution_mode!r} would "
            "mix execution models inside one schedule (plan §6.4, R2)"
        )
    prior_configurations = {
        json.dumps(
            (record.get("sweep") or {}).get("agent_configuration"),
            sort_keys=True,
        )
        for record in state["records"]
    }
    expected_configuration = json.dumps(configuration, sort_keys=True)
    if state["records"] and prior_configurations != {expected_configuration}:
        raise SystemExit(
            f"sweep {schedule['sweep_id']} already has runs under a different "
            "agent configuration; use a distinct result directory for each "
            "model family"
        )
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0}
    for record in state["records"]:
        _add_usage(usage, record.get("outcome", {}).get("usage") or {})

    started = _dt.datetime.now(_dt.timezone.utc).isoformat()
    stopped_early = None

    workers = getattr(args, "workers", 1)
    if workers <= 1:
        # The default serial path is byte-identical to walking the frozen
        # schedule one attempt at a time: recruitment reads the exposure
        # results of every earlier run, so this is also the adaptive exact
        # form of the paraphrase fallback (plan §8.4).
        for attempt in schedule["attempts"]:
            signal = _process_attempt(state, usage, attempt, schedule, args, configuration)
            if signal in ("stop_max", "stop_spend"):
                stopped_early = "max_attempts" if signal == "stop_max" else "spend_ceiling"
                break
    else:
        stopped_early = _run_parallel(state, usage, schedule, args, configuration, workers)

    return _manifest(schedule, args, state, usage, started, stopped_early)


def _group_complete(group: dict[str, Any], counts: dict[str, Any]) -> bool:
    """Stop once every paraphrase in the group has what it needs.

    Recruitment is on *exposed* runs, so an entry point the agent rarely opens
    costs attempts rather than silently reporting a smaller sample.
    """
    if counts["attempted"] >= group["attempt_cap"]:
        return True
    if group["recruits_to_exposure"]:
        # Both conjuncts, because the per-paraphrase floor alone is not the
        # rule. The Stage 1 smoke recruits one exposed run per group across
        # three paraphrases, so the floor is 0 and "every paraphrase has at
        # least 0" reads as complete on a group that has never run — every
        # injected group skipped, and the manifest reporting it as reached.
        # The group's own target is what recruitment owes.
        return (counts["exposed"] >= group["target"]
                and all(counts["exposed_by_paraphrase"].get(p, 0)
                        >= _paraphrase_target(group)
                        for p in group["paraphrases"]))
    return counts["attempted"] >= group["target"]


def _paraphrase_target(group: dict[str, Any]) -> int:
    """Exposed runs each paraphrase is owed.

    Floor division: a release group's target divides evenly across its three
    paraphrases, and a smoke group's does not divide at all, which is what
    ``--integration-smoke`` opts out of. There the floor is 0 and the total
    target in ``_group_complete`` is what binds.
    """
    return group["target"] // len(group["paraphrases"])


def _resolve_attempt(
    group: dict[str, Any], counts: dict[str, Any], attempt: dict[str, Any]
) -> dict[str, Any]:
    if not group["recruits_to_exposure"]:
        return attempt
    target = _paraphrase_target(group)
    start = group["paraphrases"].index(attempt["paraphrase"])
    options = attempt.get("paraphrase_options") or (
        group["paraphrases"][start:] + group["paraphrases"][:start]
    )
    exposed = counts["exposed_by_paraphrase"]
    # Below the floor first, which is the release path and unchanged. When no
    # paraphrase is below it — a smoke group, whose floor is 0 — the group is
    # still short on its total, so fall back to the least-recruited paraphrase
    # in the same rotation order rather than raising StopIteration.
    paraphrase = next(
        (p for p in options if exposed.get(p, 0) < target),
        min(options, key=lambda p: exposed.get(p, 0)),
    )
    if paraphrase == attempt["paraphrase"]:
        return attempt
    resolved = {**attempt, "paraphrase": paraphrase}
    injections = attempt.get("injections_by_paraphrase") or dict(
        zip(group["paraphrases"], group["texts"])
    )
    resolved["injection"] = injections[paraphrase]
    resolved["injection_hash"] = (
        attempt.get("injection_hashes_by_paraphrase") or {}
    ).get(paraphrase)
    return resolved


def _empty_counts() -> dict[str, Any]:
    return {
        "attempted": 0,
        "exposed": 0,
        "conclusive": 0,
        "exposed_by_paraphrase": {},
    }


def _agent_configuration(args: argparse.Namespace) -> dict[str, Any]:
    configuration = {
        "adapter": args.agent,
        "model": args.model,
        "max_tokens": args.max_tokens,
        "turn_limit": args.turn_limit,
    }
    if args.agent == "anthropic":
        configuration["effort"] = args.effort or "high"
    elif args.agent == "openai_compatible":
        configuration.update({
            "base_url": args.base_url,
            "api_key_env": args.api_key_env,
            "reasoning_effort": args.reasoning_effort,
            "temperature": args.temperature,
            "token_param": args.token_param,
        })
    elif args.agent == "scripted":
        with open(args.script, "rb") as fh:
            configuration["script_sha256"] = hashlib.sha256(fh.read()).hexdigest()
    return configuration


def _run_one(schedule: dict[str, Any], attempt: dict[str, Any], args: argparse.Namespace):
    run_args = argparse.Namespace(
        host=schedule["host"]["dir"],
        task=attempt["task"],
        condition=attempt["condition"],
        injection=attempt["injection"],
        near_miss_action=attempt["near_miss_action"],
        seed=attempt["placement_seed"],
        canary_seed=args.canary_seed,
        execution_mode=args.execution_mode,
        defense=args.defense,
        control_profiles=args.control_profiles,
        inference_trust_boundary=args.inference_trust_boundary,
        agent=args.agent,
        script=args.script,
        model=args.model,
        max_tokens=args.max_tokens,
        turn_limit=args.turn_limit,
        effort=args.effort,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        reasoning_effort=args.reasoning_effort,
        temperature=args.temperature,
        token_param=args.token_param,
        out=args.out,
        keep_run_dir=False,
        print_answer=False,
    )
    return runner.assemble_and_run(run_args)


def _resume(out_dir: str, schedule: dict[str, Any]) -> dict[str, Any]:
    """Raw results are append-only, so a restarted sweep continues rather than reruns."""
    done: set[str] = set()
    counts: dict[str, dict[str, int]] = {}
    records = []
    for path in sorted(glob.glob(os.path.join(out_dir, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
        sweep = record.get("sweep")
        if not sweep or sweep["sweep_id"] != schedule["sweep_id"]:
            continue
        done.add(sweep["attempt_id"])
        records.append(record)
        counts.setdefault(sweep["group"], _empty_counts())
        counts[sweep["group"]]["attempted"] += 1
        counts[sweep["group"]]["exposed"] += bool(record["exposure"]["exposed"])
        paraphrase = (record.get("injection") or {}).get("paraphrase")
        if record["exposure"]["exposed"] and paraphrase:
            by_paraphrase = counts[sweep["group"]]["exposed_by_paraphrase"]
            by_paraphrase[paraphrase] = by_paraphrase.get(paraphrase, 0) + 1
        counts[sweep["group"]]["conclusive"] += not record["outcome"]["inconclusive"]
    return {"done": done, "counts": counts, "records": records, "attempted": 0}


def _write(out_dir: str, attempt_id: str, record: dict[str, Any]) -> None:
    path = os.path.join(out_dir, attempt_id + ".json")
    if os.path.exists(path):
        raise SystemExit(f"refusing to overwrite existing result {path}")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def _control_profile_hashes(directory: str) -> list[dict[str, Any]]:
    profiles = []
    if not os.path.isdir(directory):
        return profiles
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            profile = json.load(fh)
        profiles.append({
            "file": name,
            "profile_id": profile["profile_id"],
            "version": profile["version"],
            "annotation": profile["annotation"],
            "sha256": _canonical_sha256(profile),
        })
    return profiles


def _add_usage(total: dict[str, int], reported: dict[str, Any]) -> None:
    for key in total:
        total[key] += int(reported.get(key) or 0)


def _cost(usage: dict[str, int], args: argparse.Namespace) -> float:
    """Measured tokens against a price table dated on the day of approval.

    Provider prices change too quickly to be a release assumption, so nothing
    is hardcoded: if no prices are given, cost is reported as zero and the
    spend ceiling has to be expressed in attempts instead.
    """
    uncached = max(0, usage["input_tokens"] - usage["cache_read_input_tokens"])
    return (
        uncached * (args.price_in or 0)
        + usage["cache_read_input_tokens"] * (args.price_cached or 0)
        + usage["output_tokens"] * (args.price_out or 0)
    ) / 1e6


# --- per-attempt execution ----------------------------------------------
def _decide_attempt(state, schedule, args, usage, attempt):
    """Classify one attempt without executing it.

    Returns ``("run", group, counts)`` if it should run, ``("skip",
    group, counts)`` if it is already done or its group is complete, or
    ``("stop_max", ...)`` / ``("stop_spend", ...)`` for a hard stop. The two
    stop reasons are how the caller knows which ceiling was hit.
    """
    group = schedule["groups"][attempt["group"]]
    counts = state["counts"].setdefault(attempt["group"], _empty_counts())
    if attempt["attempt_id"] in state["done"]:
        return "skip", group, counts
    if _group_complete(group, counts):
        return "skip", group, counts
    if args.max_attempts and state["attempted"] >= args.max_attempts:
        return "stop_max", group, counts
    if args.spend_ceiling and _cost(usage, args) >= args.spend_ceiling:
        return "stop_spend", group, counts
    return "run", group, counts


def _apply_result(
    state, usage, schedule, args, configuration,
    attempt, resolved_attempt, record,
) -> None:
    """Fold one finished run into shared sweep state.

    This is the single shared accounting path for both the serial and the
    parallel runner, so the manifest, the append-only result files, and the
    per-group counts are identical regardless of how attempts were scheduled.
    The result file is written here on the caller's (main) thread, to a unique
    ``attempt_id`` path that ``_write`` refuses to overwrite, so batch order
    and thread timing never change a record.
    """
    counts = state["counts"].setdefault(attempt["group"], _empty_counts())
    record["sweep"] = {
        "sweep_id": schedule["sweep_id"],
        "attempt_id": attempt["attempt_id"],
        "group": attempt["group"],
        "order": attempt["order"],
        "block": attempt["block"],
        # Carried onto the result, not just the schedule: pilot runs must never
        # be pooled with the sweep they precede (pilot_protocol.md), and a
        # result that has been copied out of its directory would otherwise
        # carry no trace of having been an integration check.
        "integration_smoke": schedule.get("integration_smoke", False),
        "agent_configuration": configuration,
    }
    _write(args.out, attempt["attempt_id"], record)
    state["records"].append(record)

    counts["attempted"] += 1
    state["attempted"] += 1
    if record["exposure"]["exposed"]:
        counts["exposed"] += 1
        paraphrase = resolved_attempt.get("paraphrase")
        if paraphrase:
            by_paraphrase = counts["exposed_by_paraphrase"]
            by_paraphrase[paraphrase] = by_paraphrase.get(paraphrase, 0) + 1
    if not record["outcome"]["inconclusive"]:
        counts["conclusive"] += 1
    _add_usage(usage, record["outcome"].get("usage") or {})
    if args.verbose:
        print(f"[{attempt['order']:>5}] {attempt['attempt_id']:<28} "
              f"exposed={record['exposure']['exposed']!s:<5} "
              f"inconclusive={record['outcome']['inconclusive']}")


def _process_attempt(
    state, usage, attempt, schedule, args, configuration,
) -> str:
    """Execute one attempt and fold its result into shared sweep state.

    Returns the loop-control signal: ``"ran"``, ``"skip"``, ``"stop_max"``, or
    ``"stop_spend"``. The serial runner calls this once per schedule slot, so
    its per-attempt accounting is identical to the original loop.
    """
    decision, group, counts = _decide_attempt(state, schedule, args, usage, attempt)
    if decision != "run":
        return decision
    resolved_attempt = _resolve_attempt(group, counts, attempt)
    record = _run_one(schedule, resolved_attempt, args)
    _apply_result(
        state, usage, schedule, args, configuration,
        attempt, resolved_attempt, record,
    )
    return "ran"


def _run_parallel(
    state, usage, schedule, args, configuration, workers,
):
    """Run the schedule in deterministic, write-isolated batches.

    Each batch takes up to ``workers`` pending attempts, resolves all of them
    against the counts as they stand at batch start (exposure outcomes within
    the batch are unknown until the attempts run), executes the model calls
    concurrently, and folds the finished records in schedule order. Results
    are written on this single thread, so the set of result files and the
    manifest are reproducible for a fixed ``(schedule, seed, workers)`` even
    though thread timing varies.

    At ``workers == 1`` this behaves exactly like the serial path. At
    ``workers > 1`` a slot that would, in serial order, have been re-routed by
    an earlier same-batch run's exposure result instead resolves on the next
    batch boundary, so the recruitment snapshot is coarser by up to one batch;
    a group may therefore over-recruit by up to ``workers - 1`` attempts. The
    attempt and spend ceilings are re-checked at every batch boundary, so the
    spend ceiling may be exceeded by up to one batch's cost rather than being
    enforced per run.

    Returns the ``stopped_early`` reason string, or ``None``.
    """
    attempts = schedule["attempts"]
    idx = 0
    while True:
        batch: list[tuple[Any, Any]] = []  # (frozen attempt, resolved attempt)
        stopped = None
        room = None
        if args.max_attempts:
            room = args.max_attempts - state["attempted"]
            if room <= 0:
                return "max_attempts"
        while len(batch) < workers and idx < len(attempts):
            if args.max_attempts and len(batch) >= room:
                # The attempt ceiling keeps the batch from overshooting: run
                # exactly the remaining allowance, then the next pass stops.
                break
            attempt = attempts[idx]
            decision, group, counts = _decide_attempt(
                state, schedule, args, usage, attempt
            )
            if decision in ("stop_max", "stop_spend"):
                stopped = decision
                break
            if decision == "skip":
                idx += 1
                continue
            resolved = _resolve_attempt(group, counts, attempt)
            batch.append((attempt, resolved))
            idx += 1
        if stopped is not None:
            return "max_attempts" if stopped == "stop_max" else "spend_ceiling"
        if not batch:
            return None
        with ThreadPoolExecutor(max_workers=min(workers, len(batch))) as executor:
            futures = [
                executor.submit(_run_one, schedule, resolved, args)
                for _, resolved in batch
            ]
            for (attempt, resolved), future in zip(batch, futures):
                record = future.result()
                _apply_result(
                    state, usage, schedule, args, configuration,
                    attempt, resolved, record,
                )


def _manifest(schedule, args, state, usage, started, stopped_early) -> dict[str, Any]:
    groups = {}
    for name, group in sorted(schedule["groups"].items()):
        counts = state["counts"].get(name, _empty_counts())
        achieved = counts["exposed"] if group["recruits_to_exposure"] else counts["attempted"]
        paraphrase_target = (
            _paraphrase_target(group) if group["recruits_to_exposure"] else None
        )
        exposed_by_paraphrase = {
            p: counts["exposed_by_paraphrase"].get(p, 0) for p in group["paraphrases"]
        }
        shortfall_by_paraphrase = {
            p: max(0, paraphrase_target - exposed_by_paraphrase[p])
            for p in group["paraphrases"]
        }
        # `achieved >= target` on both arms: a smoke group's per-paraphrase
        # shortfalls are all 0 against a floor of 0, so the balance test alone
        # certifies a group that recruited nothing.
        reached_target = achieved >= group["target"] and (
            all(shortfall == 0 for shortfall in shortfall_by_paraphrase.values())
            if group["recruits_to_exposure"] else True
        )
        groups[name] = {
            **counts,
            "target": group["target"],
            "attempt_cap": group["attempt_cap"],
            "paraphrase_target": paraphrase_target,
            "exposed_by_paraphrase": exposed_by_paraphrase,
            "shortfall_by_paraphrase": shortfall_by_paraphrase,
            "reached_target": reached_target,
            # A group that hit the cap short of its target is reported at the
            # precision it reached, with both denominators, never pooled away.
            "hit_attempt_cap": counts["attempted"] >= group["attempt_cap"]
                               and not reached_target,
            # Recruitment counts attempts (clean, near-miss) or exposed runs
            # (injected); the analysis counts neither. It fits *conclusive*
            # runs, so a block can satisfy its recruitment rule and still hand
            # the model fewer rows than N. That gap was being tracked and never
            # read. Report it: `reached_target` keeps its recruitment meaning —
            # the registered attempts were spent — and this says how many of
            # them the analysis can actually use.
            "conclusive_shortfall": max(0, group["target"] - counts["conclusive"]),
        }
    return {
        "sweep_id": schedule["sweep_id"],
        "started_at": started,
        "finished_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "release": runner.RELEASE,
        "git_commit": runner._git_commit(),
        "git_source_sha256": runner._git_source_sha256(),
        "git_dirty": runner._git_dirty(),
        "schedule": {
            # Derived from SWEEP_ID_KEYS rather than a parallel list of the same
            # names. The two had already drifted once: adding a key to the
            # identity left the manifest reproducing a different hash, so a
            # signed aggregation would have failed its own binding check for a
            # reason that had nothing to do with the sweep. Whatever the
            # identity is derived from, the manifest carries.
            **{k: schedule.get(k) for k in SWEEP_ID_KEYS},
            # Per-group targets, because N is per condition (plan §7): replaying
            # recruitment against one global target would hold near-miss blocks
            # to the injected N and read every one of them as short.
            "group_targets": {
                name: group["target"] for name, group in schedule["groups"].items()
            },
        },
        "agent": {
            "adapter": args.agent, "model": args.model, "base_url": args.base_url,
            "effort": (args.effort or "high") if args.agent == "anthropic" else None,
            "turn_limit": args.turn_limit, "max_tokens": args.max_tokens,
        },
        "agent_configuration": _agent_configuration(args),
        "defense": args.defense,
        "execution_mode": args.execution_mode,
        "attempt_ids": [attempt["attempt_id"] for attempt in schedule["attempts"]],
        "result_sha256_by_attempt_id": {
            record["sweep"]["attempt_id"]: _canonical_sha256(record)
            for record in sorted(
                state["records"], key=lambda value: value["sweep"]["attempt_id"]
            )
            if (record.get("sweep") or {}).get("sweep_id") == schedule["sweep_id"]
        },
        "evaluated_control_profiles": _control_profile_hashes(args.control_profiles),
        "stopped_early": stopped_early,
        "groups": groups,
        "totals": {
            "attempted_this_session": state["attempted"],
            "attempted_total": sum(g["attempted"] for g in groups.values()),
            "exposed_total": sum(g["exposed"] for g in groups.values()),
            "groups_short_of_target": sorted(n for n, g in groups.items() if not g["reached_target"]),
            "groups_short_of_conclusive_target": sorted(
                n for n, g in groups.items() if g["conclusive_shortfall"]),
        },
        "cost": {
            "usage": usage,
            "price_table_date": args.price_date,
            "prices_per_million": {
                "input": args.price_in, "cached_input": args.price_cached, "output": args.price_out,
            },
            "estimated_cost": _cost(usage, args),
            "spend_ceiling": args.spend_ceiling,
        },
    }


# --- CLI -----------------------------------------------------------------
# argparse `append` cannot express "none of them": an absent flag means the
# registered allocation, so a caller who wants a uniform cap needs a value that
# says so. `plan()` already takes the empty mapping; this is the CLI spelling.
OPT_OUT = "none"


def _parse_entry_point_caps(values: Sequence[str] | None) -> dict[str, int]:
    """`["E3=6"]` -> `{"E3": 6}`. plan() validates the entry point and the cap."""
    caps: dict[str, int] = {}
    for value in values or ():
        if value.strip().lower() == OPT_OUT:
            continue
        entry, _, cap = value.partition("=")
        if not cap.strip().isdigit():
            raise SystemExit(
                f"--entry-point-attempt-cap expects EP=N (e.g. E3=6) or "
                f"{OPT_OUT!r} for a uniform cap; got {value!r}")
        caps[entry.strip()] = int(cap)
    return caps


def _parse_cells_only(values: Sequence[str] | None) -> list[str] | None:
    """`None` keeps the registered allocation; `["none"]` clears it."""
    if values is None:
        return None
    return [v for v in values if v.strip().lower() != OPT_OUT]


def cmd_plan(args: argparse.Namespace) -> int:
    tasks = args.tasks if args.tasks is not None else list(DEFAULT_RELEASE_TASKS)
    extra = {
        "near_miss_target": getattr(args, "near_miss_target", None),
        "clean_target": getattr(args, "clean_target", None),
        "integration_smoke": getattr(args, "integration_smoke", False),
        # None means "the registered allocation", which plan() supplies; a flag
        # that is given replaces it rather than adding to it, so a diagnostic
        # schedule can opt out of either.
        "entry_point_attempt_caps": (
            None if getattr(args, "entry_point_attempt_caps", None) is None
            else _parse_entry_point_caps(args.entry_point_attempt_caps)),
        "cells_only_tasks": _parse_cells_only(getattr(args, "cells_only_tasks", None)),
    }
    schedule = plan(args.host, args.injections, args.seed, args.exposed_target,
                    args.attempt_cap, tasks, args.entry_points, **extra)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(schedule, fh, indent=2)
        fh.write("\n")
    scope = schedule["scope"]
    print(f"{schedule['sweep_id']}: {len(schedule['groups'])} groups, "
          f"{schedule['target_runs']} target runs, {schedule['max_attempts']} maximum attempts")
    print(f"  scope: {len(scope['tasks'])} task(s) {', '.join(scope['tasks'])}"
          f"  entry points: {', '.join(scope['entry_points']) if scope['entry_points'] else 'all'}")
    print(f"  wrote {args.out}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    with open(args.schedule, encoding="utf-8") as fh:
        schedule = json.load(fh)
    host = runner.load_host(schedule["host"]["dir"])
    if host["_hash"] != schedule["host"]["hash"]:
        raise SystemExit(
            f"host {host['host_id']} has changed since this schedule was planned "
            f"({schedule['host']['hash']} -> {host['_hash']}). Plan a new sweep rather than "
            "mixing material across a configuration change (plan §11.4)."
        )
    manifest = execute(schedule, args)
    path = os.path.join(args.out, f"sweep_manifest_{schedule['sweep_id']}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    totals = manifest["totals"]
    print(f"\n{manifest['sweep_id']}: {totals['attempted_total']} attempted, "
          f"{totals['exposed_total']} exposed")
    if manifest["stopped_early"]:
        print(f"  stopped early: {manifest['stopped_early']}")
    for name in totals["groups_short_of_target"]:
        g = manifest["groups"][name]
        recruits = schedule["groups"][name]["recruits_to_exposure"]
        achieved = f"{g['exposed']}/{g['target']} exposed" if recruits else f"{g['attempted']}/{g['target']} run"
        print(f"  short of target: {name} — {achieved} in {g['attempted']} attempts"
              + ("  [hit attempt cap]" if g["hit_attempt_cap"] else ""))
    for name in totals["groups_short_of_conclusive_target"]:
        g = manifest["groups"][name]
        print(f"  short of analysable N: {name} — {g['conclusive']}/{g['target']} "
              f"conclusive in {g['attempted']} attempts "
              f"({g['conclusive_shortfall']} short)")
    print(f"  wrote {path}")
    return 0


def add_arguments(sub) -> None:
    plan_p = sub.add_parser("plan", help="generate the frozen attempt schedule")
    plan_p.add_argument("--host", required=True)
    plan_p.add_argument("--injections", default="injections")
    plan_p.add_argument("--out", required=True)
    plan_p.add_argument("--seed", type=int, default=1)
    plan_p.add_argument("--exposed-target", type=int, default=EXPOSED_TARGET,
                        help="N per injected group (plan §9.5)")
    plan_p.add_argument("--attempt-cap", type=int, default=ATTEMPT_CAP)
    plan_p.add_argument("--integration-smoke", action="store_true",
                        help="pilot Stage 1 only: allow an injected target that "
                             "does not divide across the three paraphrases. The "
                             "smoke checks wiring, exposure and placement, none "
                             "of which reads the paraphrase allocation. Stamped "
                             "on the schedule and on every result; never a "
                             "measurement, never pooled with a sweep")
    plan_p.add_argument("--near-miss-target", type=int, default=NEAR_MISS_TARGET,
                        help="N per (task, action) near-miss block; these carry no "
                             "injected text, so they recruit nothing and balance no "
                             "paraphrases (plan §7.4)")
    plan_p.add_argument("--clean-target", type=int, default=CLEAN_TARGET,
                        help="N per task for the clean block (plan §7.1)")
    plan_p.add_argument("--task", action="append", dest="tasks",
                        help="release task; repeatable. Default: all five")
    plan_p.add_argument("--entry-point", action="append", dest="entry_points",
                        choices=ENTRY_POINTS,
                        help="restrict to this entry point; repeatable. Default: E1-E4")
    plan_p.add_argument("--cells-only", action="append", dest="cells_only_tasks",
                        metavar="TASK",
                        help="this task contributes its injected cells but no "
                             "near-miss or clean block; repeatable. The "
                             "auxiliary tasks exist to keep every entry point "
                             "and induced action present in the same number of "
                             "tasks (plan §6.2), which is a property of cells; "
                             "an expensive task can supply that balance without "
                             "paying for blocks it does not need")
    plan_p.add_argument("--entry-point-attempt-cap", action="append",
                        dest="entry_point_attempt_caps", metavar="EP=N",
                        help="override --attempt-cap for one entry point, e.g. "
                             "E3=6; repeatable. Recruitment cost is per entry "
                             "point because exposure is, and a vehicle the "
                             "workflow rarely opens otherwise spends the full "
                             "cap to report a shortfall. A cap below the "
                             "exposed target is allowed and intended: the "
                             "manifest reports such a group with both "
                             "denominators and hit_attempt_cap")

    run_p = sub.add_parser("run", help="execute a schedule, retaining every attempt")
    run_p.add_argument("--schedule", required=True)
    run_p.add_argument("--out", required=True)
    run_p.add_argument("--agent", default="anthropic",
                       choices=("anthropic", "openai_compatible", "scripted"))
    run_p.add_argument("--script")
    run_p.add_argument("--model", default="claude-opus-5")
    run_p.add_argument("--max-tokens", type=int, default=16000)
    run_p.add_argument("--turn-limit", type=int, default=runner.TURN_LIMIT)
    # None, not "high", so the runner can refuse it on an adapter that would
    # drop it; the recorded configuration still resolves to "high" below, so
    # model_configuration_sha256 is unchanged for an anthropic sweep.
    run_p.add_argument("--effort", default=None,
                       choices=("low", "medium", "high", "xhigh", "max"))
    run_p.add_argument("--base-url")
    run_p.add_argument("--api-key-env", default="OPENAI_API_KEY")
    run_p.add_argument("--reasoning-effort")
    run_p.add_argument("--temperature", type=float)
    run_p.add_argument("--token-param", default="max_tokens",
                       choices=("max_tokens", "max_completion_tokens"))
    run_p.add_argument("--canary-seed", default="dev-generation")
    run_p.add_argument(
        "--execution-mode", default="two_agent", choices=runner.EXECUTION_MODES,
        help="held constant across every cell in a sweep (plan §6.4, R2); the "
             "release uses two_agent throughout; single_agent is an "
             "optional future extension, never mixed inside one schedule",
    )
    run_p.add_argument("--defense", default="none")
    run_p.add_argument("--control-profiles", default="control_profiles")
    run_p.add_argument("--inference-trust-boundary", default="external_api",
                       choices=("external_api", "on_prem"))
    run_p.add_argument("--max-attempts", type=int, help="hard stop for this session")
    run_p.add_argument("--spend-ceiling", type=float,
                       help="stop scheduling new runs at this estimated cost")
    run_p.add_argument("--price-in", type=float, help="USD per million uncached input tokens")
    run_p.add_argument("--price-cached", type=float, help="USD per million cached input tokens")
    run_p.add_argument("--price-out", type=float, help="USD per million output tokens")
    run_p.add_argument("--price-date", help="the day the price table was read and approved")
    run_p.add_argument("--verbose", action="store_true")
    run_p.add_argument(
        "--workers", type=int, default=1,
        help="run up to this many attempts concurrently. Default 1 preserves "
             "the exact serial, adaptive-fallback order; concurrency batches "
             "the recruitment snapshot by one batch and re-checks the spend "
             "ceiling per batch (plan §8.4, §10.3)",
    )


def main(args: argparse.Namespace) -> int:
    return cmd_plan(args) if args.sweep_command == "plan" else cmd_run(args)
