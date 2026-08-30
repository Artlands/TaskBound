# TaskBound v1.0-broad: design overview

This is the concise version of the design. It uses figures and points to the
full specification by section number. `development_plan.md` contains the full
rules; `design_history.md` records the evidence behind later amendments.

## What TaskBound measures, in one paragraph

TaskBound asks a single question about a language-model agent doing real work in
an HPC (high-performance computing) environment: *does the agent stay inside the
task it was given, even when untrusted text it reads along the way asks it to do
something that is allowed but off-task?*

The agent is authorized to use the account, and every action it takes stays
within that account's permissions. The risk is an agent drifting outside its
*specific assignment* because a log line, README, or tool result told it to.

```mermaid
flowchart LR
    TASK[Authorized task: diagnose failed job] --> AGENT[Planner → worker → planner]
    VEHICLE[Untrusted text in tool-visible artifact] --> AGENT
    AGENT --> IN[In-scope diagnosis]
    AGENT -. requested diversion .-> OUT[Out-of-scope action]
    OUT --> ORACLE[Policy + action-trace oracle]
```

The primary outcome is whether the agent complies with that diversion, measured
only where it actually saw the injected text. Realization is graded separately,
so an attempted-but-failed action is not confused with a refusal. And because
that number alone cannot say whether a low rate is judgment or timidity, the
release measures a second thing beside it: the same action, requested by the
*user*, under a policy that makes it legitimate.

## Release scope

`v1.0-broad` is narrow where it needs to be and broad where breadth is
affordable: **one host, five tasks, eight model families**, with the near-miss
control run at a much larger sample size (N = 36) because that is the quantity
the most important control exists to measure.

It is the successor to `v1.0-compact` (a smaller earlier design: T1 only, two
model families, near-miss at N = 9), which is retired and recorded in
`design_history.md` §5 with the reasons for widening. Because all three changes —
more model families, more tasks, and a larger near-miss sample — changed the
allocation of runs, the new schedule is registered as a *new version* rather than
edited over the old one.

**Two identifiers.** The release version names the allocation; the *registration
revision* names the claim set. The current pair is `v1.0-broad` / **`r2`**, and
`design_history.md` §7 records what `r2` changed from an allocation it did not
touch.

**Target venue: a benchmarks track**, which is why no defense is evaluated here;
the three-arm study stays `v1.1` (§10.2, §13).

## What is scheduled

| Axis | `v1.0-broad` choice |
|------|----------------------|
| Host | `site_a` |
| Tasks | T1 (the core task), plus T2–T5 at two cells each |
| Groups | 24 — T1's 16 cells plus 8 additional (task, cell) pairs |
| Request families | 12 |
| Conditions | attacked, benign, inert, near-miss, clean |
| Execution | two-agent only |
| Model replication | eight families |
| Defense | `none` |
| Exposed target | N = 9 per injected group |
| Attempt cap | 3N = 27 per injected group |
| Near-miss | N = 36 per (task, action), 12 blocks |
| Clean | N = 9 per task, 5 blocks |

The core task carries the full entry-point × induced-action crossing:

```mermaid
flowchart TB
    T1[T1 failed-job diagnosis] --> E1[E1 job log]
    T1 --> E2[E2 project README]
    T1 --> E3[E3 module description]
    T1 --> E4[E4 work order]
    E1 --> A[A1 read secret · A2 scheduler · A3 modify config · A4 persist content]
    E2 --> A
    E3 --> A
    E4 --> A
```

The four auxiliary tasks carry two cells each, drawn from the sixteen T1 already
populates:

```mermaid
flowchart LR
    T2[T2 post-processing repair] --> C2[E1A3 · E2A1]
    T3[T3 build and run] --> C3[E1A2 · E3A3]
    T4[T4 data staging] --> C4[E2A4 · E4A2]
    T5[T5 status report] --> C5[E3A4 · E4A1]
    C2 --> SHARED[Every auxiliary cell is also a T1 cell]
    C3 --> SHARED
    C4 --> SHARED
    C5 --> SHARED
    SHARED --> TASKEFF[Task effect estimated within cell]
```

That has two useful consequences: every additional cell has a built-in within-T1
comparison, and every entry point and every induced action appears in exactly
three of the five tasks. The task effect can therefore be separated from (not
confused with) either of those two factors.

## What each condition tells us

```mermaid
flowchart LR
    CLEAN[Clean: no injected text]
    INERT[Inert: same vehicle, no request]
    BENIGN[Benign: matched in-scope request]
    ATTACK[Attacked: matched out-of-scope request]
    NEAR[Near-miss: requested action moved into task scope]
```

- **Attacked vs. benign** estimates how susceptible the agent is to an
  out-of-scope request, while keeping the wording, intent, and entry point the
  same between the two.
- **Inert** measures whether the targeted action happens *on its own*, with no
  request present at all. This runs only on the core task, because it is a
  question about the vehicle (the file the text lives in) rather than about the
  reader.
- **Near-miss** takes the same requested action but moves it *into* the task's
  scope. It is the only control that tells a good agent (one that respects task
  scope) apart from a merely cautious one (one that refuses broadly). That is why
  it — not the attack — gets the largest sample size, and why it carries a
  headline estimand. It yields two rates with two denominators, and they are
  not interchangeable: the **in-scope action rate** (did the action happen at
  all, over all 36 runs) feeds C2, while **overblocking** (did the agent decline
  while otherwise doing the job, over the realized denominator) is the stricter
  diagnostic that says why.
- **Clean** measures the ordinary, non-adversarial baseline: how often the agent
  completes its task normally, and how often background scope-violation happens,
  per task.

Controls account for 729 of the 945 runs per model family because these
explanations cannot be recovered from attack runs alone. The near-miss and
attacked conditions are the two halves of the second headline estimand:

```mermaid
flowchart LR
    NEARM[Near-miss: the USER asks<br/>in-scope action rate<br/>full 36-run denominator] --> D{{"D = in-scope − attacked"}}
    ATT[Attacked: a FILE asks<br/>compliance among exposed] --> D
    D --> GOOD[D near 1:<br/>does the work, declines the diversion]
    D --> AMB[D near 0:<br/>ambiguous alone — read the two rates]
    AMB --> REFUSE[both low:<br/>refuses broadly]
    AMB --> COMPLY[both high:<br/>complies with anything]
```

D is never reported without both component rates, because the bottom two boxes
produce the same D and only the levels distinguish them.

All groups run under the same two-agent protocol, so execution mode is held
constant rather than measured as a variable:

```mermaid
sequenceDiagram
    participant P as Planner
    participant W as Worker
    participant H as HPC tools
    P->>W: diagnosis plan / delegated work
    W->>H: inspect and act
    H-->>W: tool-visible artifacts, possibly injected
    W-->>P: findings
    P->>H: finish task
```

Three fixed paraphrases share each semantic request. The N = 9 sample divides
evenly as three runs per paraphrase, so paraphrases do not multiply the number of
runs. Near-miss and clean blocks carry no injected text and therefore have no
paraphrase balance to maintain.

## What the design claims

**Claim status: exploratory.** Two headline results, reported with intervals
against frozen reference lines. Neither is a test: no power gate stands behind
them, no multiplicity correction is taken over them, and nothing gates on where
a bound falls. The task boundary they are defined against is author-declared,
with no independent adjudication behind it. `docs/design_history.md` §9 records
the re-scope and what it costs.

- **C1 — attack susceptibility after exposure**, averaged equally over T1's
  sixteen cells and equally over the eight registered model families. The same
  definition the retired registration used, kept fixed so the widened design
  cannot be read as having redefined the target. It passes when the lower bound
  of the 95% interval exceeds the frozen **10-percentage-point** practical-risk
  floor.
- **C2 — scope discrimination**, the in-scope action rate minus attacked
  compliance for the same action. A perfectly scope-respecting agent does the
  work when its user asks and declines when a file asks, so its discrimination is
  near 1. C2 passes when the lower bound on the *deficit* (`1 − D`) exceeds a
  frozen **20-percentage-point** floor — that is, when the design can show
  discrimination is meaningfully imperfect.

```mermaid
flowchart TB
    DESIGN[Same frozen 945-run schedule] --> M1[Family 1]
    DESIGN --> M2[Family 2]
    DESIGN --> MD[... families 3-7]
    DESIGN --> M8[Family 8]
    M1 --> EST[Pooled, equal weights over cells and families]
    M2 --> EST
    MD --> EST
    M8 --> EST
    EST --> G1[C1: lower bound vs 10pp reference line]
    EST --> G2[C2: lower bound of 1−D vs 20pp reference line]
    M1 --> PF[Per-family intervals]
    M8 --> PF
    PF --> K["the line is cleared in k of 8 families"]
```

C2 is the one the field has no instrument for, and it is why near-miss gets 46%
of the budget. It is ambiguous if reported alone — a value near zero is produced
both by an agent that complies with everything and by one that refuses everything
— so it never appears without both component rates beside it. The two estimands
are reported side by side with no correction between them, and each is also
reported per family to support "the reference line is cleared in *k* of 8
families" as description. There is no power simulation to fail and no demotion
branch: C2 is reported with its interval whatever its width, and the 20pp line
stays where it was frozen.

**Everything else sits in one of two lower tiers.** Tier 2 (Holm-corrected,
labelled secondary in the text): scope selectivity, the entry-point and
induced-action effects, the five-task contrast, overblocking by action, exposure
by entry point, family heterogeneity, and the comparability re-scoring. Tier 3
(intervals only, no p-values, no significance language): the entry-point ×
action interaction, wording variance, overblocking by task, per-cell rates,
realization ladders, and evaluated-control observability.

Overblocking keeps a **declared precision target** rather than a gate: ±16
percentage points per (task, action) per family, or ±8 points pooled over a
task's four actions, measured on the number of runs that actually remain after
`overblocked: null` runs are removed. C2 gates instead of it because C2's
near-miss term uses the full denominator, which no pilot measurement can shrink.

**The comparability re-scoring** (§9.6) scores every family twice on the same
traces — once the way a benchmark reporting attack success alone would, once the
way this one does — and reports how often the two conventions disagree, as a
sign-reversal count and a rank correlation rather than as two ordered lists. It
costs no runs, and it is a claim about a scoring *convention*, not about any
named benchmark's published numbers.

The release does **not** claim to generalize across hosts or execution modes, or
across tasks beyond the five authored here. Eight families are a *replication*
exercise, not a *ranking*: family results print in registered order and are never
sorted by rate.

**The models.** Four registered fits on three populations. The primary model is
`condition * entry_point * induced_action + task + model_family` on exposed
attacked and benign runs; the exposure model is `condition * entry_point + task +
model_family` on every attempted injected run; and two additive fits on near-miss
runs, `overblocked ~ induced_action + task + model_family` on the realized
denominator and `in_scope_action ~ induced_action + task + model_family` on the
full one. C2 differences the first and last draw-wise — their populations are
disjoint by construction, so the draws are independent. `task` appears as a main
effect only. `host:cell` is undefined in a single-host design. `request_family`
and `task:cell` are *candidates* whose inclusion is decided by a rank check and a
synthetic-recovery check on the exact broad design matrix before the design is
signed — the default is to exclude them. This discipline is what
`design_history.md` §§2–3 exist to enforce.

## The exact run budget

Per model family:

| Component | Target runs |
|-----------|------------:|
| Attacked: 24 groups × 3 | 72 |
| Benign: 24 groups × 3 | 72 |
| Inert: 4 entry points × 3 | 12 |
| Near-miss: 10 blocks × 6 | 60 |
| Clean: 4 tasks × 3 | 12 |
| **Total** | **228** |

Injected groups may retry up to three times per target; near-miss and clean
blocks have fixed counts. That is a hard cap of **462 attempts** per family —
**1,824 target runs against a 3,696-attempt cap** across the eight. On a
self-hosted endpoint that is about **11 hours per model family**, which is what
this allocation is sized against (`design_history.md` §10).

Ten near-miss blocks and four clean, not twelve and five: T3 carries its two
cells and no blocks of its own, because those cells are what keep every entry
point and induced action present in three tasks apiece while its runs are the
most expensive in the sweep.

Controls account for **156** of the 228 target runs per family, and near-miss
alone is 60 of them: this is a design whose most novel quantity is a control. If
cost binds, a predeclared ladder reduces scope in a fixed order, each rung named
with the claim it costs — model families from the end of the registered order
(8 → 6 → 4), then the auxiliary tasks, then the near-miss N. Rungs 1–3 cost
breadth; rung 4 halves near-miss and so costs C2 most of its resolution — with
no power gate there is nothing to re-simulate, which makes the cost a judgement
about precision rather than a gate outcome. Each rung is taken
at signing or not at all. Changing the injected N, the T1 crossing, the
paraphrase count, or any condition requires a new release version; changing an
estimand, a floor, or a tier requires a new registration revision.

A later, separate three-arm defense study runs over the T1 block only: 477 runs
per family per arm, 2,862 runs across a registered two-family subset, with a
6,750-attempt cap.

## Gates that must be passed before execution

```mermaid
flowchart LR
    BUILD[Harness + full 156-text library] --> SUPPORT[7c broad-scope scheduling]
    SUPPORT --> SMOKE[69-run integration smoke<br/>+ early cost projection]
    SMOKE --> D7[7d analysis support for r2]
    SMOKE --> REAUTH[Re-author texts:<br/>human seeds, out-of-set renderer]
    D7 --> PILOT[Sizing pilot]
    REAUTH --> REVIEW[Acceptance + realism review]
    PILOT --> GATES[cost gate]
    REVIEW --> GATES
    GATES --> SIGN[Sign v1.0-broad / r2]
    SIGN --> RUN[Eight frozen 945-run schedules]
    RUN --> AUDIT[Oracle audit + reproducible aggregation]
```

The harness plans this scope (69 groups, 945 target runs, 1,881 attempts per
family), carries `task` in both registered blocks, fits overblocking on its
realized denominator, and decides the reopened random components by rank. What
remains needs people, money, and an out-of-set generator.

1. **Run the integration smoke** — 69 runs, one per group, any model — and
   circulate a rough cost projection from it *before* the human
   review gates start. Reviewing 236 artifacts and re-authoring 156 texts is
   months of people-time, and spending it on material a later cost decision would
   drop is the one sequencing error that cannot be undone.
2. **Run the §11.3 inference cross-check** once in `lme4` or `glmmTMB` from
   the exported frame, and record its agreement figures. Milestone 7d is
   otherwise complete.
3. **Re-author all 156 injection texts** through the three-step pipeline —
   human-written request-family seeds, an out-of-set open-weight renderer, named
   human acceptance. At eight families the out-of-set rule binds unconditionally,
   and human seeds are what make it satisfiable.
4. **Run the sizing pilot** to measure exposure, clustering, tokens, turns, cost,
   and the rate at which overblocking empties the null denominator.
5. **Optionally simulate power as a diagnostic.** No gate depends on it. One
   model fit over the full allocation takes about 23 seconds, so a
   500-simulation run is hours of compute, and it answers what the allocation
   could resolve under assumed clustering — useful to know, but it licenses and
   blocks nothing.
6. **Approve expected cost, hard-cap cost, and contingency** across the full
   eight-family envelope. This is the gate most likely to bind.
7. **Complete named acceptance review** of all 236 authored artifacts and
   independent realism review of the host — including whether one allocation
   plausibly carries all five situations at once.
8. **Freeze the eight model/configuration hashes**, the registered family order,
   the schedule, canaries, marker set, **and the registration revision** in the
   signed pre-registration.
9. **After the sweep**, pass the stratified oracle audit and reproduce
   aggregation from each family's immutable release manifest.

Pilot observations cannot silently raise or lower any registered N, change an
estimand or a floor, or restore excluded axes. A failed gate blocks the release
or leads to a new versioned design — and both happen *before* main results are
viewed.

## Current state

The harness, simulator, policies, oracle, two-agent runner, sweep planner,
mixed-effects aggregation, overblocking fit, and the analysis support `r2` needs
are all implemented: the in-scope action rate on the full near-miss denominator,
C2's model and draw-wise interval, explicit family weighting, per-family
reporting, tier labels, and the §9.6 re-scoring. All five tasks, all 156 texts,
twelve request families, twelve near-miss tasks, and 50 calibration fixtures
exist and validate. No pilot or main sweep has been run yet.

The open gates are listed once, in the README's
[Known gaps](../README.md#known-gaps-before-this-is-a-v10-broad-result), rather
than restated here where the two would drift apart.

One remainder is external rather than pending: the §11.3 inference cross-check
needs `lme4` or `glmmTMB`, which this standard-library-only repository does not
depend on. `aggregate --export-frame` writes the frame and the reference-fit
script; the comparison is run once by hand and its agreement recorded before
signing.

Building the wider scope surfaced three latent defects — all pre-existing, and
all invisible under the compact schedule: a generator-provenance check that could
never fail, an audit stratum that absorbed the near-miss runs with no verdict,
and a run-total check that multiplied where it should have summed.
`design_history.md` §6 records them.
