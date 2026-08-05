"""Regularized mixed-effects logistic regression (plan §9.1, milestone 7).

The pre-registered primary model has population-level coefficients for the
design and random intercepts for request family, paraphrase, injection,
placement, and cell. Fitting it is what makes the reported intervals account
for the fact that three paraphrases of one request are not three independent
observations — pooling them into a Wilson interval over runs would report a
precision the design does not have.

The fit is a Laplace approximation to the marginal likelihood:

* the conditional mode of (beta, u) is found by Newton iteration on the
  penalized joint log-likelihood, with a Gaussian prior on the fixed effects
  that keeps a perfectly separating predictor finite rather than divergent;
* the variance components are estimated by maximizing the Laplace-approximated
  marginal likelihood over their logs, by Nelder-Mead, warm-started from the
  previous mode;
* intervals come from simulating the joint normal approximation at the mode,
  because the reported quantities are standardized combinations of many
  coefficients rather than single ones.

Standard library only, and deliberately small: random intercepts, one link,
one family. Anything the plan does not ask for is not here.

`fit` is deterministic. If it fails to converge, the caller falls back to
`fit_fixed_only`, and §9.1 requires both fits to be disclosed rather than the
model to be simplified after seeing the answer.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

MAX_NEWTON = 60
NEWTON_TOL = 1e-8
DEFAULT_PRIOR_SD = 2.5      # weakly informative on the logit scale
LOG_SD_BOUNDS = (-6.0, 3.0)  # a component below exp(-6) is indistinguishable from zero


# --- small dense linear algebra -----------------------------------------
def cholesky(matrix: list[list[float]]) -> list[list[float]]:
    """Lower-triangular L with L Lᵀ = matrix. Raises on a non-positive matrix."""
    n = len(matrix)
    lower = [[0.0] * n for _ in range(n)]
    for i in range(n):
        row = lower[i]
        for j in range(i + 1):
            total = matrix[i][j] - sum(row[k] * lower[j][k] for k in range(j))
            if i == j:
                if total <= 0:
                    raise ValueError("matrix is not positive definite")
                row[j] = math.sqrt(total)
            else:
                row[j] = total / lower[j][j]
    return lower


def cho_solve(lower: list[list[float]], rhs: Sequence[float]) -> list[float]:
    n = len(lower)
    y = [0.0] * n
    for i in range(n):
        y[i] = (rhs[i] - sum(lower[i][k] * y[k] for k in range(i))) / lower[i][i]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (y[i] - sum(lower[k][i] * x[k] for k in range(i + 1, n))) / lower[i][i]
    return x


def cho_logdet(lower: list[list[float]]) -> float:
    return 2.0 * sum(math.log(lower[i][i]) for i in range(len(lower)))


def sample_normal(
    mean: Sequence[float], lower: list[list[float]], rng: random.Random
) -> list[float]:
    """Draw from N(mean, (L Lᵀ)⁻¹) given the Cholesky factor of the precision."""
    n = len(mean)
    z = [rng.gauss(0.0, 1.0) for _ in range(n)]
    # Solving Lᵀ x = z gives x with covariance (L Lᵀ)⁻¹.
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (z[i] - sum(lower[k][i] * x[k] for k in range(i + 1, n))) / lower[i][i]
    return [mean[i] + x[i] for i in range(n)]


# --- model -------------------------------------------------------------
@dataclass
class RandomFactor:
    """One `(1 | group)` term: an intercept per level of a grouping factor."""

    name: str
    levels: list[str]
    index: list[int]  # row -> level index

    @property
    def n_levels(self) -> int:
        return len(self.levels)


@dataclass
class Design:
    y: list[int]
    x: list[list[float]]          # n x p, dense: the design is small and full
    fixed_names: list[str]
    factors: list[RandomFactor]

    @property
    def n(self) -> int:
        return len(self.y)

    @property
    def p(self) -> int:
        return len(self.fixed_names)


@dataclass
class Fit:
    design: Design
    beta: list[float]
    u: list[float]
    log_sd: list[float]
    precision_chol: list[list[float]]
    converged: bool
    n_evaluations: int
    method: str = "laplace"
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def sd(self) -> dict[str, float]:
        return {f.name: math.exp(s) for f, s in zip(self.design.factors, self.log_sd)}

    @property
    def variance(self) -> dict[str, float]:
        return {name: s * s for name, s in self.sd.items()}

    def coefficient(self, name: str) -> float:
        return self.beta[self.design.fixed_names.index(name)]


def build_design(
    rows: Sequence[dict[str, Any]],
    outcome: str,
    fixed: Sequence[str],
    random_terms: Sequence[str],
) -> Design:
    """Treatment-coded dense design from records, in the plan's formula notation.

    `a:b` is the interaction alone; `a*b` expands to the main effects and every
    interaction between them, so `condition * entry_point * induced_action`
    means what §9.1 says it means rather than the three-way term on its own.

    Reference levels are the first in sorted order, so the encoding does not
    depend on the order results happened to be read in.
    """
    y = [int(bool(r[outcome])) for r in rows]
    columns: list[tuple[str, list[float]]] = [("(intercept)", [1.0] * len(rows))]
    for term in expand_terms(fixed):
        parts = term.split(":")
        pieces = [_dummies(rows, part) for part in parts]
        for combination in _cross(pieces):
            label = ":".join(name for name, _ in combination)
            values = [
                math.prod(column[i] for _, column in combination) for i in range(len(rows))
            ]
            if any(values):
                columns.append((label, values))

    x = [[column[i] for _, column in columns] for i in range(len(rows))]
    factors = []
    for term in random_terms:
        keys = [_group_key(r, term) for r in rows]
        levels = sorted(set(keys))
        lookup = {level: i for i, level in enumerate(levels)}
        factors.append(RandomFactor(term, levels, [lookup[k] for k in keys]))
    return Design(y=y, x=x, fixed_names=[name for name, _ in columns], factors=factors)


def expand_terms(fixed: Sequence[str]) -> list[str]:
    """`a*b*c` -> every non-empty subset, ordered by degree, deduplicated."""
    out: list[str] = []
    for term in fixed:
        if "*" not in term:
            if term not in out:
                out.append(term)
            continue
        parts = term.split("*")
        subsets: list[list[str]] = []
        for mask in range(1, 1 << len(parts)):
            subsets.append([parts[i] for i in range(len(parts)) if mask >> i & 1])
        for subset in sorted(subsets, key=lambda s: (len(s), s)):
            label = ":".join(subset)
            if label not in out:
                out.append(label)
    return out


def _group_key(row: dict[str, Any], term: str) -> str:
    return "|".join(str(row[part]) for part in term.split(":"))


def _dummies(rows: Sequence[dict[str, Any]], variable: str) -> list[tuple[str, list[float]]]:
    values = sorted({str(r[variable]) for r in rows})
    return [
        (f"{variable}[{value}]", [1.0 if str(r[variable]) == value else 0.0 for r in rows])
        for value in values[1:]  # first sorted level is the reference
    ]


def _cross(pieces: list[list[tuple[str, list[float]]]]) -> list[list[tuple[str, list[float]]]]:
    out: list[list[tuple[str, list[float]]]] = [[]]
    for piece in pieces:
        out = [prefix + [item] for prefix in out for item in piece]
    return out


# --- the penalized joint fit --------------------------------------------
def _theta_blocks(design: Design) -> list[tuple[int, int]]:
    """Where each random factor's intercepts sit in the stacked parameter vector."""
    blocks = []
    offset = design.p
    for factor in design.factors:
        blocks.append((offset, offset + factor.n_levels))
        offset += factor.n_levels
    return blocks


def _linear_predictor(design: Design, theta: Sequence[float], blocks) -> list[float]:
    eta = []
    for i in range(design.n):
        value = sum(design.x[i][j] * theta[j] for j in range(design.p))
        for factor, (start, _) in zip(design.factors, blocks):
            value += theta[start + factor.index[i]]
        eta.append(value)
    return eta


def _newton(
    design: Design,
    log_sd: Sequence[float],
    prior_sd: float,
    start: Sequence[float] | None,
) -> tuple[list[float], list[list[float]], float, bool]:
    """Conditional mode of (beta, u) and the Cholesky factor of its precision."""
    blocks = _theta_blocks(design)
    size = blocks[-1][1] if blocks else design.p
    theta = list(start) if start is not None and len(start) == size else [0.0] * size
    variances = [math.exp(2.0 * s) for s in log_sd]
    prior_precision = [1.0 / (prior_sd * prior_sd)] * design.p
    for factor, variance, (start_i, end_i) in zip(design.factors, variances, blocks):
        prior_precision.extend([1.0 / variance] * (end_i - start_i))

    converged = False
    for _ in range(MAX_NEWTON):
        eta = _linear_predictor(design, theta, blocks)
        gradient = [-prior_precision[k] * theta[k] for k in range(size)]
        hessian = [[0.0] * size for _ in range(size)]
        for k in range(size):
            hessian[k][k] = prior_precision[k]

        for i in range(design.n):
            mu = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, eta[i]))))
            weight = max(mu * (1.0 - mu), 1e-10)
            residual = design.y[i] - mu
            # The row's non-zero columns: the dense fixed block plus exactly one
            # intercept per random factor. Exploiting that is what keeps this
            # tractable without a sparse matrix library.
            active = [(j, design.x[i][j]) for j in range(design.p) if design.x[i][j]]
            for factor, (start_i, _) in zip(design.factors, blocks):
                active.append((start_i + factor.index[i], 1.0))
            for a, (ja, va) in enumerate(active):
                gradient[ja] += residual * va
                for jb, vb in active[a:]:
                    contribution = weight * va * vb
                    hessian[ja][jb] += contribution
                    if ja != jb:
                        hessian[jb][ja] += contribution

        try:
            lower = cholesky(hessian)
        except ValueError:
            return theta, [], float("nan"), False
        step = cho_solve(lower, gradient)
        theta = [theta[k] + step[k] for k in range(size)]
        if max(abs(s) for s in step) < NEWTON_TOL:
            converged = True
            break

    eta = _linear_predictor(design, theta, blocks)
    log_likelihood = sum(
        design.y[i] * eta[i] - math.log1p(math.exp(min(30.0, eta[i]))) for i in range(design.n)
    )
    penalty = 0.5 * sum(prior_precision[k] * theta[k] ** 2 for k in range(size))
    joint = log_likelihood - penalty
    # Laplace: log |2 pi Sigma_prior|^-1/2 terms cancel into the constant, leaving
    # the joint mode value against half the log determinant of the precision.
    for factor, variance in zip(design.factors, variances):
        joint -= 0.5 * factor.n_levels * math.log(variance)
    marginal = joint - 0.5 * cho_logdet(lower)
    return theta, lower, marginal, converged


def fit(
    design: Design,
    prior_sd: float = DEFAULT_PRIOR_SD,
    # The primary model has five variance components, and a simplex in five
    # dimensions routinely needs a couple of hundred evaluations. Budgeting for
    # 200 makes convergence depend on how far the start happens to be from the
    # answer, which would silently route well-behaved data to the fallback.
    max_evaluations: int = 600,
    tolerance: float = 1e-4,
) -> Fit:
    """Laplace fit: Nelder-Mead over log standard deviations, warm-started."""
    if not design.factors:
        return fit_fixed_only(design, prior_sd)

    cache: dict[tuple[float, ...], tuple[float, list[float], list[list[float]], bool]] = {}
    warm: dict[str, Any] = {"theta": None}
    evaluations = 0

    def objective(log_sd: Sequence[float]) -> float:
        nonlocal evaluations
        key = tuple(round(s, 6) for s in log_sd)
        if key in cache:
            return -cache[key][0]
        clipped = [max(LOG_SD_BOUNDS[0], min(LOG_SD_BOUNDS[1], s)) for s in log_sd]
        theta, lower, marginal, ok = _newton(design, clipped, prior_sd, warm["theta"])
        evaluations += 1
        if not ok or math.isnan(marginal):
            return 1e12
        warm["theta"] = theta
        cache[key] = (marginal, theta, lower, ok)
        return -marginal

    start = [math.log(0.5)] * len(design.factors)
    best, converged = nelder_mead(objective, start, max_evaluations, tolerance)
    key = tuple(round(s, 6) for s in best)
    if key not in cache:
        objective(best)
        key = tuple(round(s, 6) for s in best)
    marginal, theta, lower, newton_ok = cache[key]

    log_sd = [max(LOG_SD_BOUNDS[0], min(LOG_SD_BOUNDS[1], s)) for s in best]
    return Fit(
        design=design,
        beta=theta[: design.p],
        u=theta[design.p:],
        log_sd=log_sd,
        precision_chol=lower,
        converged=converged and newton_ok,
        n_evaluations=evaluations,
        diagnostics={
            "marginal_loglik": marginal,
            "prior_sd": prior_sd,
            "at_variance_boundary": [
                name for name, s in zip([f.name for f in design.factors], log_sd)
                if abs(s - LOG_SD_BOUNDS[0]) < 1e-6
            ],
        },
    )


def fit_fixed_only(design: Design, prior_sd: float = DEFAULT_PRIOR_SD) -> Fit:
    """The pre-registered deterministic fallback: no random effects.

    Used when the primary fit fails its diagnostics. §9.1 requires the fallback
    to be fixed in advance and both fits disclosed — the model is never
    simplified after seeing the answer.
    """
    flat = Design(y=design.y, x=design.x, fixed_names=design.fixed_names, factors=[])
    theta, lower, marginal, converged = _newton(flat, [], prior_sd, None)
    return Fit(
        design=flat, beta=theta, u=[], log_sd=[], precision_chol=lower,
        converged=converged, n_evaluations=1, method="fixed_effects_fallback",
        diagnostics={"marginal_loglik": marginal, "prior_sd": prior_sd,
                     "note": "random effects dropped; clustering is not accounted for"},
    )


# --- inference ----------------------------------------------------------
def simulate(fit_result: Fit, draws: int, seed: int) -> list[list[float]]:
    """Draws of (beta, u) from the normal approximation at the mode.

    The reported quantities are standardized combinations of many coefficients,
    so their intervals come from propagating the joint uncertainty rather than
    from a delta-method approximation applied per coefficient.
    """
    rng = random.Random(seed)
    mean = list(fit_result.beta) + list(fit_result.u)
    return [sample_normal(mean, fit_result.precision_chol, rng) for _ in range(draws)]


def design_row(design: Design, values: dict[str, str]) -> dict[str, float]:
    """The fixed-effect covariate vector for a named combination of levels.

    Used for standardization: a reported rate is a predeclared weighting of
    per-cell predictions, not the accidental proportions of the observed data.
    """
    row = {"(intercept)": 1.0}
    for name in design.fixed_names[1:]:
        active = True
        for part in name.split(":"):
            variable, level = part[:-1].split("[", 1)
            if str(values.get(variable)) != level:
                active = False
                break
        row[name] = 1.0 if active else 0.0
    return row


def predict(design: Design, theta: Sequence[float], row: dict[str, float]) -> float:
    """Probability for a named combination of fixed effects, at zero random effect."""
    eta = 0.0
    for name, value in row.items():
        eta += theta[design.fixed_names.index(name)] * value
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, eta))))


def interval(values: Sequence[float], level: float = 0.95) -> tuple[float, float]:
    ordered = sorted(values)
    if not ordered:
        return (float("nan"), float("nan"))
    tail = (1.0 - level) / 2.0
    return (_quantile(ordered, tail), _quantile(ordered, 1.0 - tail))


def _quantile(ordered: Sequence[float], q: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    low = int(math.floor(position))
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (position - low) * (ordered[high] - ordered[low])


# --- optimizer ----------------------------------------------------------
def nelder_mead(
    objective: Callable[[Sequence[float]], float],
    start: Sequence[float],
    max_evaluations: int,
    tolerance: float,
) -> tuple[list[float], bool]:
    """Derivative-free simplex search, deterministic from its starting point."""
    n = len(start)
    simplex = [list(start)]
    for i in range(n):
        point = list(start)
        point[i] += 0.5
        simplex.append(point)
    values = [objective(p) for p in simplex]
    evaluations = len(simplex)

    while evaluations < max_evaluations:
        order = sorted(range(len(simplex)), key=lambda i: values[i])
        simplex = [simplex[i] for i in order]
        values = [values[i] for i in order]
        if abs(values[-1] - values[0]) < tolerance:
            return simplex[0], True

        centroid = [sum(p[j] for p in simplex[:-1]) / n for j in range(n)]
        worst = simplex[-1]
        reflected = [centroid[j] + (centroid[j] - worst[j]) for j in range(n)]
        f_reflected = objective(reflected)
        evaluations += 1

        if f_reflected < values[0]:
            expanded = [centroid[j] + 2.0 * (centroid[j] - worst[j]) for j in range(n)]
            f_expanded = objective(expanded)
            evaluations += 1
            simplex[-1], values[-1] = (
                (expanded, f_expanded) if f_expanded < f_reflected else (reflected, f_reflected)
            )
        elif f_reflected < values[-2]:
            simplex[-1], values[-1] = reflected, f_reflected
        else:
            contracted = [centroid[j] + 0.5 * (worst[j] - centroid[j]) for j in range(n)]
            f_contracted = objective(contracted)
            evaluations += 1
            if f_contracted < values[-1]:
                simplex[-1], values[-1] = contracted, f_contracted
            else:
                for i in range(1, len(simplex)):
                    simplex[i] = [
                        simplex[0][j] + 0.5 * (simplex[i][j] - simplex[0][j]) for j in range(n)
                    ]
                    values[i] = objective(simplex[i])
                    evaluations += 1
    order = sorted(range(len(simplex)), key=lambda i: values[i])
    return simplex[order[0]], False
