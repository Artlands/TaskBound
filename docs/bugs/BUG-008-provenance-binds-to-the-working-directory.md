# BUG-008 — recorded provenance was whatever repository the user was standing in

**Severity:** high (a result can carry a commit id and source hash from a tree containing none of this code, and still pass the release binding check)
**Component:** `taskbound/runner.py` (`_git_commit`, `_git_source_sha256`, `_git_dirty`); consumed by `taskbound/sweep.py` and `aggregate.validate_release_binding`
**Found:** 2026-09-03, preparing the first public release: a clean checkout unpacked outside a git repository failed a provenance test, and the reason it failed turned out to be the smaller half of the problem
**Status:** FIXED 2026-09-03 — see *What was implemented* below

## Summary

Every result records three provenance fields:

```python
"git_commit":        _git_commit(),
"git_source_sha256": _git_source_sha256(),
"git_dirty":         _git_dirty(),
```

All three shelled out to `git` **with no `-C` and no `cwd`**, so all three
answered about the process's working directory. `git rev-parse` walks *up* from
where it is asked. A TaskBound unpacked anywhere inside another checkout —
vendored into a monorepo, dropped in a scratch directory that happens to be
under version control, extracted beside an unrelated project — therefore
resolved to *that* repository, and:

- `git_commit` became that repository's `HEAD`;
- `git_source_sha256` hashed that repository's tracked files, none of which are
  TaskBound's;
- `git_dirty` described that repository's working tree.

Nothing in the record says which repository it is talking about.

## Repro

```sh
mkdir outer && cd outer && git init -q .
echo hi > unrelated.txt && git add unrelated.txt && git commit -qm init
git clone --depth 1 <taskbound> TaskBound && cd TaskBound
python -m taskbound.runner run --host hosts/site_a --task t1_failed_job \
  --condition clean --agent scripted \
  --script fixtures/scripts/clean_success.json --out res
python -c "import json,glob;r=json.load(open(glob.glob('res/*.json')[0]));print(r['git_commit'])"
cd .. && git rev-parse HEAD     # before the fix: the same 40 hex characters
```

The recorded commit is the *outer* repository's, and the recorded
`git_source_sha256` is a digest over `unrelated.txt`.

## Why this is worse than a wrong string

`aggregate.validate_release_binding` is the check that ties a set of results to
a source tree. It asserts only that the fields are *well-formed*:

```python
if not isinstance(adapter_commit, str) or len(adapter_commit) != 40 \
        or set(adapter_commit) - digest_chars:
    invalid.append(...)
```

A foreign repository's HEAD is forty hex characters and a digest over its files
is sixty-four, so both pass. The binding check would confirm a release binding
to a tree that contains none of the code that produced the results, and a
reader following `git_commit` to reproduce a number would check out a commit
that either does not exist in this repository or is an unrelated project's.

The second-order effect is on the sweep. `sweep.py:936-938` stamps the same
three values on the manifest, and the source hash is what would otherwise
notice that the oracle or the host material changed under a running sweep.
Bound to an outer repository, that hash does not move when TaskBound's own
files change — so the guard stops guarding. Vendored into a busy monorepo it
fails the other way, changing on every unrelated commit and refusing valid
resumes.

## Blast radius

None realised. Every run to date was launched from the repository root of a
plain clone, where the working directory *is* the source tree and all three
fields were correct — `results/local-deepseek-v4-flash/` and the Stage 1 pilot
both carry the right commit. The defect became reachable rather than
theoretical with this release, which adds a `taskbound` console script: an
installed entry point makes running from an arbitrary working directory the
normal thing to do rather than an unusual one.

## What was implemented

`runner._source_repo_root()` resolves the checkout **the imported package came
from**, and every one of the three functions now asks `git` about that root via
`-C` instead of inheriting the working directory. Two conditions must hold or
it returns `None`:

1. `git -C <package dir> rev-parse --show-toplevel` succeeds, and
2. that repository actually **tracks** `taskbound/runner.py`.

The second is what separates a real checkout from an unpacked copy sitting
inside someone else's. Containment is not provenance.

When the root is `None` the fields become `"unknown"` / `None`, which is the
safe direction: `"unknown"` is neither 40 nor 64 hex characters, so
`validate_release_binding` refuses it. A run that cannot say where it came from
is denied a release binding rather than handed a false one.

`_git_dirty` needed splitting rather than moving, because it was answering two
different questions at once and only one of them is about the source tree:

- the **source tree of record** has uncommitted changes or an untracked
  importable file — asked of the package's checkout;
- the **working directory** has an untracked importable file — which stays a
  working-directory question, because `python -m` puts the working directory
  first on `sys.path`, so an untracked `openai.py` there shadows the SDK no
  matter which checkout the package came from.

`_untracked_importable(where)` is factored out and both are OR-ed.

Regression tests are in `tests/test_end_to_end.py`: the working-directory
shadowing case with the source tree held fixed, provenance unchanged across a
`chdir` into another repository, the unpacked-inside-a-foreign-repo case
returning `unknown`, and one asserting that `unknown` cannot satisfy the
binding check's format rules.

## Why the suite did not catch it

The one test that touched these functions,
`test_source_provenance_rejects_untracked_imports_but_allows_results`, worked
by `chdir`-ing into a throwaway git repository and reading `_git_dirty()` back
— that is, it *depended on* the cwd sensitivity that is the defect, and used it
as a convenient fixture. A test built on a behaviour cannot fail because of it.

Every other test ran from the repository root, where the working directory and
the source tree coincide, so no test ever distinguished the two. The general
shape: the code took an ambient input (the process working directory) for an
explicit one (which source tree this is), and the test suite shared the
ambience.
