# BUG-001 — `canary_generation` is recorded on every run and enforced nowhere

**Severity:** high (silent data-validity defect; no error, no warning, wrong number)
**Component:** `taskbound/sweep.py` (resume guard), `taskbound/aggregate.py` (analysis frame)
**Found:** 2026-09-01, while resuming the `local-deepseek-v4-flash` release sweep
**Status:** FIXED 2026-09-01 — see *What was implemented* below

## Summary

Every result file carries `canary_generation`, the 12-hex digest of the
`--canary-seed` (`runner.derive_secrets`: `sha256(canary_seed)[:12]`). Nothing
ever reads it back.

* `sweep._agent_configuration` — the dict the resume guard compares against
  prior rows — does not include the canary seed or its generation, so resuming a
  sweep under a **different** seed is accepted without a word.
* `aggregate` never references `canary_generation` at all. It is not in `_row`,
  not in the analysis frame, not in the exported CSV, and not in
  `validate_release_binding`.

So a results directory can span two canary generations, and every downstream
tool will treat it as one measurement.

This matters because the canary values are substituted into the workspace at
load time and *define what counts as a leak*. `realization.leak` from two
generations is not the same measurement, and `realization.level` (which
distinguishes a level-3 canary egress from a level-2 read) is derived from them.

`preregistration.draft.json` registers the field:

```json
"canary_generation": {
  "seed_source": "environment variable, never committed",
  "generation_id_recorded_per_run": true,
  "release_seed": "PENDING_AT_SIGNING"
}
```

Recording it per run is implemented. Binding the release to it is not.

## Why this is the *one* silent gap

Every other configuration change is refused up front. Only the seed is not:

| Changed on resume | Result |
|---|---|
| model / adapter / script | **refused** — "already has runs under a different agent configuration" |
| `--execution-mode` | **refused** — "would mix execution models inside one schedule (plan §6.4, R2)" |
| base_url, api_key_env, temperature, reasoning_effort, token_param, max_tokens, turn_limit | **refused** (all in `_agent_configuration`) |
| **`--canary-seed`** | **silently accepted** |

The README's stated safety net is detection after the fact — results record
`canary_generation` "so contaminated runs stay identifiable after the fact" —
but no code performs that detection, so it falls entirely to whoever remembers
to run the check by hand.

## Reproduction

Fully offline, using the scripted adapter. Two `sweep run` invocations against
one output directory, differing **only** in `--canary-seed`:

```sh
.venv/bin/python -m taskbound.runner sweep plan \
  --host hosts/site_a --task t1_failed_job --entry-point E1 \
  --exposed-target 3 --attempt-cap 3 --near-miss-target 1 --clean-target 1 \
  --out /tmp/mini.json --seed 1

for SEED in SEED-AAA SEED-BBB; do
  .venv/bin/python -m taskbound.runner sweep run --schedule /tmp/mini.json \
    --out /tmp/out --agent scripted \
    --script fixtures/scripts/two_agent_worker_complied.json \
    --execution-mode two_agent --canary-seed "$SEED" --max-attempts 2
done

.venv/bin/python -c "
import json,glob,os,collections
c=collections.Counter(json.load(open(f))['canary_generation']
  for f in glob.glob('/tmp/out/*.json')
  if not os.path.basename(f).startswith('sweep_manifest'))
print(dict(c))"
```

Observed — both runs succeed, and one results directory now holds two
generations:

```
{'93975386c065': 2, '509dbf297198': 2}
DISTINCT CANARY GENERATIONS IN ONE RESULTS DIR: 2
```

Control, same directory: changing the script is refused, and changing the
execution mode is refused. Only the seed slips through.

## What was implemented

Two guards, one on each side.

**1. The sweep refuses to resume across a generation boundary** (`sweep.execute`,
beside the existing execution-mode check, which it deliberately mirrors):

```python
expected_generation = runner.canary_generation(args.canary_seed)
prior_generations = sorted(
    {r.get("canary_generation") for r in state["records"]} - {None}
)
if prior_generations and prior_generations != [expected_generation]:
    raise SystemExit(...)
```

Note this is **not** the fix originally suggested below. Putting the seed — or
its generation — into `_agent_configuration` would work for new directories but
would refuse to resume every directory written before the change, because the
guard compares whole serialised dicts. Comparing the `canary_generation` field
each result *already records* has none of that problem: it works retroactively
on the 240 rows of the first sweep, it needs no back-compatibility shim, and it
checks the recorded value rather than a copy of it. `runner.canary_generation`
was factored out of `derive_secrets` so there is one derivation, and so a caller
that only needs to compare generations never holds a seed's canary values.

**2. `aggregate` refuses a frame that mixes generations inside one model
family** (`aggregate.validate_canary_generations`, called from `load_frame`
beside `validate_release_scope`). Scoped per family on purpose: whether the
release runs one seed for every family or one seed per family is an allocation
decision and both are legitimate; two generations inside a single family never
are. A row with no `canary_generation` is treated as unverifiable rather than as
a mismatch, so an older frame still loads.

`canary_generation` is now carried into `_row`, so it reaches the analysis frame
instead of stopping at the raw result.

**Regression tests** (there were none — the suite passed with the defect
present): `test_a_sweep_cannot_resume_under_another_canary_seed`,
`test_resuming_under_the_same_canary_seed_is_allowed` in `tests/test_sweep.py`;
`test_ingestion_rejects_two_canary_generations_inside_one_family`,
`test_a_frame_written_before_the_generation_field_still_loads` in
`tests/test_analysis.py`.

Verified against the original repro: the second `sweep run` under `SEED-BBB` now
exits with

```
sweep sweep_27c8320eec8f already has runs under canary generation 93975386c065;
the seed passed here derives 509dbf297198. Resuming would split one results
directory across two canary generations...
```

and the directory is left holding one generation instead of two.

## Originally suggested fix (superseded)


Add the **derived generation id**, not the seed, to the compared configuration:

```python
# taskbound/sweep.py, _agent_configuration
configuration = {
    "adapter": args.agent,
    "model": args.model,
    "max_tokens": args.max_tokens,
    "turn_limit": args.turn_limit,
    "canary_generation": hashlib.sha256(args.canary_seed.encode()).hexdigest()[:12],
}
```

The generation id rather than the seed, because `_agent_configuration` is
serialised into `sweep.agent_configuration` on **every result file**, and the
seed is a secret that must never be written to disk (plan §12). The generation
id is already written to every result, so this adds no new disclosure.

Note this changes the compared configuration dict, so it will refuse to resume
existing directories that predate the change. Either gate it on the field being
absent in prior rows, or treat it as a schema bump.

Second, independently: have `aggregate` refuse a frame spanning more than one
generation. Carry `canary_generation` into `_row`, and reject in
`validate_release_scope` (which already refuses out-of-scope rows) rather than
only under a signed preregistration — an unsigned run should not be able to
produce a mixed-generation report either.

## Workaround until fixed

After any resume, before aggregating:

```sh
.venv/bin/python -c "
import json,glob,collections
print(collections.Counter(json.load(open(f))['canary_generation']
      for f in glob.glob('results/<model>/*.json')))"
# must print exactly one key
```

This is what `results/RESUME_local-deepseek-v4-flash.md` instructs, and it was
run before and after the 2026-09-01 resume — one generation, `249dfc19e9fb`,
across all rows.
