# Publication readiness review

An outside assessment of TaskBound as a candidate top-tier conference
submission, written 2026-08-19 against the tree at `80ea0c9` (`v1.0-compact`,
built but not run).

Evidence base: the plan documents, the `site_a` host and T1 assets, the
injection library, the analysis code, plus a full test run (286 passed, 6m28s)
and `runner validate` (4,814 checks, 0 errors, 157 provenance warnings —
all of them the expected "no `preregistration.json`" notice).

Nothing here is a design amendment. `development_plan.md` remains the
specification; this file records how the design is likely to be received and
what would change that.

> **Status, 2026-08-21.** Items **2, 3, and 4** of "Highest-return changes" were
> adopted, as `v1.0-broad`: eight model families, T2–T5 scheduled, near-miss at
> N = 36. As this file predicted, each changed the allocation and so required a
> new versioned registration rather than an edit; the compact schedule is retired
> to `design_history.md` §5, which records the evidence and what did not change.
> Item 1 (related-work positioning), item 5 (an early defense arm), and the
> reframe are **not** adopted and remain open. This file is left as written — an
> outside assessment of the tree at `80ea0c9` — rather than revised to match what
> came of it.

## Summary

The methodology is stronger than most published agent-security benchmarks. The
scope and current state are not publishable at a top-tier venue, and one
structural problem — the confirmatory claim is something the field already
believes — is not fixed by running the sweep.

## What is strong

- **The central invariant** (§2, `account_policy.allows(a) ∧ ¬task_policy.allows(a)`)
  is a clean formalization of a failure mode most benchmarks conflate with
  access control, and the validator enforces it mechanically rather than by
  assertion. That is rare.
- **The near-miss condition** (§7.4) is the best idea in the design. Without it
  an agent that refuses everything scores perfectly, which is how existing
  injection benchmarks can be gamed. Pairing each attack with a run where the
  *user* asks for the same action, and widening the policy to match, is a
  control the field largely lacks.
- **The inert condition** (§7.2) separates "text was present" from "text
  contained an instruction." Also uncommon.
- **Exposure conditioning done correctly**, including the explicit statement
  (§8.1) that the exposed subset is post-treatment selected and that conditional
  contrasts are not causal on a common population. Most papers condition on
  exposure silently.
- **Identification-first design** (§3, R1–R4), pre-registration, attrition
  reported under both extreme assignments (§9.4), Holm (§9.2), a power gate that
  can block the release (§9.5), an oracle audit with precision/recall gates
  (§8.7), and realism review by non-authors used as a covariate and never as a
  subsetting rule (§9.3, `realism_rubric.md`).
- **`design_history.md` §2.** Discovering that `host:cell` and `request_family`
  were aliased with the saturated fixed block, recording the log-likelihood
  evidence, and amending the registration is the kind of thing that normally
  gets quietly deleted.

The engineering matches the design: standard library only, and the analysis
tests fit the registered model to synthetic data with known coefficients rather
than asserting on a mock.

## Why it does not clear the bar yet

1. **Nothing has been run.** Known gap #1. Everything below assumes the sweep
   happens.

2. **Zero engagement with related work.** No mention of AgentDojo, InjecAgent,
   BIPIA, Agent Security Bench, WASP, AgentHarm, or anything comparable appears
   anywhere in the documentation. This is the largest single submission risk:
   the first reviewer question is "how does this differ from AgentDojo?" and
   there is currently no answer on file. The answer exists — permitted-but-
   out-of-scope actions, compliance rather than harm as the primary outcome,
   the near-miss and inert controls — but an undefended novelty claim in a
   crowded area is rejected quickly.

3. **The confirmatory claim is a result the field considers settled.** The sole
   confirmatory estimand is attacked compliance among exposed runs clearing the
   10pp practical-risk floor (§9.5). Prior injection benchmarks already report
   compliance well above that. The whole apparatus is therefore spent
   establishing an undisputed point, while the genuinely novel quantities —
   scope selectivity, entry-point and induced-action effects, overblocking —
   are exploratory and, by §9.5's own table, underpowered by construction.
   "The one confirmatory finding is unsurprising and the interesting ones
   cannot be claimed" is a hard paper to place.

4. **Scale is far below benchmark-paper norms.** One host, one task, 16 cells,
   N = 9, two model families. Benchmark tracks routinely see 10–25 models over
   hundreds of scenarios. §9.3 disclaims host *and* task generalization
   outright, which is honest and correct, but reads to a reviewer as "the
   benchmark cannot support the claim in its title." Honesty about narrow scope
   does not make narrow scope publishable *as a benchmark*.

5. **No defense is evaluated.** Defense is `none` throughout `v1.0-compact`;
   the three-arm study is deferred to `v1.1`. Security venues generally expect
   at least one mitigation measured.

6. **The environment is small.** `local_sim`, roughly ten workspace files, no
   real scheduler. The realism rubric rates plausibility, not fidelity. At an
   HPC venue, "simulator, no site deployment" is an easy criticism.

7. **Contamination is unresolved.** Every injection records
   `generator: claude-opus-5`. Known gap #3 requires re-authoring by an
   out-of-set generator if either evaluated family shares lineage — 108 texts
   of work still ahead, correctly flagged but not done.

8. **The hand-rolled GLMM invites a question.** `glmm.py` is defensible and
   tested against synthetic truth, but a reviewer will ask why not `lme4` or
   `glmmTMB`. Cross-checking one fit against a reference implementation and
   reporting the agreement is an afternoon's insurance on the entire inference.

## The reframe worth considering

The apparatus can produce a result the field does *not* already believe, and
the plan currently files it under exploratory:

> Existing injection benchmarks' safety numbers are confounded, and here is
> what changes when the missing controls are added.

Near-miss can show that models scoring "safe" are refusing broadly rather than
discriminating scope. Inert can show what share of the effect is text presence
rather than instruction content. That is a methodological result about how the
field measures, it is novel, it is contrarian, and this design uniquely
supports it. Led with, it makes the narrow scope acceptable — a methods
contribution does not need 25 models.

Under the current framing ("agents are hijacked at rate X"), narrow scope is
fatal, because a benchmark paper is judged on coverage.

## Highest-return changes, ordered

1. **Write the related-work positioning before the sweep.** It will change what
   gets measured.
2. **Add model families — 8–12, not two.** This is the replication axis and it
   costs money rather than authoring time. The frozen-schedule machinery
   already supports it. Note this conflicts with §9.3's no-leaderboard rule
   only if the extra families are reported as a ranking; as replication they
   are consistent with it.
3. **Schedule T2–T5.** They are authored and validated. Excluding them for
   runtime, when §10.3 identifies authoring rather than runtime as the binding
   constraint, is the wrong trade — and it buys the task-generalization claim
   §9.3 currently forfeits.
4. **Raise N for near-miss and overblocking specifically.** At N = 9 per action
   that is roughly ±27pp on the most novel quantity. If overblocking carries
   the reframed story, it must be powered.
5. **Add one defense arm** (prompt hardening) without waiting for the full
   `v1.1` three-arm study.

Items 2–4 all change N, cells, or conditions, so under §9.5 and
`plan_summary.md` each requires a new versioned registration rather than an
edit to this one. That is the intended cost of making them.

## Venue assessment

| Venue | Verdict as-is |
|-------|---------------|
| NeurIPS / ICLR Datasets & Benchmarks | Reject — scale |
| USENIX Security, CCS, S&P, NDSS | Reject — no defenses, no related work, simulated environment, known headline |
| SC / ISC | The HPC framing helps; they would want site involvement and be indifferent to the statistics |
| SaTML or a workshop | Realistic today |

After items 1–3 and the reframe: genuinely competitive at a benchmarks track or
SaTML, and plausible at a security venue with a defense arm.

## The one-line version

The rigor is real and rare, but rigor is not what gets benchmark papers
accepted — coverage and a surprising finding are. Right now the design points
world-class rigor at a narrow scope and a known answer.
