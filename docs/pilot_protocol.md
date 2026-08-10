# Pilot protocol

Frozen at milestone 7, **before any pilot data exists** (plan §11.2). A pilot
whose stopping rules are written after its numbers are visible is not a pilot,
it is a first look at the result.

Pilot failures are implementation defects, not benchmark results. Pilot runs are
never pooled with the sweep they precede, and the pilot budget is its own line
in the cost manifest rather than being hidden inside sweep contingency.

---

## Stage 1 — integration smoke

One run per applicable condition and populated cell, using a model **outside the
confirmatory family set**. For `v0.5` that is 12 attacked + 12 benign + 3 inert
+ 4 near-miss + 1 clean = 32 runs.

```sh
python -m taskbound.runner sweep plan \
  --host hosts/h1_failed_job --out pilot/smoke_schedule.json --seed 1 \
  --exposed-target 1 --attempt-cap 3

python -m taskbound.runner sweep run \
  --schedule pilot/smoke_schedule.json --out pilot/smoke \
  --agent <adapter> --model <a model outside the confirmatory set> --verbose
```

It must show, and each is a hard stop rather than a note:

| Check | How it is read |
|-------|----------------|
| Exposure where structurally required | E1 exposure at or near 1 in `sweep run`'s manifest. E2 and E3 are expected to be lower — that is a result, not a failure |
| No silent injection failures | Every attempt records a `placement`; a placement class resolving to nothing is already a hard error, and this confirms it in a live run |
| No literal canaries or payload markers in the repository | `python -m taskbound.runner validate` plus a repository-wide scan for `TB-CANARY-` and `TB-MARKER-` |
| No missing result fields | Every result carries schema version, release, commit, host and injection hashes, model identifiers, prompt and tool hashes, sampling, seeds, request ids, timestamps, usage |
| Passing criterion calibration | `python -m taskbound.runner calibrate --host hosts/h1_failed_job`: five positive references pass, five negative fixtures fail |
| Deterministic backend replay | Two runs from the same seed and inputs produce the same backend behaviour |
| Oracle sanity | `python -m taskbound.runner audit sample` runs over the smoke results and produces a worksheet with every stratum represented |

## Stage 2 — sizing pilot

Repeated H1 attacked and benign blocks, **balanced over all three paraphrases**,
against the same out-of-set model. Six exposed per cell is enough to measure
what this stage measures; it is not enough to estimate anything reportable and
nothing from it is reported.

```sh
python -m taskbound.runner sweep plan \
  --host hosts/h1_failed_job --out pilot/sizing_schedule.json --seed 2 \
  --exposed-target 6 --attempt-cap 18
```

It measures four things, in this order of importance:

1. **Exposure per entry point.** This sets how many attempts the real sweep
   needs, and E2 and E3 are where over-recruitment costs money. It is also a
   reported result in its own right.
2. **Overdispersion / clustering.** The between-paraphrase, between-injection,
   and between-placement variance components, which feed the power gate. They
   replace `CLUSTERING_RANGE` in `taskbound/power.py`.
3. **Tokens, turns, and cost per run**, measured rather than assumed, written
   into the cost manifest against a provider price table dated on the day of
   approval.
4. **Inconclusive rate and its reasons**, because attrition biases every rate
   and the turn limit must be set before results are visible, not after.

## Stage 3 — the two gates

Both must pass before the main pre-registration is signed.

### Power gate

```sh
python -m taskbound.runner power --simulations 500 --out pilot/power.json
```

The simulation uses the exact allocation and the exact analysis function the
aggregator uses, and must show **at least 80% power across the clustering
range** for attack susceptibility, scope selectivity, and both main effects, at
the minimum effects of interest named in the pre-registration.

The gate is the *worst case* across the range, not the best guess within it: a
design with power at a paraphrase sd of 0.2 and none at 0.9 has a power claim
that depends on a number nobody has measured. Once the sizing pilot has
measured the clustering, the range narrows to what was observed and the gate is
re-run against it.

**N = 48 is a floor.** The pilot may raise it; it may not lower it. If the gate
fails at N = 48, the options are a larger N, a larger declared minimum effect
of interest, or the §10.5 scope-reduction ladder — never a quieter claim about
the same data.

### Cost gate

Expected cost, hard-cap cost, and a 20% contingency are approved before the
sweep starts, from the pilot's measured usage:

```text
cost = uncached_input * rate_in + cached_input * rate_cached
     + output * rate_out + provider-specific request charges
```

The sweep runner enforces per-run token and turn caps and a sweep spend ceiling
(`--spend-ceiling`, with `--price-in`, `--price-cached`, `--price-out`, and
`--price-date`). Batch and prompt caching may be used only after a smoke test
shows byte-identical prompts and equivalent tool behaviour; their savings are
measured, not assumed.

## What the pilot may and may not change

| May | May not |
|-----|---------|
| Raise N | Lower N |
| Set the turn limit, token caps, and attempt cap | Change the estimands |
| Narrow the clustering range to what it measured | Change the factor definitions |
| Trigger a declared step of the §10.5 scope-reduction ladder | Add or remove a condition |
| Fix implementation defects it exposes | Change the analysis after effect tables have been viewed |

The last one is the one that matters. Once a table of attacked-versus-benign
rates has been looked at, every subsequent choice is a choice made with results
in view, and the pre-registration is what separates the two.
