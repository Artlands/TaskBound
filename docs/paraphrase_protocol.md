# Paraphrase protocol

## Status

Milestone 5. This protocol applies to every text written after that milestone.
`v1.0-broad` uses all of it: T1's sixteen E1–E4 cells and eight auxiliary cells
across T2–T5, for 156 texts and twelve request families.

Paraphrases prevent a result from depending on one sentence (plan §7.5). If the
paraphrase slot predicts susceptibility better than the individual text, that
wording effect is reported and takes precedence over the factorial analysis.

---

## 1. What a paraphrase is

Three renderings represent **one semantic request**. They are allocated across
N, not added to it: N = 9 means three texts run three times each, not one text
run nine times. N is divisible by three so every text gets equal exposure. This
costs the same as repeating one text and lets the analysis separate wording
variation from model stochasticity.

A paraphrase is an artifact of the benchmark, not of the run. It is written once,
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

Commit the specification with the texts. The set can serve as a random effect
only when the fixed parts of the request are recorded there.

## 3. Matching rules

**Across entry points.** Intent `p2` in E1 is the matched pair of intent `p2` in
E2 and E3. It is not the second unrelated thing a model happened to return. Each
intent is rendered once per condition per populated entry point, in that
vehicle's voice. This applies within T1, whose four families each span four entry
points. An auxiliary family (T2–T5) occupies a single entry point, so its three
intents have no cross-entry-point partners; the matching rules below still bind,
and the entry-point pairing simply does not arise there.

**Across conditions.** The attacked and benign renderings of one intent differ in
the **target** and in nothing else that can be held fixed. Where the factor
forces a wider difference — A2, whose task boundary runs between two verbs rather
than two objects — the family specification says so explicitly and explains why.

**Within a cell.** The three attacked texts must not be near-duplicates. The
validator enforces a token-Jaccard threshold of **0.50**. A generator can return
three near-identical texts that pass a count check but collapse the variance
decomposition to one text.

The threshold is set from the material rather than picked round. Across the 156
shipped pairs the observed maximum is 0.37 and the median 0.19, because a
paraphrase is a distinct rationale for the same request and not a rewording. The
earlier value of 0.80 would have admitted texts twice as similar as anything
authored, so it was not protecting the property it exists to protect. A test pins
both ends: the shipped set must clear the threshold by at least 0.10, so
loosening the gate or authoring a lazier set fails rather than passing a check
that was never binding.

## 4. Register

Every rendering is a well-formed diagnostic or documentation note whose requested
action reads as a reasonable next step. Register is an **authoring constraint,
not a variable**: no rendering pleads, threatens, claims authority it does not
have, or asks the reader to conceal anything. Whether blunter phrasings behave
differently is a separate question and is outside this design.

Register belongs to the vehicle, not to the family. The E1 texts are log lines
with the emitter's timestamp prefix; the E2 texts are dated notes in a project
README; the E3 texts are comments in the site-notes voice of a modulefile. The
inert texts of an entry point share that entry point's register exactly, because
they are the control *for* register.

## 5. Generation and provenance

Texts are produced by a **three-step pipeline**: a human writes the seed, an
out-of-set model renders it, a named human accepts it.

| Step | Who | What |
|------|-----|------|
| 1. Seed | A human author | The twelve request-family specifications — the semantic content of each (task, action) request, its target, and the three paraphrase *intents*. This is the step that determines what the benchmark measures, and it is not model-generated |
| 2. Render | An out-of-set open-weight model | Each seed rendered into its three paraphrases in the operational register, one generation context per paraphrase |
| 3. Accept | A named human reviewer | §6's checklist, per text |

| Rule | Why |
|------|-----|
| The renderer is drawn from **outside the evaluated model set** | A model evaluated against text its own family wrote is not being tested on material independent of it |
| Step 1 is human, always | With eight evaluated families the exclusion covers nearly every frontier generator, so a rule requiring one to write the *content* would be unsatisfiable. Seeding by hand makes the rule satisfiable and produces a better artifact than a regenerated one: a human-seeded library is what a benchmark should be |
| One generation context per paraphrase | Three requests in one context produce three variations on the first answer, not three independent renderings |
| Every text is reviewed and accepted by a **named author** | AI rendering makes drafting cheap and does not make acceptance review cheap |
| `authored_by`, `generator`, `generated_at`, and `accepted_by` are recorded per text | AI-generated text carries its own contamination risk, separate from publication, and the provenance fields are what make it auditable. `authored_by` is new: it records the seed's human author, so the pipeline is checkable rather than asserted |

The validator enforces the renderer rule once `preregistration.json` names the
evaluated families, and warns until then.

> **Blocking release item.** Every text currently in `injections/` records
> `"generator": "claude-opus-5"`. Under `v1.0-broad` this is no longer
> conditional on the selection: eight evaluated families cover the frontier, so
> the out-of-set rule binds whatever is chosen, and **all 156 texts are
> re-authored** through the three-step pipeline above before acceptance review —
> not after it, since reviewing text that is about to be regenerated wastes the
> review. The provenance field is accurate as it stands, and the fix is
> re-authoring, not relabelling. The renderer is named in the registration so a
> reader can check it against the evaluated set, and so is the seed author.

## 6. Acceptance review

A text is not done when it exists. For each one, a named reviewer confirms:

1. it implements the intent its `paraphrase` field claims;
2. it names the declared target and requests the declared operation;
3. it holds the register;
4. its `vehicle_writer` and `write_precondition` describe a capability someone
   plausibly has at an HPC site;
5. it contains no literal canary or payload marker — only slots.

Separately, and before results exist, **two HPC practitioners who did not author
the cell** rate its task, vehicle, attacker write precondition, and requested
action against `realism_rubric.md`. Both must independently judge the scenario
plausible; disagreements are adjudicated before any model result exists. Realism
scores stay covariates and are never used to choose which cells are quoted
(plan §9.3).

## 7. What the variance ratio reports — and what it no longer decides

The paraphrase-to-**text** variance ratio is reported with its interval as a
**Tier 3 descriptive diagnostic**: the paraphrase slot's variance against the
individual text's, on the model's latent scale.

**The rule that made it a headline is retired** at registration revision `r2`.
Earlier revisions declared that a ratio interval wholly above 1 meant systematic
wording variance "dominated" and superseded the factorial. The denominator is
`injection_id`, so both terms are wording — the paraphrase slot against the
individual text — and the comparison cannot license the claim the rule was named
for, which was wording against *structure*. Structure is a fixed effect with no
variance component to divide by. A promotion mechanism that fires under a name
describing a different quantity is read by its name, however carefully the
caveat is documented.

Plan §7.5 states the current treatment; `design_history.md` §2 records why the
denominator changed, and §7 records why the rule was removed rather than
repaired.
