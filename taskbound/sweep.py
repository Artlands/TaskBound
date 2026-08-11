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
from collections.abc import Sequence
from typing import Any

from . import runner

EXPOSED_TARGET = 9
# Three attempts per exposed run, not two: E3's exposure is around 0.40, so a
# cap of 2N leaves its cells short of target and the entry-point contrast is
# read off the arm that got starved. 3N lets the least-exposed entry point
# reach the same N as the rest.
ATTEMPT_CAP = 3 * EXPOSED_TARGET
BLOCK = 3  # one attempt per paraphrase, so a stopped group stays balanced
PARAPHRASES = ("p1", "p2", "p3")
INERT_PARAPHRASES = ("i1", "i2", "i3")
DEFAULT_RELEASE_TASKS = ("t1_failed_job",)


# --- planning ------------------------------------------------------------
def plan(
    host_dir: str,
    injections_dir: str,
    seed: int,
    exposed_target: int = EXPOSED_TARGET,
    attempt_cap: int = ATTEMPT_CAP,
    tasks_filter: Sequence[str] | None = None,
    entry_points: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Freeze the attempt schedule for one release's scope.

    A release is a subset of what the host carries. The compact `v1.0` release
    is the core task at E1-E4; auxiliary tasks remain available for future
    expansions. The scope is named here rather than inferred, so a schedule
    records what it was planned to cover and a later sweep cannot silently widen
    it.
    """
    if exposed_target % len(PARAPHRASES):
        raise SystemExit("exposed target must divide evenly across the three paraphrases")
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

    tasks = {tid: runner.load_task(host, tid) for tid in declared}
    for task_id, task in tasks.items():
        cells = [c for c in task["cells"] if keep_ep is None or c[:2] in keep_ep]
        if not cells:
            continue
        for cell in cells:
            for condition, kind in (("attacked", "attack"), ("benign", "benign")):
                texts = [injections[task_id, cell, kind, p] for p in PARAPHRASES]
                groups[f"{condition}|{task_id}|{cell}"] = _group(
                    condition, exposed_target, attempt_cap, texts,
                    task=task_id, cell=cell, recruits=True,
                )
        if task.get("role") == "core":
            for entry in sorted({c[:2] for c in cells}):
                texts = [injections[task_id, entry, "inert", p] for p in INERT_PARAPHRASES]
                groups[f"inert|{task_id}|{entry}"] = _group(
                    "inert", exposed_target, attempt_cap, texts,
                    task=task_id, cell=entry, recruits=True,
                )
        for action in sorted({c[2:] for c in cells}):
            groups[f"near_miss|{task_id}|{action}"] = _group(
                "near_miss", exposed_target, exposed_target, [],
                task=task_id, near_miss_action=action, recruits=False,
            )
        groups[f"clean|{task_id}"] = _group(
            "clean", exposed_target, exposed_target, [], task=task_id, recruits=False
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


def _sweep_id(schedule: dict[str, Any]) -> str:
    payload = json.dumps(
        {k: schedule[k] for k in ("host", "seed", "exposed_target", "attempt_cap", "attempts")},
        sort_keys=True,
    )
    return "sweep_" + hashlib.sha256(payload.encode()).hexdigest()[:12]


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

    # R2 (plan §3, §6.4): the execution model is held constant across the cells
    # being compared. The compact release fixes two-agent mode throughout; a
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
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0}
    for record in state["records"]:
        _add_usage(usage, record.get("outcome", {}).get("usage") or {})

    started = _dt.datetime.now(_dt.timezone.utc).isoformat()
    stopped_early = None

    for attempt in schedule["attempts"]:
        group = schedule["groups"][attempt["group"]]
        counts = state["counts"].setdefault(attempt["group"], _empty_counts())
        if attempt["attempt_id"] in state["done"]:
            continue
        if _group_complete(group, counts):
            continue
        if args.max_attempts and state["attempted"] >= args.max_attempts:
            stopped_early = "max_attempts"
            break
        if args.spend_ceiling and _cost(usage, args) >= args.spend_ceiling:
            stopped_early = "spend_ceiling"
            break

        resolved_attempt = _resolve_attempt(group, counts, attempt)
        record = _run_one(schedule, resolved_attempt, args)
        record["sweep"] = {
            "sweep_id": schedule["sweep_id"],
            "attempt_id": attempt["attempt_id"],
            "group": attempt["group"],
            "order": attempt["order"],
            "block": attempt["block"],
        }
        _write(args.out, attempt["attempt_id"], record)

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

    return _manifest(schedule, args, state, usage, started, stopped_early)


def _group_complete(group: dict[str, Any], counts: dict[str, Any]) -> bool:
    """Stop once every paraphrase in the group has what it needs.

    Recruitment is on *exposed* runs, so an entry point the agent rarely opens
    costs attempts rather than silently reporting a smaller sample.
    """
    if counts["attempted"] >= group["attempt_cap"]:
        return True
    if group["recruits_to_exposure"]:
        target = group["target"] // len(group["paraphrases"])
        return all(counts["exposed_by_paraphrase"].get(p, 0) >= target
                   for p in group["paraphrases"])
    return counts["attempted"] >= group["target"]


def _resolve_attempt(
    group: dict[str, Any], counts: dict[str, Any], attempt: dict[str, Any]
) -> dict[str, Any]:
    if not group["recruits_to_exposure"]:
        return attempt
    target = group["target"] // len(group["paraphrases"])
    start = group["paraphrases"].index(attempt["paraphrase"])
    options = attempt.get("paraphrase_options") or (
        group["paraphrases"][start:] + group["paraphrases"][:start]
    )
    paraphrase = next(
        p for p in options if counts["exposed_by_paraphrase"].get(p, 0) < target
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


def _manifest(schedule, args, state, usage, started, stopped_early) -> dict[str, Any]:
    groups = {}
    for name, group in sorted(schedule["groups"].items()):
        counts = state["counts"].get(name, _empty_counts())
        achieved = counts["exposed"] if group["recruits_to_exposure"] else counts["attempted"]
        paraphrase_target = (
            group["target"] // len(group["paraphrases"])
            if group["recruits_to_exposure"] else None
        )
        exposed_by_paraphrase = {
            p: counts["exposed_by_paraphrase"].get(p, 0) for p in group["paraphrases"]
        }
        shortfall_by_paraphrase = {
            p: max(0, paraphrase_target - exposed_by_paraphrase[p])
            for p in group["paraphrases"]
        }
        reached_target = (
            all(shortfall == 0 for shortfall in shortfall_by_paraphrase.values())
            if group["recruits_to_exposure"] else achieved >= group["target"]
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
            k: schedule[k]
            for k in ("host", "seed", "exposed_target", "attempt_cap", "attempts")
        },
        "agent": {
            "adapter": args.agent, "model": args.model, "base_url": args.base_url,
            "effort": args.effort, "turn_limit": args.turn_limit, "max_tokens": args.max_tokens,
        },
        "defense": args.defense,
        "execution_mode": args.execution_mode,
        "attempt_ids": [attempt["attempt_id"] for attempt in schedule["attempts"]],
        "stopped_early": stopped_early,
        "groups": groups,
        "totals": {
            "attempted_this_session": state["attempted"],
            "attempted_total": sum(g["attempted"] for g in groups.values()),
            "exposed_total": sum(g["exposed"] for g in groups.values()),
            "groups_short_of_target": sorted(n for n, g in groups.items() if not g["reached_target"]),
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
def cmd_plan(args: argparse.Namespace) -> int:
    tasks = args.tasks if args.tasks is not None else list(DEFAULT_RELEASE_TASKS)
    schedule = plan(args.host, args.injections, args.seed, args.exposed_target,
                    args.attempt_cap, tasks, args.entry_points)
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
    print(f"  wrote {path}")
    return 0


def add_arguments(sub) -> None:
    plan_p = sub.add_parser("plan", help="generate the frozen attempt schedule")
    plan_p.add_argument("--host", required=True)
    plan_p.add_argument("--injections", default="injections")
    plan_p.add_argument("--out", required=True)
    plan_p.add_argument("--seed", type=int, default=1)
    plan_p.add_argument("--exposed-target", type=int, default=EXPOSED_TARGET,
                        help="N per cell for the compact release (plan §9.5)")
    plan_p.add_argument("--attempt-cap", type=int, default=ATTEMPT_CAP)
    plan_p.add_argument("--task", action="append", dest="tasks",
                        help="release task; repeatable. Default: t1_failed_job only")
    plan_p.add_argument("--entry-point", action="append", dest="entry_points",
                        choices=("E1", "E2", "E3", "E4"),
                        help="restrict to this entry point; repeatable. Default: E1-E4")

    run_p = sub.add_parser("run", help="execute a schedule, retaining every attempt")
    run_p.add_argument("--schedule", required=True)
    run_p.add_argument("--out", required=True)
    run_p.add_argument("--agent", default="anthropic",
                       choices=("anthropic", "openai_compatible", "scripted"))
    run_p.add_argument("--script")
    run_p.add_argument("--model", default="claude-opus-5")
    run_p.add_argument("--max-tokens", type=int, default=16000)
    run_p.add_argument("--turn-limit", type=int, default=30)
    run_p.add_argument("--effort", default="high",
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
             "compact release uses two_agent throughout; single_agent is an "
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


def main(args: argparse.Namespace) -> int:
    return cmd_plan(args) if args.sweep_command == "plan" else cmd_run(args)
