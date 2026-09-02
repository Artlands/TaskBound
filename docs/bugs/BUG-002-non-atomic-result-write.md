# BUG-002 — a signal during a result write leaves a truncated file that blocks resume

**Severity:** medium (recoverable by hand, but it breaks the one workflow the
sweep is designed for — stop and resume a multi-hour run)
**Component:** `taskbound/sweep.py:_write`, `taskbound/sweep.py:_resume`,
`taskbound/aggregate.py:load_frame`
**Found:** 2026-09-01, while resuming the `local-deepseek-v4-flash` release sweep
**Status:** FIXED 2026-09-01 — see *What was implemented* below

## Summary

`_write` serialises straight into the final path:

```python
def _write(out_dir: str, attempt_id: str, record: dict[str, Any]) -> None:
    path = os.path.join(out_dir, attempt_id + ".json")
    if os.path.exists(path):
        raise SystemExit(f"refusing to overwrite existing result {path}")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")
```

There is no temp-file-and-rename. A result is ~80 KB of indented JSON, so the
write is several buffer flushes wide. A `SIGTERM` landing inside that window —
which is exactly how this sweep is paused — leaves a syntactically invalid file
in the results directory.

Nothing downstream tolerates it, and the failure is an unhandled traceback
rather than a diagnostic:

* `sweep._resume` does a bare `json.load` per file → `JSONDecodeError`, so the
  sweep **cannot be resumed at all** until the file is found and deleted by hand.
* `aggregate.load_frame` does the same → the whole report fails.

There is a second-order trap. If `_resume` were made to skip the unreadable
file, the attempt id would not land in `done`, the attempt would be re-run, and
`_write` would then hit `refusing to overwrite existing result`. So the
truncated file has to be deleted manually under either behaviour.

## Reproduction

```sh
mkdir -p /tmp/trunc/model_x
src=$(ls results/<model>/*.json | head -1)
head -c 4000 "$src" > /tmp/trunc/model_x/attacked_t1_failed_job_E1A1_00.json

.venv/bin/python -m taskbound.runner aggregate --results /tmp/trunc --out /tmp/r.json
```

Observed:

```
json.decoder.JSONDecodeError: Unterminated string starting at: line 84 column 21 (char 3982)
```

and the same exception from `sweep._resume` on that directory.

## Why it has not bitten yet

`_write` runs on the sweep's main thread, and the write is fast relative to a
~17-minute attempt, so the window is small — under a minute per hour of
wall clock. The 2026-09-01 pause at 220 attempts came through clean: all 220
files parse. That is luck, not a guarantee, and the exposure grows with every
additional stop/resume cycle. At the release scale — 8 model families ×
462 maximum attempts — the odds of never landing in the window are not ones
worth relying on.

## What was implemented

`_write` now serialises to a sibling `.partial`, fsyncs, and `os.replace`s it
into position, so the appearance of the final path is atomic and a reader sees
either no file or a complete one. `_resume` globs `*.json`, so a `.partial` left
behind by a kill is already invisible to it and is always safe to delete — raw
results are append-only, and the attempt simply re-runs.

Both readers now name the offending file instead of raising a bare
`JSONDecodeError`, because the caller has to find and delete it before work can
continue and the traceback did not say which of several hundred results was the
problem:

```
<path> is not valid JSON (<detail>). A result truncated by a signal cannot be
repaired; delete it and the attempt re-runs (raw results are append-only).
```

**Regression tests** (there were none): `test_a_result_is_never_left_half_written`
asserts no `.partial` survives a clean write, and
`test_a_truncated_result_names_itself_rather_than_raising_a_bare_decode_error`
asserts both `sweep._resume` and `aggregate.load_frame` name the file. Both in
`tests/test_sweep.py`.

## Originally suggested fix (superseded)


Write to a sibling temp file and `os.replace`, which is atomic on POSIX within
a filesystem:

```python
def _write(out_dir: str, attempt_id: str, record: dict[str, Any]) -> None:
    path = os.path.join(out_dir, attempt_id + ".json")
    if os.path.exists(path):
        raise SystemExit(f"refusing to overwrite existing result {path}")
    tmp = path + ".partial"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
```

A reader then sees either no file or a complete one. `os.replace` also keeps the
existing overwrite refusal meaningful, since the check is still against `path`.

Worth pairing with: have `_resume` and `load_frame` report the offending path
rather than raising a raw `JSONDecodeError`, and have `_resume` ignore
`*.partial` leftovers. Raw results are append-only, so a `.partial` file is
always safe to discard — the attempt simply re-runs.

## Note

`results/` is gitignored and holds canary values, so a truncated file cannot be
recovered from version control. Deleting it is the correct recovery; the sweep
re-attempts that cell.
