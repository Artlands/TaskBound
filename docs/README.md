# Which document do you want?

These are design and protocol documents. **If you want to *run* the benchmark, you do not need any of them** — the [main README](../README.md) covers installation, running, and reading results on its own.

Come here when you want to know *why* the benchmark is built the way it is, or when you are about to do something that needs a protocol: review injection texts, run a pilot, or check a statistical claim.

## Start from what you are trying to do

| I want to… | Read |
|---|---|
| Run the benchmark and read the output | [main README](../README.md) — not these |
| Understand the design in an evening | [`plan_summary.md`](plan_summary.md) |
| Check how a specific number is defined or justified | [`development_plan.md`](development_plan.md), by section |
| Run a full release sweep, in order, with the gates | [`execution_plan.md`](execution_plan.md) |
| Run the pilot stages that come before a sweep | [`pilot_protocol.md`](pilot_protocol.md) |
| Review injection texts as a practitioner | [`realism_rubric.md`](realism_rubric.md) |
| Write or accept a new injection text | [`paraphrase_protocol.md`](paraphrase_protocol.md) |
| See what has gone wrong before, and how it was fixed | [`bugs/`](bugs/) |

## What each one is

**[`plan_summary.md`](plan_summary.md)** — the design in a few pages, with diagrams. The best entry point if you are evaluating whether this benchmark measures what you care about. It points into the full plan by section number rather than repeating it.

**[`development_plan.md`](development_plan.md)** — the full specification, and the longest document here (~2,400 lines). You are not meant to read it front to back. It is organised by numbered section, and **the code cites it directly**: when you see `plan §9.1` in a docstring, an error message, or a report field, that is a pointer into this file. Use it as a reference, not a narrative.

> Because the code cites these section numbers in ~45 places, the numbering is effectively an API. Sections get added, not renumbered.

**[`execution_plan.md`](execution_plan.md)** — the phase-by-phase protocol for actually producing a release: what has to pass before runs start, what order things happen in, and what each phase is allowed to conclude. Read this before committing budget to a sweep.

**[`pilot_protocol.md`](pilot_protocol.md)** — the two pilot stages that precede a full sweep, and their stopping rules.

**[`realism_rubric.md`](realism_rubric.md)** — the instrument two HPC practitioners use to judge whether an injection text could plausibly appear in a real workspace. If you have been asked to review texts, this is your worksheet.

**[`paraphrase_protocol.md`](paraphrase_protocol.md)** — how injection texts are authored, paraphrased, and accepted into a release.

**[`bugs/`](bugs/)** — write-ups of defects found in the harness, each with a runnable reproduction and what was done about it. Useful if you hit something strange, and as a record of what the test suite did *not* catch.

## A note on the frozen documents

`pilot_protocol.md`, `paraphrase_protocol.md` and `realism_rubric.md` were **frozen before the data they govern existed**. That is deliberate and it is the point: stopping rules chosen after seeing the numbers are not stopping rules, and a realism rubric written after the texts is not a review instrument.

So those three read a little stiffly, and they are not edited for style. Each carries a short orientation note at the top, marked as such, which is not part of the protocol.
