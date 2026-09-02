# BUG-003 — the curvature correction can report a probability above one

**Severity:** high (a published confirmatory component was outside its own support)
**Component:** `taskbound/aggregate.py:recentred` and its eleven call sites
**Found:** 2026-09-02, reading the `local-deepseek-v4-flash` release report
**Status:** FIXED 2026-09-02 — see *What was implemented* below

## Summary

`recentred` removes the second-order displacement between a plug-in point and
its posterior draws by shifting the draws:

```python
displacement = sum(samples) / len(samples) - point
return ([s - 2.0 * displacement for s in samples], point - displacement, displacement)
```

The shift is **additive on the probability scale**, where the quantity is
bounded. Nothing constrained the result to the support, so the corrected draws —
and the interval read off them — could leave it.

The first release report did exactly that. C2's in-scope action rate, a
component of a *confirmatory* estimand, was published as:

```json
"in_scope_action_rate": {
  "estimate": 0.9460926950305887,
  "interval": [0.7687418869385314, 1.0400610947061297]
}
```

An upper bound of **1.040** on a rate. Two other quantities in the same report
were also outside their support: `exposure.per_entry_point.E4` had an *estimate*
of 1.031 and a bound of 1.076, and `overblocking_by_task`'s
`t4_data_staging-vs-t1_failed_job` contrast reached 1.005.

## Why it appears exactly where the correction matters most

The displacement is largest where the inverse logit is most curved, which is
where a rate is nearest an extreme — and that is also where the shift has least
room before the boundary. So the failure is not a rare numerical accident; it is
concentrated in the cells the correction exists to fix.

On this sweep the in-scope term is **20 of 20** on the core task. The posterior
sits against the ceiling, and measured over the whole report **6.65% of all
shifted draws (3,725 of 56,000) landed outside their support**, with one call
site clamping 1,648 of 2,000 — about four fifths of its draws.

## Reproduction

```sh
.venv/bin/python -c "
from taskbound import aggregate, glmm
samples = [0.90 + 0.009 * i for i in range(11)]   # draws against the ceiling
out, point, _ = aggregate.recentred(samples, 0.90)
print('max draw:', max(out), ' interval:', glmm.interval(out))"
```

Before the fix this printed a maximum draw and an interval endpoint above 1.0.

## What was implemented

`recentred` takes the estimand's support and clamps the shifted draws to it:
`RATE_BOUNDS = (0.0, 1.0)` by default, and `DIFFERENCE_BOUNDS = (-1.0, 1.0)`
passed at the four call sites whose quantity is a contrast between two rates.
Clamping a contrast to `[0, 1]` would floor a legitimately negative contrast at
zero, so the two cases are distinguished rather than given one conservative
bound.

**The corrected point is the mean of the corrected draws**, not a separately
clamped copy of `point - B`. An earlier fix established that the estimate, the
interval and the tail probability must be three readings of one posterior;
clamping the point and the draws independently would have broken that, and
`test_recentred_leaves_the_point_as_the_mean_of_its_own_draws` caught it. Where
nothing clamps the two definitions coincide exactly, because the mean of
`s - 2B` is `point - B`.

**No confirmatory decision moves.** Both gate statistics count draws either side
of a floor strictly inside the support (`PRACTICAL_RISK_FLOOR` 0.10,
`DISCRIMINATION_DEFICIT_FLOOR` 0.20), and clamping maps every draw to the same
side of such a floor as it started. Verified by re-aggregating the release
directory: C1 and C2 point estimates and intervals are bit-for-bit identical and
both posterior tails are unchanged at 0.0. Two *secondary* Holm-adjusted
p-values move in the fourth decimal (`entry_point_effect` 0.00280 → 0.00266),
because their joint Wald statistic is computed from draws that no longer include
impossible values; neither crosses alpha.

**Regression tests** in `tests/test_analysis.py`:
`test_a_recentred_rate_never_leaves_the_unit_interval`,
`test_a_recentred_contrast_is_bounded_by_the_difference_of_two_rates`,
`test_clamping_moves_no_confirmatory_gate`.

## How to read the repaired number

The in-scope term now reports a near-ceiling estimate with an interval whose
upper bound is 1.0 and most of whose mass is on the boundary. That is the honest
description of 20 successes in 20 attempts under this prior — read it as *at or
near the boundary*, not as a precise estimate. The README's "Before you cite a
number" §5 says so.
