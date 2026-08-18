# TaskBound design history

Superseded design decisions and the evidence that retired them. Nothing here is
scheduled, claimed, or pooled by `v1.0-compact`; `development_plan.md` is the
specification and this file is the audit trail behind two of its amendments.

It exists because the registered analysis model **changed** after implementation.
A reader asking "why these three random effects and not five?" or "why is §7.5's
denominator `injection_id`?" needs the symptoms that prompted the repair, not
just its result. That evidence is here rather than in the spec so the spec can
read forward.

| Retired | Superseded by | Section |
|---------|---------------|---------|
| `v0.5`: E1–E3, single-agent, N = 24 | `v1.0-compact`: E1–E4, two-agent, N = 9 | §1 |
| `(1 \| host:cell)` and `(1 \| request_family)` in the primary model | `PRIMARY_RANDOM` = `request_family:paraphrase`, `injection_id`, `placement_id` | §2 |
| `induced_action` in the exposure model | `EXPOSURE_FIXED` = `condition * entry_point`, `model_family` | §3 |
| A private held-out fourth host | Per-release canary generation and recorded provenance | §4 |

---

## 1. The `v0.5` design

The earlier plan ran E1–E3 under a single-agent execution model at N = 24, with
a concurrent single-agent bridge arm so that E4 could later be added without
confounding entry point with execution model.

It was replaced for two reasons. The bridge arm cost runtime that bought only an
execution-mode contrast the release does not claim, and holding execution model
constant across every cell (plan §6.4, R2) removes the confound outright rather
than measuring it. Dropping to N = 9 with the full E1–E4 crossing then bought
the fourth entry point at roughly the same total cost.

`v0.5` material is not scheduled, pooled, or used as a bridge. Historical
single-agent runs never enter a confirmatory fit, and the compact release makes
no execution-mode claim. **N = 9 inherits no power conclusion from N = 24** and
must clear its own exact simulation (plan §9.5).

## 2. `host:cell` and `request_family` were aliased with the fixed block

Both were registered random effects in the primary model until fitting revealed
they estimated nothing.

Fitting the pre-registered model to data generated at a known `cell_sd` of 0.60
returns essentially zero, and stays there however much data it is given:

| Rows | fitted `cell_sd` (true 0.60) | fitted `paraphrase_sd` (true 0.90) | fitted `injection_sd` (true 0.35) |
|-----:|---:|---:|---:|
| 2,046 | 0.005 | 0.370 | 0.494 |
| 6,369 | 0.002 | 0.763 | 0.364 |
| 16,953 | 0.004 | 0.468 | 0.338 |

This is not a sample-size problem and not an optimiser failure — at the fitted
point the marginal log-likelihood is −562.43 against −562.85 with `cell_sd` held
at its true 0.60, so the surface genuinely prefers zero and is flat besides.

The cause is structural. `condition * entry_point * induced_action` expands to a
**saturated** fixed block, exactly one parameter per (condition, cell): every row
sharing a (condition, cell) has an identical fixed design row. At the `v0.5`
scope these diagnostics were run on — E1–E3, so 12 cells — that is 24 distinct
rows against a 12-level `host:cell` random intercept, which lies entirely inside
that span, leaving nothing for it to explain. Removing the interaction confirms
it:

| Fixed effects | fitted `host:cell` |
|---|---:|
| `condition * entry_point * induced_action` (24 columns) | 0.005 |
| `condition + entry_point + induced_action` (7 columns) | **0.555** |
| intercept only (1 column) | 0.835 |

`request_family` is aliased the same way and for the same reason: its four levels
are the four induced actions, which `induced_action` already carries as a fixed
effect. `request_family:paraphrase`, `injection_id`, and `placement_id` are not
aliased and do estimate.

**The compact release widens the same structure rather than escaping it.** E1–E4
gives 16 cells, so the block is 32 columns (33 with `model_family`) against a
would-be 16-level `host:cell`: the aliasing argument holds with more room, not
less. The aliasing follows from the saturated fixed block, not from the release
scope.

### Two consequences, both repaired

1. **§7.5's supersession rule could not do its job.** It compared
   between-paraphrase variance against between-cell variance, and the denominator
   was pinned near zero by construction rather than by evidence, so the ratio was
   large whatever the data said — 4,577 on the table above, against a true value
   of 2.25. It never misfired, but only because it demands the ratio's *interval*
   lie wholly above 1 and that interval spanned some 300 orders of magnitude. The
   rule was inert, and inert for a reason unrelated to the question it was written
   to answer.
2. **The clustering measurement refused to narrow**, because `host:cell` landed on
   the variance boundary every time. That was correct behaviour, but it meant the
   pilot could not discharge the power gate the way plan §9.5 assumes it will.

### "Costs nothing" was checked, not assumed

Refitting the same data with `host:cell` and `request_family` removed moves every
reported quantity by less than 0.005 on the probability scale:

| Contrast | 5 random effects | aliased two dropped |
|---|---|---|
| Susceptibility | +0.2799 [+0.2282, +0.3482] | +0.2805 [+0.2280, +0.3572] |
| Scope selectivity | −0.1121 [−0.1728, −0.0456] | −0.1116 [−0.1749, −0.0482] |
| Entry point E3−E1 | −0.3251 [−0.4258, −0.1972] | −0.3300 [−0.4423, −0.1920] |

The cell information is carried by the saturated fixed block either way, which is
the same fact that makes the random intercepts redundant.

### What the repair changed

Both components were dropped from `PRIMARY_RANDOM`, now
`request_family:paraphrase`, `injection_id`, `placement_id`. §7.5's denominator
became `injection_id`. Two effects worth recording:

* **The clustering measurement narrows again.** `host:cell` was the component that
  always landed on the variance boundary and triggered the refusal branch; with it
  gone, a full-sweep-sized frame resolves all three remaining components and their
  intervals cover their true values. The pilot can discharge the power gate.
* **`cell_sd` is now simulated but unmeasurable.** `generate` still draws a
  per-cell effect, because between-cell heterogeneity is real in the
  data-generating process even though the fitted model absorbs it into fixed
  effects. `runner clustering` carries the a-priori bracket through for that one
  knob while narrowing the other three, rather than reporting a number no fit
  produced.

### What §7.5's rule no longer tests

Until the amendment the denominator was `host:cell` and the rule read "wording
against structure". The cost of the repair is that **both terms are now wording**:
`request_family:paraphrase` is the paraphrase slot shared across the cells that
use it, `injection_id` the individual text. The comparison is systematic wording
against idiosyncratic wording, and it does not by itself establish that wording
outweighs structure — the structural term is now a fixed effect with no variance
component to divide by. A rule testing the original question would compare the
between-text component against the spread of the fitted cell means, a
random-effect-to-fixed-effect comparison that is not what is pre-registered. The
report's headline note and `variance_decomposition`'s docstring both say so where
the number is emitted, so the narrower claim cannot be read as the wider one.

## 3. `induced_action` was aliased in the exposure model

Registered in the exposure block and removed before signing. It was aliased with
the rest of the block on that model's own population: every inert run carries a
null `induced_action`, so that level's indicator *is* the `condition[inert]`
indicator `condition * entry_point` already supplies. The fixed block was rank
deficient — rank 7 of 8, on real records as well as synthetic — before any data
were seen.

Dropping it costs nothing substantively: exposure is whether the agent read the
vehicle, a property of the entry point and the placement rather than of what the
text went on to ask for. Standardization became equal weights over each entry
point's populated conditions. The primary model was untouched, inert runs stay in
the population, and the block is full rank with and without them.

**Both aliased terms were found the same way — by fitting the model rather than
by reading it.** That is why the aggregator now reports each fixed block's rank
beside its fit and names any duplicated columns, so a third one cannot reach a
signed registration unnoticed.

## 4. The private held-out host

Earlier drafts carried an unpublished fourth host, cell-matched against a public
one and reported beside the public result. It was removed with the multi-host
design.

It was never a contamination estimator: a public-versus-private gap carries host,
task, and publication-status shift together, so no gap could be attributed to
training exposure. What it offered was a descriptive sensitivity signal, at the
cost of a fourth workspace, an access-controlled bundle, and access logging.

It would also have been the wrong instrument structurally: with one host, an
unpublished second host is simply a second host, and plan §9.3 declines to claim
anything from cross-host comparison.

What replaced it is in plan §12 — per-release canary and marker generation,
recorded benchmark version and canary generation per result, and generator
provenance on every text. A causal contamination estimate needs paired public and
private variants of the *same* scenarios, frozen model snapshots or a
longitudinal design, and its own pre-registration. That is a different study.
