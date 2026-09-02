# BUG-005 — the two control-table crossing figures are over different populations

**Severity:** medium (an invited ratio between two printed numbers is not a real quantity)
**Component:** `taskbound/aggregate.py:control_table`, `_row`
**Found:** 2026-09-02, extending the §8.6 table with the severity split
**Status:** FIXED 2026-09-02

## Summary

The evaluated-control table reports how many scope crossings the control
profiles would observe. `crossings` counts entries of `control_annotations`,
which `_row` builds from `path_and_verb_violations` only — those are the only
crossings a profile annotates.

The severity split added beside it took its count from a different place:

```python
mutating = sum(row["scope_violations_mutating"] for row in rows)
```

`scope_violations_mutating` is `mutation_count`, which by construction is
`mutating path-and-verb violations + state_constraint_violations`. State
constraints are properties of the end state, not actions; they have no
annotation and are not in `crossings`.

So the report printed `crossings: 4483` and `mutating_crossings: 254` next to
each other over **different populations**, and the ratio a reader would form
from them is not a quantity that exists.

The more useful figure was also missing entirely: the unobserved fraction was
quoted only over *every* crossing, and most crossings are reads and listings —
a diagnosis casting around for context. "98.8% of crossings unobserved" and
"98.8% of writes unobserved" are very different claims, and only the first was
computable.

## What was implemented

`_row` carries `control_annotation_mutating`, a list of booleans parallel to
`control_annotations`, derived from each violation's `kind` via
`oracle.is_mutating` — derived rather than read from the stored `mutating`
field, so a result written before that field existed classifies identically.

It is a **parallel list rather than a key inside the annotation dicts** because
`control_table` reads observation with `any(annotation.values())`; a severity
flag sitting in there would make every mutating crossing count as observed by
construction. `test_a_severity_flag_is_never_mistaken_for_a_profile_observation`
pins that down.

`control_table` then counts both figures over the one denominator, and reports
`mutating_observed_by_any_profile` and `mutating_unobserved_fraction` beside the
headline pair, plus a `denominator` field saying in words what is excluded and
why.

**Regression test:**
`test_the_control_table_counts_both_figures_over_one_denominator` in
`tests/test_controls_and_audit.py`, which uses a row whose
`scope_violations_mutating` is deliberately larger than its mutating crossings,
so the old code cannot pass it.
