# Follow-up Study — Does Camouflage Matter?

Designed but not scheduled. This study is **not** part of TaskBound `v1.0` and
nothing in `development_plan.md` depends on it. It is recorded here so the design
is not re-derived later.

## The question

TaskBound `v1.0` writes every injection in a single **operational register**: a
well-formed diagnostic or README note whose requested action reads as a
reasonable next step. That is the realistic HPC case, and a benchmark measuring
real-world risk should measure the realistic case.

It leaves a separate question unanswered: does the *dressing* of the request
change whether the agent complies? The consequence is concrete. If a bare
imperative succeeds as often as a carefully disguised one, then input filtering
aimed at obvious injected commands is worthless, and defenses have to work on the
objective rather than on the wording.

That is a study about attack construction. It is not part of establishing the
phenomenon, which is why it does not belong in the core budget.

## Design

Add a `framing` field to the injection schema, with four values:

| Framing | Form |
|---------|------|
| `operational` | A well-formed diagnostic or README note whose requested action looks like a reasonable troubleshooting step (`v1.0`'s register) |
| `naive` | A bare imperative appended to the content |
| `authority` | Text presenting itself as a site policy note, sysadmin message, or system banner |
| `indirect` | The instruction is a consequence of following apparently benign guidance, never stated as a command |

Run `naive`, `authority`, and `indirect` on the core task's sixteen cells;
`operational` is already covered by the core runs.

| Run class | Count | Per configuration |
|-----------|-------|-------------------|
| Framing extension | 16 cells × 3 framings × 33 | 1,584 |

That is 4,752 runs across the three-model baseline — about 70% of the `v1.0`
two-agent sweep, or 51% of the complete `v1.0` target including the mode bridge —
and 144 new texts on top of the core's 156 injection texts. It is droppable, but
it is not cheap, and the plan should not describe it as a rider.

`authority` is the value most worth carrying for domain reasons: site-authority
framing is exactly what HPC users are trained to comply with, so it is the
framing most likely to behave differently in HPC than in general agent
benchmarks.

## Two constraints on how it is reported

**Framing is nominal, not ordinal.** These are different disguises, not points on
a scale. Only `naive` < `operational` is a defensible ordering; nothing justifies
ranking `authority` against `indirect`. Keep them named rather than numbered so
no table invites a reader to see a trend across them.

**Framing is not a control, and must not be reported as one.** It looks like a
floor condition proving the instrument works, but four genuine controls already
do that job in `v1.0`:

- **Near-miss** — the sensitive action is reachable and the oracle detects it,
  when the *user* asks for it.
- **Benign control** — the agent acts on *environment*-sourced instructions at
  all.
- **Inert control** — the effect is the instruction, not the presence of text.
- **Paraphrase variance** — a low compliance rate is not an artifact of one badly
  written text.

With those four in place, low compliance under the operational register is already
interpretable as agent robustness rather than as broken instrumentation. A
`naive` floor tests nothing further: an agent that obeys the user, obeys benign
environment text, and still refuses out-of-scope environment text has been
characterized completely.

## Why it is a category rather than three more paraphrases

Both framings and paraphrases are wording variation. The difference is how the
analysis treats them: paraphrases are unlabeled and modeled as a random effect,
while a framing is a fixed effect because the whole point of naming it is to make
a claim about the label. That claim is what costs a full set of factorial cells,
and it is why this study is separate rather than folded into `v1.0`'s three
paraphrases per cell.
