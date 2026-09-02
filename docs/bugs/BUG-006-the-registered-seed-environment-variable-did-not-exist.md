# BUG-006 — the canary seed's registered environment variable was never read

**Severity:** high (the documented release procedure derives canaries from a public seed, silently)
**Component:** `taskbound/runner.py` (`--canary-seed`), `taskbound/sweep.py`, `docs/execution_plan.md` phase 5
**Found:** 2026-09-02, noticing the seed of a running pilot in `ps` output
**Status:** FIXED 2026-09-02 — see *What was implemented* below

## Summary

`preregistration.draft.json` registers how the seed reaches a run:

```json
"canary_generation": {
  "seed_source": "environment variable, never committed",
  "generation_id_recorded_per_run": true,
  "release_seed": "PENDING_AT_SIGNING"
}
```

No environment variable existed. `runner.py:737` and `sweep.py:1153` each
declared `--canary-seed` with `default="dev-generation"`, and nothing in the
package read `os.environ` for a seed at all — the only `os.environ` access was
`agents.py:489`, for the API key.

Two consequences, one worse than the other.

**The documented release command silently used the public default.** Phase 5 of
`execution_plan.md` reads:

```sh
export TB_CANARY_SEED=<release_seed>
.venv/bin/python -m taskbound.runner sweep run \
  --schedule schedules/v1_sweep.json --out results/<family> ...
```

The export was decoration. Nothing consumed it, so every run took
`dev-generation` — a string committed to this repository — and the canaries and
payload markers deciding what counts as a leak became computable by anyone with
a clone. Nothing in the results says so, beyond a `canary_generation` of
`312b5d159d0e` that a reader would have to know to distrust.

**The workaround leaks the seed.** The README's own example closed the gap by
expanding the variable into the command line, `--canary-seed "$TB_CANARY_SEED"`.
Arguments are world-readable: on a shared node any other user running `ps` sees
the seed of every sweep in progress, and with it every canary value the run is
watching for.

## Repro

```sh
export TB_CANARY_SEED=a-real-secret-seed
python -m taskbound.runner sweep run --schedule s.json --out o ...   # before the fix
python - <<'PY'
import json, hashlib, glob
row = json.load(open(sorted(glob.glob("o/*.json"))[0]))
print(row["canary_generation"], hashlib.sha256(b"dev-generation").hexdigest()[:12])
PY
# both print 312b5d159d0e: the exported seed reached nothing
```

## Blast radius

None realised. Both sweeps run to date passed `--canary-seed` explicitly, so
`results/local-deepseek-v4-flash/` carries generation `249dfc19e9fb` and the
Stage 1 pilot carries `487628023980`; neither is `312b5d159d0e`. The defect was
latent in the procedure, waiting for the first person to follow the plan as
written.

## What was implemented

`runner.resolve_canary_seed` resolves one seed for both entry points, from
`runner.main` immediately after `parse_args`, in this order:

1. `--canary-seed` when given — an explicit argument beats an ambient one — with
   a warning to stderr naming `TB_CANARY_SEED`, unless the flag is the public
   default (a scripted or throwaway run, which is what the flag is for).
2. `TB_CANARY_SEED`, stripped. A seed exported as `"$(< seedfile)"` carries the
   file's trailing newline, and a seed differing by whitespace derives a
   different generation — the sweep would then refuse to resume its own
   directory over an invisible character. **Set but empty is a hard error**
   rather than a fallback: someone who exported the variable believes they set a
   seed, and the failure mode of a silent default is a whole sweep whose leak
   detection is public knowledge.
3. `dev-generation`, unchanged, when neither is given.

The flag is passed through byte for byte — only the environment value is
stripped — so every existing results directory keeps deriving the generation its
rows already carry, and both defaults become `None` only so that "not given" is
distinguishable from "given the default".

Regression tests are in `tests/test_sweep.py`, beside the resume-guard tests
that share the seed's semantics: environment resolution, stripping, flag
precedence plus its warning, the empty-variable refusal, the unchanged default,
and one test driving `runner.main` to confirm the resolution happens before any
sweep code sees `args`.

`README.md` drops `--canary-seed` from the sweep example, marks the flag's
default as `$TB_CANARY_SEED, else dev-generation`, and states plainly that a
real seed must not be passed as an argument. `execution_plan.md` phase 5 needed
no change: it was already written against the behaviour this fix supplies.

## Why the suite did not catch it

Every test that exercises a seed builds an `argparse.Namespace` by hand and
calls `sweep.execute` directly, so no test crossed `runner.main` — the layer
where a seed is resolved and the only layer where an environment variable could
have been read. The registered `seed_source` was prose in a JSON file that no
test asserted against the code. This is the same shape as BUG-001: a field the
preregistration treats as binding, implemented nowhere, with a green suite.
