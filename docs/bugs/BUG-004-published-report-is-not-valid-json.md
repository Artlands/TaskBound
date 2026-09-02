# BUG-004 — the published report is not valid JSON

**Severity:** high (the artifact meant for other people is unreadable by their tools)
**Component:** `taskbound/aggregate.py:wilson`, report write path
**Found:** 2026-09-02, checking the release report with a strict parser
**Status:** FIXED 2026-09-02 — see *What was implemented* below

## Summary

`wilson` returned `nan` for a cell with no observations:

```python
if not total:
    return (float("nan"), float("nan"))
```

`json.dump` writes that as the bare token `NaN`. **RFC 8259 has no such
literal.** Python's own reader accepts it as an extension, so the defect is
invisible from Python and fatal everywhere else — R's `jsonlite`, Go's
`encoding/json`, Rust's `serde_json` and browser `JSON.parse` all reject the
file outright.

`reports/local-deepseek-v4-flash.json` — the file the write-up is built from and
the one a reader would be handed — contained **18 bare `NaN` tokens**, from empty
cells in the stratified norms table and the per-cell grid (E3A1, E3A2, E3A4 are
unpopulated because E3 exposure is only 22%).

## Why it survived review

Every check in the repository is written in Python, and Python round-trips the
file without complaint:

```sh
.venv/bin/python -c "import json; json.load(open('reports/local-deepseek-v4-flash.json')); print('fine')"
# fine
```

The shipped R script reads the exported **CSV**, not the JSON, so the one
non-Python consumer in the repo did not touch the broken path either.

## Reproduction

```sh
.venv/bin/python -c "
import json
json.load(open('reports/local-deepseek-v4-flash.json'),
          parse_constant=lambda c: (_ for _ in ()).throw(ValueError('strict JSON has no '+c)))"
```

Observed: `ValueError: strict JSON has no NaN`. Equivalently, in R:
`jsonlite::fromJSON("reports/local-deepseek-v4-flash.json")` errors.

## What was implemented

**1. Fixed at the source.** `wilson` returns `(None, None)` for an empty cell.
This is not merely a different spelling — `rate` already returns `"rate": None`
for the same condition, so the pair now agrees with the number beside it instead
of mixing two representations of "no value" in one dict.

**2. A backstop at the write path.** `aggregate.json_safe` walks the report and
replaces any non-finite float with `None`, returning the paths it changed, which
the caller appends to `notes` — silently nulling would hide a genuine numerical
failure as effectively as it hides an empty cell. The file is then written with
`json.dump(..., allow_nan=False)`, which is a standing assertion that what gets
published is JSON a strict parser accepts: `json_safe` has already removed
everything it would trip on, so it only fires if some future path invents a
non-finite value `json_safe` cannot reach.

`None` rather than a sentinel number, because every non-finite value that can
reach the writer means "this quantity is not defined on this data" — an interval
over an empty cell, a ratio over a variance pinned at its boundary — and `None`
is how the rest of the report already spells that.

Verified by re-aggregating: 0 `NaN` tokens, and the strict-parser check above
passes. `json_safe` found nothing left to sanitize, which confirms the source fix
is doing the work rather than the backstop covering for it.

**Regression tests** in `tests/test_analysis.py`:
`test_an_empty_cell_has_no_wilson_interval_rather_than_a_nan`,
`test_the_report_writer_refuses_to_emit_a_non_finite_value`.

## The defect was already committed

`reports/local-deepseek-v4-flash.json` was untracked, but
**`reports/t1_6h.json` is tracked**, and it carried two of the same tokens — a
`wilson` pair on an empty cell in `factor_effects.norms_stratified`, sitting
directly beside the `"rate": null` that shows what the convention was meant to
be. So an unparseable artifact was already in the repository's history.

Its source results no longer exist (`results/` is gitignored and only the
DeepSeek directory survives), so it cannot be regenerated. It was instead
round-tripped through `json_safe` and the fixed writer, which reproduces exactly
what the current code emits for an empty cell. Verified byte-for-byte:

```sh
diff <(sed 's/NaN/null/g' t1_6h.json.bak) reports/t1_6h.json   # identical
```

## Note

`glmm.interval([])` still returns `(nan, nan)`. It is not reachable from the
report — every caller guards its population first — and the write-path backstop
would catch it if a future one did not.
