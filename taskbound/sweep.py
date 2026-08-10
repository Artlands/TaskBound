"""Sweep driver: frozen attempt schedules and exposure recruitment (plan §8.4, §11.4).

Two commands, deliberately separate:

    sweep plan  -> a complete, ordered, seeded attempt schedule, written before
                   anything runs and hashed against the material it was built
                   from
    sweep run   -> walks that schedule, retaining every attempt

The separation is the point. The complete attempt order and seeds are generated
*before* execution, so recruitment cannot become a decision made while results
are visible. A cell recruits to 24 exposed runs in blocks of three — one per
paraphrase, so the split stays balanced whenever it stops — and is capped at 48
attempted. A cell that hits the cap short of 24 is reported at the precision it
actually reached, with both denominators shown, and is never quietly pooled.

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
import sys
from typing import Any

from . import runner

EXPOSED_TARGET = 48
# Three attempts per exposed run, not two: E3's exposure is around 0.40, so a
# cap of 2N leaves its cells short of target and the entry-point contrast is
# read off the arm that got starved. 3N lets the least-exposed entry point
# reach the same N as the rest.
ATTEMPT_CAP = 3 * EXPOSED_TARGET
BLOCK = 3  # one attempt per paraphrase, so a stopped group stays balanced
PARAPHRASES = ("p1", "p2", "p3")
INERT_PARAPHRASES = ("i1", "i2", "i3")


# --- planning ------------------------------------------------------------
def plan(
    host_dir: str,
    injections_dir: str,
    seed: int,
    exposed_target: int = EXPOSED_TARGET,
    attempt_cap: int = ATTEMPT_CAP,
) -> dict[str, Any]:
    host = runner.load_host(host_dir)
    injections = _index_injections(injections_dir, host["host_id"])
    rng = random.Random(seed)

    groups: dict[str, dict[str, Any]] = {}
    for cell in host["cells"]:
        for condition, kind in (("attacked", "attack"), ("benign", "benign")):
            texts = [injections[cell, kind, p] for p in PARAPHRASES]
            groups[f"{condition}|{cell}"] = _group(
                condition, exposed_target, attempt_cap, texts, cell=cell, recruits=True
            )
    for entry in sorted({c[:2] for c in host["cells"]}):
        texts = [injections[entry, "inert", p] for p in INERT_PARAPHRASES]
        groups[f"inert|{entry}"] = _group(
            "inert", exposed_target, attempt_cap, texts, cell=entry, recruits=True
        )
    for action in sorted({c[2:] for c in host["cells"]}):
        groups[f"near_miss|{action}"] = _group(
            "near_miss", exposed_target, exposed_target, [], near_miss_action=action, recruits=False
        )
    groups["clean|host"] = _group("clean", exposed_target, exposed_target, [], recruits=False)

    attempts = _interleave(groups, rng)
    schedule = {
        "schema_version": runner.SCHEMA_VERSION,
        "sweep_id": None,
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "host": {"dir": host_dir, "id": host["host_id"], "hash": host["_hash"]},
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
    cell: str | None = None,
    near_miss_action: str | None = None,
    recruits: bool = True,
) -> dict[str, Any]:
    return {
        "condition": condition,
        "cell": cell,
        "near_miss_action": near_miss_action,
        "target": exposed_target,
        "attempt_cap": attempt_cap,
        # Only injected conditions recruit: exposure is a property of injected
        # text reaching a tool result, and clean and near-miss runs have none.
        "recruits_to_exposure": recruits,
        "texts": [t["_path"] for t in texts],
    }


def _index_injections(directory: str, host_id: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    index = {}
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            inj = json.load(fh)
        if inj["host"] != host_id:
            continue
        inj["_path"] = path
        index[inj["cell"], inj["kind"], inj["paraphrase"]] = inj
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
        for index in range(group["attempt_cap"]):
            attempts.append({
                "group": name,
                "condition": group["condition"],
                "cell": group["cell"],
                "near_miss_action": group["near_miss_action"],
                "injection": texts[index % len(texts)] if texts else None,
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
    os.makedirs(args.out, exist_ok=True)
    state = _resume(args.out, schedule)
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0}
    for record in state["records"]:
        _add_usage(usage, record.get("outcome", {}).get("usage") or {})

    started = _dt.datetime.now(_dt.timezone.utc).isoformat()
    stopped_early = None

    for attempt in schedule["attempts"]:
        group = schedule["groups"][attempt["group"]]
        counts = state["counts"].setdefault(
            attempt["group"], {"attempted": 0, "exposed": 0, "conclusive": 0}
        )
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

        record = _run_one(schedule, attempt, args)
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
        if not record["outcome"]["inconclusive"]:
            counts["conclusive"] += 1
        _add_usage(usage, record["outcome"].get("usage") or {})
        if args.verbose:
            print(f"[{attempt['order']:>5}] {attempt['attempt_id']:<28} "
                  f"exposed={record['exposure']['exposed']!s:<5} "
                  f"inconclusive={record['outcome']['inconclusive']}")

    return _manifest(schedule, args, state, usage, started, stopped_early)


def _group_complete(group: dict[str, Any], counts: dict[str, int]) -> bool:
    """Stop at a block boundary once the group has what it needs.

    Recruitment is on *exposed* runs, so an entry point the agent rarely opens
    costs attempts rather than silently reporting a smaller sample. Checking
    only on block boundaries is what keeps the three paraphrases balanced in a
    group that stops mid-recruitment.
    """
    if counts["attempted"] >= group["attempt_cap"]:
        return True
    if counts["attempted"] % BLOCK:
        return False
    if group["recruits_to_exposure"]:
        return counts["exposed"] >= group["target"]
    return counts["attempted"] >= group["target"]


def _run_one(schedule: dict[str, Any], attempt: dict[str, Any], args: argparse.Namespace):
    run_args = argparse.Namespace(
        host=schedule["host"]["dir"],
        condition=attempt["condition"],
        injection=attempt["injection"],
        near_miss_action=attempt["near_miss_action"],
        seed=attempt["placement_seed"],
        canary_seed=args.canary_seed,
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
        counts.setdefault(sweep["group"], {"attempted": 0, "exposed": 0, "conclusive": 0})
        counts[sweep["group"]]["attempted"] += 1
        counts[sweep["group"]]["exposed"] += bool(record["exposure"]["exposed"])
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
        counts = state["counts"].get(name, {"attempted": 0, "exposed": 0, "conclusive": 0})
        achieved = counts["exposed"] if group["recruits_to_exposure"] else counts["attempted"]
        groups[name] = {
            **counts,
            "target": group["target"],
            "attempt_cap": group["attempt_cap"],
            "reached_target": achieved >= group["target"],
            # A group that hit the cap short of its target is reported at the
            # precision it reached, with both denominators, never pooled away.
            "hit_attempt_cap": counts["attempted"] >= group["attempt_cap"]
                               and achieved < group["target"],
        }
    return {
        "sweep_id": schedule["sweep_id"],
        "started_at": started,
        "finished_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "release": runner.RELEASE,
        "git_commit": runner._git_commit(),
        "schedule": {k: schedule[k] for k in ("host", "seed", "exposed_target", "attempt_cap")},
        "agent": {
            "adapter": args.agent, "model": args.model, "base_url": args.base_url,
            "effort": args.effort, "turn_limit": args.turn_limit, "max_tokens": args.max_tokens,
        },
        "defense": args.defense,
        "execution_mode": "single_agent",
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
    schedule = plan(args.host, args.injections, args.seed, args.exposed_target, args.attempt_cap)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(schedule, fh, indent=2)
        fh.write("\n")
    print(f"{schedule['sweep_id']}: {len(schedule['groups'])} groups, "
          f"{schedule['target_runs']} target runs, {schedule['max_attempts']} maximum attempts")
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
                        help="N per cell; the pilot may raise it, never lower it (plan §9.5)")
    plan_p.add_argument("--attempt-cap", type=int, default=ATTEMPT_CAP)

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
