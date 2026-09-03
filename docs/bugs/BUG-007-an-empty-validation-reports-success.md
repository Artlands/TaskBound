# BUG-007 — a validation that examined nothing reported success

**Severity:** medium (a release gate passes green while checking nothing)
**Component:** `taskbound/validate.py` (`Report.print`, `validate_all`)
**Found:** 2026-09-03, running the newly-added `taskbound` console script from a directory that was not the repository root
**Status:** FIXED 2026-09-03 — see *What was implemented* below

## Summary

`runner validate` defaults to `--hosts hosts` and `--injections injections`,
both relative paths. `validate_all` globbed them, found nothing, iterated zero
hosts, validated zero injections, and handed a `Report` with `checks == 0` to
`Report.print`, which decides its verdict on `self.errors` alone:

```python
status = "OK" if not self.errors else "FAILED"
```

Zero errors, so `OK`, so exit 0. The output was:

```
OK: 0 checks, 0 errors, 0 warnings
```

That is the same success line a full run prints, with a count nobody reads,
and the exit code a gate acts on.

## Repro

```sh
cd /tmp && python -m taskbound.runner validate ; echo "exit=$?"
# before: OK: 0 checks, 0 errors, 0 warnings   exit=0
# after:  FAILED: no hosts directory at 'hosts'  ...  exit=1
```

## Blast radius

None realised — every validation run to date was from the repository root
against the real tree. It became reachable, rather than merely possible, with
two changes made for the first public release: a `taskbound` console script,
which makes running from an arbitrary working directory the normal thing to do,
and a CI job that treats `validate`'s exit code as a gate. A checkout step that
lands the repository somewhere other than the job's working directory would
then have produced a green gate over an empty check set.

## What was implemented

Two guards, at different altitudes, because they fail for different reasons.

`Report.print` refuses to print `OK` when `checks == 0`, and says why. Every
real path through the validator makes at least one check, so zero is never a
clean tree — it is a tree that was not read. This covers `calibrate`, which
shares the `Report`, as well as `validate`.

`validate_all` names what was missing before it reads anything: whether each
directory exists, and whether the hosts directory yielded a host at all. A
wrong `--hosts` now fails as a wrong `--hosts`, rather than as a generic empty
result, and the messages tell the reader to check the paths and their working
directory.

Regression tests are in `tests/test_validator.py`: the bare `Report` guard, the
outside-a-checkout case driven through `monkeypatch.chdir`, the
directories-exist-but-are-empty case, and one asserting the shipped tree still
passes so the guard cannot fire on real material.

## Why the suite did not catch it

Every validator test passed an explicit absolute path built from
`os.path.dirname(__file__)`, so no test ever ran the validator against a
directory that did not exist — the defaults were never exercised. The suite
tested that invalid material fails; it did not test that *absent* material
fails, which is a different claim and the one a gate depends on.

This is the bounded-quantity shape again, one level up: `checks` is a count with
a meaningful floor, and the code that turned it into a verdict did not carry the
fact that zero is not a valid pass. See BUG-003 and BUG-004 for the same shape
on reported rates.
