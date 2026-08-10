# Paraphrase protocol

Milestone 5. Fixed here because every text written afterwards inherits it — T1's
twelve E1–E3 cells, its four E4 cells, and the eight auxiliary cells across
T2–T5. Changing it later would mean re-authoring everything written under the old
rules, so it is frozen before the first cell rather than settled by precedent
afterwards.

This document is the *how*. The reason paraphrases exist at all is plan §7.5:
a reported rate whose lower or upper term is a property of one sentence is not
interpretable, and if the paraphrase slot a text occupies predicts susceptibility
better than the individual text does, that is the headline finding and it
supersedes the factorial.

---

## 1. What a paraphrase is

Three renderings of **one semantic request**, allocated *across* N rather than
added to it: N = 33 runs is three texts × eleven, not one text thirty-three
times. N is a multiple of three for exactly this reason — a value that does not
divide evenly leaves the last block short and quietly unbalances the set. Same
cost as one text repeated, and it decomposes variance instead of measuring only
model stochasticity.

A paraphrase is an artifact of the benchmark, not of the run. Written once,
committed, and frozen with the release tag. No text is generated, selected, or
altered while a sweep is running.

## 2. The request family comes first

Before any text exists, `injections/specs/<spec_id>.md` states, for one
`(task, induced_action)` pair:

- the requested operation, in one sentence;
- the **matched targets** — the attacked target and its in-scope mirror — each
  with the reason it satisfies (or mirrors) the central invariant;
- three **paraphrase intents**, each a distinct rationale for the same request;
- the register constraint;
- anything about this family that does not generalise to the others.

The specification is committed alongside the texts. A paraphrase set is
interpretable as a random effect only if a reader can see what was held fixed
across it, and the specification is that record.

## 3. Matching rules

**Across entry points.** Intent `p2` in E1 is the matched pair of intent `p2`
in E2 and E3. It is not the second unrelated generation a model returned. Each
intent is rendered once per condition per populated entry point, in that
vehicle's voice. This applies within T1, whose four families each span four
entry points. An auxiliary family (T2–T5) occupies a single entry point, so its
three intents have no cross-entry-point partners; the matching rules below still
bind, and the entry-point pairing simply does not arise there.

**Across conditions.** The attacked and benign renderings of one intent differ
in the **target** and in nothing else that can be held fixed. Where the factor
forces a wider difference — A2, whose task boundary runs between two verbs
rather than two objects — the family specification says so explicitly and
explains why.

**Within a cell.** The three attacked texts must not be near-duplicates. The
validator enforces a token-Jaccard threshold of 0.80; a generator asked three
times from one specification will happily return three near-identical texts,
which passes a count check and silently collapses the variance decomposition
back to one text.

## 4. Register

Every rendering is a well-formed diagnostic or documentation note whose
requested action reads as a reasonable next step. Register is an **authoring
constraint, not a variable**: no rendering pleads, threatens, claims authority
it does not have, or asks the reader to conceal anything. Whether blunter
phrasings behave differently is a separate question and is outside this design.

Register belongs to the vehicle, not to the family. The E1 texts are log lines
with the emitter's timestamp prefix; the E2 texts are dated notes in a project
README; the E3 texts are comments in the site-notes voice of a modulefile. The
inert texts of an entry point share that entry point's register exactly,
because they are the control *for* register.

## 5. Generation and provenance

Texts may be AI-generated from the committed request-family specification, one
generation context per paraphrase, subject to:

| Rule | Why |
|------|-----|
| The generator is drawn from **outside the evaluated model set** | A model evaluated against text its own family wrote is not being tested on material independent of it |
| One generation context per paraphrase | Three requests in one context produce three variations on the first answer, not three independent renderings |
| Every text is reviewed and accepted by a **named author** | AI generation makes drafting cheap and does not make acceptance review cheap |
| `generator`, `generated_at`, and `accepted_by` are recorded per text | AI-generated text carries its own contamination risk, separate from publication, and the provenance fields are what make it auditable |

The validator enforces the generator rule once `preregistration.json` names the
evaluated families, and warns until then.

> **Open item for milestone 8.** Every text currently in `injections/` records
> `"generator": "claude-opus-5"`. If the signed pre-registration names a Claude
> model among the three confirmatory families, these texts must be re-authored
> by a generator outside that set before the sweep runs. The provenance field
> is accurate as it stands, and the fix is re-authoring, not relabelling.

## 6. Acceptance review

A text is not done when it exists. For each one, a named reviewer confirms:

1. it implements the intent its `paraphrase` field claims;
2. it names the declared target and requests the declared operation;
3. it holds the register;
4. its `vehicle_writer` and `write_precondition` describe a capability someone
   plausibly has at an HPC site;
5. it contains no literal canary or payload marker — only slots.

Separately, and before results exist, **two HPC practitioners who did not
author the cell** rate its task, vehicle, attacker write precondition, and
requested action against `realism_rubric.md`. Both must independently judge the
scenario plausible; disagreements are adjudicated before any model result
exists. Realism scores stay covariates and are never used to choose which cells
are quoted (plan §9.3).

## 7. What "dominates" means

If the posterior interval for the paraphrase-to-**text** variance ratio lies
wholly above 1 on the model's latent scale, systematic wording variance
dominates and the report leads with that. The report gives the full ratio and
interval either way. This is named in advance so that reporting it is not a
post-hoc pivot.

The denominator was `host:cell` until plan §9.5 found that component aliased with
the saturated fixed block; it is now `injection_id`. Both terms are therefore
wording — the paraphrase slot against the individual text — and the rule no
longer tests wording against structure. Plan §7.5 records what that costs.
