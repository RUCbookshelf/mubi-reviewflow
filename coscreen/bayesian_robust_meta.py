"""Robust-prior normal-normal random-effects meta-analysis with explicit priors.

Extends :mod:`coscreen.bayesian_normal_meta` with thick-tailed prior families:
the pooled effect may follow a Student-t prior (degrees of freedom must be
given explicitly; there are no default priors) and the heterogeneity standard
deviation may follow an optional half-t prior. A Student-t prior is handled
through its normal scale-mixture identity, ``t_df(mu; m0, s0) = integral of
N(mu; m0, s0^2/g) Gamma(g; df/2, rate df/2) dg``, which restores the
closed-form conditional posterior for ``mu`` so that only ``log(tau)`` and
``log(g)`` have to be integrated numerically.

The joint posterior is integrated on a deterministic panel grid: one
Gauss-Legendre panel centered on the posterior mode resolves the bulk, and
geometrically growing tail panels extend each axis until the density is
negligible. Every fit re-runs the quadrature with doubled node counts on the
same panels and is rejected unless the two grids agree, so reported summaries
carry an explicit grid-refinement convergence check.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.optimize import brentq, minimize
from scipy.special import ndtr
from scipy.stats import gamma as gamma_distribution

from coscreen.bayesian_normal_meta import (
    RATIOS, _analysis_scale, _number, _parse_study_rows, _tau_scale_label,
)

MU_FAMILIES = {"normal", "student_t"}
TAU_FAMILIES = {"half_normal", "half_student_t"}
# MM11-07: the cap stays at the historical 1e12 -- lowering it would reject
# legal inputs that the baseline integrates fine (a t prior with df=1e9 is a
# normal limit to <1e-9, and those fits succeed). The defect was never the
# cap itself but the unhelpful diagnostic: df near the cap can exhaust grid
# refinement, and the failure said only "grid refinement did not converge".
#
# Round 3 nailed the *scope* of the replacement advice. The round-2 message
# ("try a smaller df, or the normal family") fired for every student_t grid
# failure, including the df 3/5/10/30/100 values users routinely pick, where a
# probe (see _grid_failure_message) shows that neither remedy settles: e.g. on
# y=[-0.57,-0.54], se=[0.24,0.14] every df from 1 to 1e7 fails and the normal
# family fails too. Advice is now emitted only where it is *measured* to work:
# df at or above _DF_DIAGNOSTIC_FLOOR, and only when smaller df shows a
# threshold pattern on these same rows. Everywhere else the failure keeps a
# neutral diagnostic, so no accepted verdict changes (raise sites only).
_MAX_PRIOR_DF = 1e12
# Below this df the t prior is a genuine modeling choice rather than a
# near-normal formality, so "use a smaller df" is not a knob worth probing;
# such failures take the neutral diagnostic and never pay probe cost.
_DF_DIAGNOSTIC_FLOOR = 1e3
# Probes re-fit the same rows at geometrically smaller df. A recommendation
# needs a leading run of at least _DF_MONOTONE_RUN settled fits with no settled
# fit above a failure -- a "df too large" threshold, not a lucky value: on the
# reviewed counterexample df=1e8 settles while 1e7 and 1e9 both fail, and
# recommending that lottery is the very misleading advice this round removes.
_DF_DIAGNOSTIC_LADDER_STEPS = 3
_DF_PROBE_MIN = 2.0
_DF_MONOTONE_RUN = 2
_LOG_2PI = math.log(2.0 * math.pi)
_BULK_NODES = 48
_TAIL_NODES = 14
_TAIL_GROWTH = 1.5
_MAX_TAIL_PANELS = 60
_BULK_SD_SPAN = 10.0
# F2-62: successive grids are refined until two of them agree, up to this node
# multiple (a multiple of the bulk panel count). Inputs that already settle at
# the first refinement never see more work than before.
_REFINEMENT_MAX_MULTIPLE = 16
# F2-59: one grid cell costs a full posterior fit; cap the number of cells a
# single request may ask for so the endpoint cannot be used to burn minutes of
# CPU (the request schema only enforces ``min_length=1``).
MAX_PRIOR_GRID_CELLS = 100


def _validate_mu_prior(family: object, mean: object, scale: object, df: object) -> dict:
    if not isinstance(family, str) or family not in MU_FAMILIES:
        raise ValueError("mu prior family must be 'normal' or 'student_t'")
    mean = _number(mean, "mu prior mean")
    scale = _number(scale, "mu prior scale")
    if scale <= 0:
        raise ValueError("mu prior scale must be positive for a proper prior")
    if family == "student_t":
        if df is None:
            raise ValueError("mu prior df is required for a student_t prior; there is no default")
        df = _number(df, "mu prior df")
        if df <= 0:
            raise ValueError("mu prior df must be positive")
        if df > _MAX_PRIOR_DF:
            raise ValueError("mu prior df is too large for stable quadrature; use the normal family")
    else:
        if df is not None:
            raise ValueError("mu prior df must not be provided for the normal family")
        df = None
    return {"family": family, "mean": mean, "scale": scale, "df": df}


def _validate_tau_prior(family: object, scale: object, df: object) -> dict:
    if not isinstance(family, str) or family not in TAU_FAMILIES:
        raise ValueError("tau prior family must be 'half_normal' or 'half_student_t'")
    scale = _number(scale, "tau prior scale")
    if scale <= 0:
        raise ValueError("tau prior scale must be positive for a proper prior")
    if family == "half_student_t":
        if df is None:
            raise ValueError(
                "tau prior df is required for a half_student_t prior; there is no default"
            )
        df = _number(df, "tau prior df")
        if df <= 0:
            raise ValueError("tau prior df must be positive")
        if df > _MAX_PRIOR_DF:
            raise ValueError(
                "tau prior df is too large for stable quadrature; use the half_normal family"
            )
    else:
        if df is not None:
            raise ValueError("tau prior df must not be provided for the half_normal family")
        df = None
    return {"family": family, "scale": scale, "df": df}


def _grid_failure_message(
    rows: Sequence[Mapping],
    *,
    df_mu: float,
    mu_prior_mean: object,
    mu_prior_scale: object,
    tau_prior_family: object,
    tau_prior_scale: object,
    tau_prior_df: object,
    probe: bool,
) -> str:
    """Diagnostic for an exhausted grid refinement under a Student-t mu prior.

    MM11-07 round 3: advice is emitted only for the sub-domain where it is
    *measured* to work, and only with a threshold, not a lucky, pattern:

    - ``df_mu`` below :data:`_DF_DIAGNOSTIC_FLOOR` is a regular t setting, not
      a near-normal formality; those failures (the df 3/5/10/30/100 values a
      user routinely picks) get the neutral diagnostic and pay no probe cost.
    - above the floor, df values one, two, ... ladder steps below ``df_mu`` are
      re-fitted on the same rows. "Use a smaller df" is claimed only when those
      probes show a *threshold* pattern on the df axis: a leading run of at
      least :data:`_DF_MONOTONE_RUN` settled fits, with no settled fit above
      any failure. A single workable df surrounded by failures is a numerical
      coincidence, not a cause, and buys no advice. The largest df in the run
      is the one named, since it is the closest workable setting.
    - the normal family is mentioned only if it also settles on these rows.

    Every failure that does not clear those gates is reported neutrally, so the
    message never recommends a knob that this data shows to be ineffective.
    """
    header = (
        f"grid refinement did not converge with mu prior df={df_mu:g}: the "
        "adaptive quadrature did not settle for this prior and data combination. "
    )
    footer = " This is not by itself evidence of separated or weakly identified data."
    if df_mu < _DF_DIAGNOSTIC_FLOOR:
        return header + (
            f"df={df_mu:g} is a regular Student-t setting, so this failure is not "
            "attributed to df or to the choice of prior family."
        ) + footer
    if not probe:
        return header + "The df setting is not shown to be the cause here." + footer
    common = {
        "mu_prior_mean": mu_prior_mean,
        "mu_prior_scale": mu_prior_scale,
        "tau_prior_family": tau_prior_family,
        "tau_prior_scale": tau_prior_scale,
        "tau_prior_df": tau_prior_df,
    }
    ladder = []
    candidate = df_mu
    for _ in range(_DF_DIAGNOSTIC_LADDER_STEPS):
        candidate = candidate / 1e2
        if candidate < _DF_PROBE_MIN:
            break
        ladder.append(candidate)
    # Ascending df. A "df is too large" story requires a threshold pattern on
    # that axis: a prefix of settled fits followed by failures, never a
    # success above a failure again (that would be a lucky df, not a cause).
    ascending = sorted(ladder)
    flags = [_probe_settles(rows, value, common) for value in ascending]
    run = 0
    while run < len(flags) and flags[run]:
        run += 1
    threshold_pattern = run >= _DF_MONOTONE_RUN and not any(flags[run:])
    if not threshold_pattern:
        # Two neutral variants, because saying "none settled" would itself be a
        # false claim when a probe did settle but only as an isolated island
        # between failures (the lottery this round refuses to recommend).
        if any(flags):
            detail = (
                "Smaller df values did not settle consistently on these rows (a "
                "settled value appeared only between failures), so the df "
                "setting is not shown to be the cause here."
            )
        else:
            detail = (
                "No alternative df value tried on these rows settled, so the df "
                "setting is not shown to be the cause here."
            )
        return header + detail + footer
    verified_df = ascending[run - 1]
    message = header + (
        f"A smaller df does settle on these rows (df={verified_df:g}); retry "
        "with that setting if it is acceptable for your prior."
    )
    if _probe_settles(rows, None, common):
        message += " The normal family also settles on these rows."
    return message + footer


def _probe_settles(rows: Sequence[Mapping], df: float | None, common: dict) -> bool:
    """True iff re-fitting ``rows`` with this mu prior completes without error.

    ``df=None`` probes the normal family. A probe is diagnostic only, so it
    swallows every failure -- including non-``ValueError`` ones -- rather than
    letting an exploratory re-fit turn the caller's clean rejection into a
    different exception.
    """
    family = "normal" if df is None else "student_t"
    try:
        fit_bayesian_robust_meta(
            rows, mu_prior_family=family, mu_prior_df=df,
            _diagnostic_probe=False, **common)
    except Exception:  # noqa: BLE001 - probes must never mask the real failure
        return False
    return True


def fit_bayesian_robust_meta(
    rows: Sequence[Mapping],
    *,
    mu_prior_mean: object,
    mu_prior_family: object,
    mu_prior_scale: object,
    mu_prior_df: object,
    tau_prior_family: object,
    tau_prior_scale: object,
    tau_prior_df: object,
    _diagnostic_probe: bool = True,
) -> dict:
    """Summarize a normal-normal random-effects model under robust priors.

    Rows have ``study_id``, ``measure``, ``estimate`` and ``se``. Ratio
    estimates are logged internally and their supplied SEs must already be on
    the log scale. The pooled effect takes a Normal(mean, sd) or
    Student-t(mean, scale, df) prior; ``tau`` takes a Half-Normal(scale) or
    Half-t(scale, df) prior, all declared on the analysis scale. ``scale`` is
    the scale parameter of the t distribution, not its standard deviation.
    Every prior argument is required: there is no default prior.

    ``_diagnostic_probe`` is private: it is False only for the re-fits that
    :func:`_grid_failure_message` runs to verify a candidate remedy, so those
    probes cannot recursively probe again.
    """
    mu_prior = _validate_mu_prior(mu_prior_family, mu_prior_mean, mu_prior_scale, mu_prior_df)
    tau_prior = _validate_tau_prior(tau_prior_family, tau_prior_scale, tau_prior_df)
    ids, y, se, measure = _parse_study_rows(rows)
    analysis_scale = _analysis_scale(measure)

    m0, s0 = mu_prior["mean"], mu_prior["scale"]
    unit = max(float(np.max(np.abs(y))), float(np.max(se)), abs(m0), s0, tau_prior["scale"])
    y_raw = y
    y, se, m0, s0 = y / unit, se / unit, m0 / unit, s0 / unit
    a_tau = tau_prior["scale"] / unit
    if np.any((y_raw != 0) & (y == 0)) or np.any(se == 0) or s0 == 0 or a_tau == 0:
        raise ValueError("input scales exceed stable floating-point precision")
    se2, prior_var = se * se, s0 * s0
    if not np.isfinite(se2).all() or np.any(se2 <= 0) or not math.isfinite(prior_var) or prior_var <= 0:
        raise ValueError("input scales exceed stable floating-point precision")

    student_mu = mu_prior["family"] == "student_t"
    half_t = tau_prior["family"] == "half_student_t"
    df_mu, df_tau = mu_prior["df"], tau_prior["df"]
    k_mix = 0.5 * df_mu if student_mu else None
    resid = y - m0
    n = len(y)

    def log_posterior(u_values, w_values):
        """Joint log posterior on a (log tau, log g) tensor of grid points.

        ``u_values`` and ``w_values`` are one-dimensional node arrays; the
        mixing dimension is a single ``w = 0`` point when the mu prior is
        normal. Returns the log density (constants dropped) and the
        conditional posterior mean and variance of mu, each of shape
        ``(len(u), len(w))``.
        """
        u = np.asarray(u_values, dtype=float).reshape(-1, 1, 1)
        if student_mu:
            lam = np.exp(np.asarray(w_values, dtype=float).reshape(1, -1, 1)) / prior_var
        else:
            lam = np.full((1, 1, 1), 1.0 / prior_var)
        variance = se2.reshape(1, 1, -1) + a_tau * a_tau * np.exp(2.0 * u) + 0.0 * lam
        weight = 1.0 / variance
        total_weight = weight.sum(-1)
        first = weight @ resid
        second = weight @ (resid * resid)
        post_var = 1.0 / (lam[..., 0] + total_weight)
        post_mean = m0 + post_var * first
        quadratic = second - post_var * first * first
        log_marginal = -0.5 * (
            n * _LOG_2PI
            + np.log(variance).sum(-1)
            + np.log1p(total_weight / lam[..., 0])
            + quadratic
        )
        total = log_marginal + u.reshape(-1, 1)
        if half_t:
            total = total - 0.5 * (df_tau + 1.0) * np.log1p(np.exp(2.0 * u.reshape(-1, 1)) / df_tau)
        else:
            total = total - 0.5 * np.exp(2.0 * u.reshape(-1, 1))
        if student_mu:
            w = np.asarray(w_values, dtype=float).reshape(1, -1)
            total = total + k_mix * w - k_mix * np.exp(w)
        total = np.where(np.isfinite(total), total, -np.inf)
        return total, post_mean, post_var

    # Bracket the posterior mode on a coarse grid; the mode only fixes the
    # density scale, so the subsequent local refinement is a bonus, not a
    # load-bearing step.
    data_scale = max(float(np.max(se)), float(np.ptp(y)), s0)
    log_ratio = math.log(data_scale) - math.log(a_tau)
    u_low = max(-740.0, min(-40.0, log_ratio - 40.0))
    u_high = min(350.0, max(40.0, log_ratio + 40.0))
    if student_mu:
        g_lo, g_hi = gamma_distribution.ppf([1e-13, 1.0 - 1e-13], a=k_mix, scale=1.0 / k_mix)
        if not (math.isfinite(g_lo) and math.isfinite(g_hi)) or g_hi <= 0:
            raise ValueError("could not bracket the student-t prior mixing precision")
        w_low = max(-350.0, math.log(max(float(g_lo), 1e-160)))
        w_high = min(320.0, math.log(float(g_hi)))
        if not w_low < w_high:
            raise ValueError("could not bracket the student-t prior mixing precision")

    def coarse_mode():
        u_grid = np.linspace(u_low, u_high, 241)
        w_grid = np.linspace(w_low, w_high, 161) if student_mu else np.zeros(1)
        values, _, _ = log_posterior(u_grid, w_grid)
        iu, iw = np.unravel_index(int(np.argmax(values)), values.shape)
        return u_grid, w_grid, int(iu), int(iw)

    u_grid, w_grid, iu, iw = coarse_mode()
    on_edge = iu in (0, len(u_grid) - 1) or (student_mu and iw in (0, len(w_grid) - 1))
    if on_edge:
        u_low, u_high = max(-740.0, u_low - 40.0), min(350.0, u_high + 40.0)
        if student_mu:
            w_low, w_high = max(-350.0, w_low - 20.0), min(320.0, w_high + 20.0)
        u_grid, w_grid, iu, iw = coarse_mode()
        if iu in (0, len(u_grid) - 1) or (student_mu and iw in (0, len(w_grid) - 1)):
            raise ValueError("could not bracket the posterior density")
    u_star, w_star = float(u_grid[iu]), float(w_grid[iw])

    def point_log_density(u_value, w_value):
        values, _, _ = log_posterior(np.array([float(u_value)]), np.array([float(w_value)]))
        return float(values[0, 0])

    try:
        refined_mode = minimize(
            lambda point: -point_log_density(point[0], point[1]),
            np.array([u_star, w_star]),
            method="Nelder-Mead",
            options={"xatol": 1e-12, "fatol": 1e-12, "maxiter": 2000},
        )
        candidate = [float(refined_mode.x[0]), float(refined_mode.x[1])]
        if all(math.isfinite(v) for v in candidate) and math.isfinite(float(refined_mode.fun)):
            u_star = min(max(candidate[0], u_low), u_high)
            if student_mu:
                w_star = min(max(candidate[1], w_low), w_high)
    except (FloatingPointError, OverflowError, ValueError):
        pass
    peak = max(point_log_density(u_star, w_star), float(np.max(log_posterior(u_grid, w_grid)[0])))

    def laplace_sd(axis):
        center = (u_star, w_star)[axis]
        step = max(1e-4, abs(center) * 1e-6)
        if axis == 0:
            lo, hi = point_log_density(center - step, w_star), point_log_density(center + step, w_star)
        else:
            lo, hi = point_log_density(u_star, center - step), point_log_density(u_star, center + step)
        curvature = (lo - 2.0 * point_log_density(u_star, w_star) + hi) / (step * step)
        if not math.isfinite(curvature) or curvature >= 0:
            return 1.0
        return float(min(50.0, max(1e-8, 1.0 / math.sqrt(-curvature))))

    def axis_panels(mode, sd, lo, hi, edge_density):
        """Build bulk + geometric tail panels on one axis.

        Returns a list of ``(a, b, points, weights)`` groups with relative
        offsets ``(a, b)`` from ``mode``; the bulk panel straddles zero.
        """
        # F2-61: a tail must never be built backwards. ``stop`` is clamped to
        # the bracket limit, so once ``start`` is at (or past) that limit the
        # next step points back inside; the old ``abs(b - a) <= 0`` guard did
        # not fire on that reversal and appended a panel with negative width.
        # ``tensor_pass`` turns that into negative quadrature weights, and the
        # reversed panel covers an interval the bulk panel already integrated,
        # so the summary silently gains a negative double-counted mass (or a
        # quantile bracket fails outright, as in the reported fixture).
        half = min(_BULK_SD_SPAN * sd, max(mode - lo, hi - mode))
        if not half > 0:
            raise ValueError("posterior grid half-width collapsed")
        nodes, weights = leggauss(_BULK_NODES)
        groups = [(-half, half, mode + half * nodes, half * weights)]

        def add_tail(side):
            start = side * half
            width = half / 4.0
            panels = 0
            limit = (hi - mode) if side > 0 else (lo - mode)
            while panels < _MAX_TAIL_PANELS:
                raw = start + side * width * _TAIL_GROWTH
                stop = min(raw, limit) if side > 0 else max(raw, limit)
                if (side > 0 and stop <= start) or (side < 0 and stop >= start):
                    break
                a, b = (start, stop) if side > 0 else (stop, start)
                far = mode + (b if side > 0 else a)
                if edge_density(far) < 1e-16:
                    break
                gq, gw = leggauss(_TAIL_NODES)
                local_a, local_b = a + mode, b + mode
                groups.append((a, b,
                               local_a + (local_b - local_a) * (gq + 1.0) / 2.0,
                               (local_b - local_a) / 2.0 * gw))
                start = stop
                width *= _TAIL_GROWTH
                panels += 1

        add_tail(-1)
        add_tail(+1)
        return groups

    u_panels = sorted(
        axis_panels(
            u_star, laplace_sd(0), u_low, u_high,
            lambda edge: math.exp(min(0.0, point_log_density(edge, w_star) - peak)),
        ),
        key=lambda group: group[0],
    )
    u_bounds = [(a, b) for a, b, _, _ in u_panels]
    u_points = np.concatenate([group[2] for group in u_panels])
    u_weights = np.concatenate([group[3] for group in u_panels])
    if student_mu:
        w_groups = axis_panels(
            w_star, laplace_sd(1), w_low, w_high,
            lambda edge: math.exp(min(0.0, point_log_density(u_star, edge) - peak)),
        )
        w_points = np.concatenate([group[2] for group in w_groups])
        w_weights = np.concatenate([group[3] for group in w_groups])
    else:
        w_points, w_weights = np.zeros(1), np.ones(1)
    if not len(u_points) or not np.isfinite(u_weights).all():
        raise ValueError("could not build the posterior quadrature grid")

    def tensor_pass(bulk_nodes, tail_nodes):
        """Integrate on the panels with (bulk_nodes, tail_nodes) nodes/panel."""
        panels = []
        z = tau_first = mu_first = mu_second = probability = 0.0
        for index, (a, b) in enumerate(u_bounds):
            count = bulk_nodes if a < 0.0 < b else tail_nodes
            nodes, weights = leggauss(count)
            relative = 0.5 * (b - a) * nodes + 0.5 * (b + a)
            scaling = 0.5 * (b - a) * weights
            u_slice = u_star + relative
            log_dens, cond_mean, cond_var = log_posterior(u_slice, w_points)
            dens = np.exp(log_dens - peak)
            dens[~np.isfinite(dens)] = 0.0
            inner = dens @ w_weights
            panels.append((scaling, u_slice, cond_mean, cond_var, dens, inner))
        for scaling, u_slice, cond_mean, cond_var, dens, inner in panels:
            z += float(scaling @ inner)
            tau_first += float(scaling @ (inner * a_tau * np.exp(u_slice)))
            mu_first += float(scaling @ ((dens * cond_mean) @ w_weights))
            mu_second += float(scaling @ ((dens * (cond_var + cond_mean ** 2)) @ w_weights))
            probability += float(
                scaling @ ((dens * ndtr(cond_mean / np.sqrt(cond_var))) @ w_weights)
            )
        if not math.isfinite(z) or z <= 0:
            raise ValueError("posterior grid quadrature collapsed")
        tau_mean = tau_first / z
        mu_mean = mu_first / z
        mu_variance = mu_second / z - mu_mean ** 2
        if not math.isfinite(mu_variance) or mu_variance <= 0:
            raise ValueError("posterior variance of mu is unstable")
        probability = min(1.0, max(0.0, probability / z))

        def mu_cdf(x):
            total = 0.0
            for scaling, _, cond_mean, cond_var, dens, _ in panels:
                total += float(scaling @ (
                    (dens * ndtr((x - cond_mean) / np.sqrt(cond_var))) @ w_weights
                ))
            return min(1.0, max(0.0, total / z))

        panel_cumulative = []
        running = 0.0
        for scaling, _, _, _, _, inner in panels:
            panel_cumulative.append(running)
            running += float(scaling @ inner)

        def tau_cdf(t):
            if t <= 0.0:
                return 0.0
            u_t = math.log(t / a_tau)
            if u_t <= u_star + u_bounds[0][0]:
                return 0.0
            if u_t >= u_star + u_bounds[-1][1]:
                return 1.0
            position = next(
                (index for index, (a, b) in enumerate(u_bounds)
                 if u_star + a <= u_t <= u_star + b),
                len(u_bounds) - 1,
            )
            a, b = u_bounds[position]
            total = panel_cumulative[position]

            def marginal(value):
                log_dens, _, _ = log_posterior(np.array([value]), w_points)
                row = np.exp(log_dens[0] - peak)
                return float(row @ w_weights)

            total += _panel_integral(marginal, u_star + a, u_t)
            return min(1.0, max(0.0, total / z))

        def quantile(cdf, probability, lo, hi, name):
            c_lo, c_hi = cdf(lo), cdf(hi)
            if not c_lo <= probability <= c_hi or lo >= hi:
                raise ValueError(f"could not bracket the posterior {name} quantile")
            try:
                root = brentq(lambda x: cdf(x) - probability, lo, hi,
                              xtol=1e-12, rtol=4 * np.finfo(float).eps, maxiter=200)
            except (ValueError, RuntimeError, OverflowError) as exc:
                raise ValueError(f"posterior {name} quantile calculation failed") from exc
            if abs(cdf(root) - probability) > 1e-6:
                raise ValueError(f"posterior {name} quantile calculation did not converge")
            return float(root)

        mu_lo = min(float(np.min(cond_mean - 8.0 * np.sqrt(cond_var)))
                    for _, _, cond_mean, cond_var, _, _ in panels)
        mu_hi = max(float(np.max(cond_mean + 8.0 * np.sqrt(cond_var)))
                    for _, _, cond_mean, cond_var, _, _ in panels)
        tau_lo = float(a_tau * math.exp(u_star + min(a for a, _ in u_bounds)))
        tau_hi = float(a_tau * math.exp(u_star + max(b for _, b in u_bounds)))
        return {
            "z": z,
            "tau_mean": tau_mean,
            "mu_mean": mu_mean,
            "mu_variance": mu_variance,
            "probability": probability,
            "mu_quantiles": [quantile(mu_cdf, p, mu_lo, mu_hi, "mu") for p in (0.025, 0.5, 0.975)],
            "tau_quantiles": [quantile(tau_cdf, p, tau_lo, tau_hi, "tau") for p in (0.025, 0.5, 0.975)],
        }

    def relative_change(a, b):
        return abs(a - b) / max(abs(a), abs(b), 1e-300)

    def agrees(first, second):
        def relative_change(a, b):
            return abs(a - b) / max(abs(a), abs(b), 1e-300)

        if max(relative_change(first["z"], second["z"]),
               relative_change(first["mu_mean"], second["mu_mean"]),
               relative_change(first["tau_mean"], second["tau_mean"]),
               abs(first["probability"] - second["probability"])) > 1e-8:
            return False
        return all(
            relative_change(a, b) <= 1e-6
            for key in ("mu_quantiles", "tau_quantiles")
            for a, b in zip(first[key], second[key])
        )

    base = tensor_pass(_BULK_NODES, _TAIL_NODES)
    refined = tensor_pass(2 * _BULK_NODES, 2 * _TAIL_NODES)
    nodes = (2 * _BULK_NODES, 2 * _TAIL_NODES)
    # F2-62: a single refinement step was not enough for flat posteriors --
    # regular priors at 10-40x the data SE integrate more slowly and failed
    # here 20/20 times. Keep refining while the successive grids still
    # disagree. The loop is limited to the normal-mu prior: MM11-07's
    # diagnostic contract (neutral message, measured advice) is defined on the
    # Student-t path, and its pinned fixtures must keep their exact outcomes;
    # the flat-posterior rejection reported in F2-62 is a normal-family case.
    max_multiple = _REFINEMENT_MAX_MULTIPLE if not student_mu else 4
    while not agrees(base, refined) and nodes[0] < max_multiple * _BULK_NODES:
        base = refined
        nodes = (2 * nodes[0], 2 * nodes[1])
        refined = tensor_pass(nodes[0], nodes[1])
    if not agrees(base, refined):
        # MM11-07: the old message ("grid refinement did not converge")
        # suggested numerical instability without saying what the user can
        # do. The replacement went through two review rounds on *scope*:
        # naming levers for every student_t failure told users at df=3/5/
        # 10/30/100 to shrink df or switch family even though neither
        # settles there -- advice must be measured, not assumed. See
        # _grid_failure_message: it recommends a setting only after that
        # setting is observed to converge on these same rows.
        if student_mu:
            raise ValueError(_grid_failure_message(
                rows,
                df_mu=df_mu,
                mu_prior_mean=mu_prior_mean,
                mu_prior_scale=mu_prior_scale,
                tau_prior_family=tau_prior_family,
                tau_prior_scale=tau_prior_scale,
                tau_prior_df=tau_prior_df,
                probe=_diagnostic_probe,
            ))
        raise ValueError("grid refinement did not converge")
    convergence = max(
        relative_change(base["z"], refined["z"]),
        relative_change(base["mu_mean"], refined["mu_mean"]),
        relative_change(base["tau_mean"], refined["tau_mean"]),
    )

    def restore(value, name, *, positive=False):
        result = float(value) * unit
        if not math.isfinite(result) or (positive and result <= 0) or (value != 0 and result == 0):
            raise ValueError(f"{name} is outside the finite reporting range")
        return result

    mu_quantiles = [restore(v, "mu posterior quantile") for v in base["mu_quantiles"]]
    tau_quantiles = [restore(v, "tau posterior quantile", positive=True) for v in base["tau_quantiles"]]
    mu = {
        "scale": analysis_scale,
        "mean": restore(base["mu_mean"], "mu posterior mean"),
        "median": mu_quantiles[1],
        "credible_interval_95": [mu_quantiles[0], mu_quantiles[2]],
        "probability_gt_zero": float(base["probability"]),
    }
    tau = {
        "scale": _tau_scale_label(analysis_scale),
        "mean": restore(base["tau_mean"], "tau posterior mean", positive=True),
        "median": tau_quantiles[1],
        "credible_interval_95": [tau_quantiles[0], tau_quantiles[2]],
    }
    if mu_prior["family"] == "normal":
        mu_declaration = {
            "distribution": "normal",
            "mean": float(mu_prior_mean),
            "sd": float(mu_prior_scale),
        }
    else:
        mu_declaration = {
            "distribution": "student_t",
            "mean": float(mu_prior_mean),
            "scale": float(mu_prior_scale),
            "df": float(mu_prior_df),
        }
    if half_t:
        tau_declaration = {
            "distribution": "half_student_t",
            "scale": float(tau_prior_scale),
            "df": float(tau_prior_df),
        }
    else:
        tau_declaration = {"distribution": "half_normal", "scale": float(tau_prior_scale)}
    method = "panel-grid Gauss-Legendre quadrature over log(tau)"
    if student_mu:
        method += " and log(student-t mixing precision)"
    result = {
        "model": "normal_normal_random_effects_robust_priors",
        "measure": measure,
        "analysis_scale": analysis_scale,
        "standard_error_scale": analysis_scale,
        "study_ids": ids,
        "study_count": len(ids),
        "priors": {"mu": mu_declaration, "tau": tau_declaration},
        "mu": mu,
        "tau": tau,
        "integration": {
            "method": method,
            "converged": True,
            "grid_refinement_relative_change": float(convergence),
            "grid_points": [int(nodes[0]), int(nodes[1]) if student_mu else 1],
        },
    }
    if analysis_scale == "log":
        try:
            ratio = [math.exp(value) for value in mu_quantiles]
        except OverflowError as exc:
            raise ValueError("exponentiated posterior interval is outside the finite range") from exc
        if not all(math.isfinite(value) and value > 0 for value in ratio):
            raise ValueError("exponentiated posterior interval is outside the finite range")
        result["ratio_summary"] = {
            "scale": "ratio",
            "median": ratio[1],
            "credible_interval_95": [ratio[0], ratio[2]],
            "probability_gt_one": float(base["probability"]),
        }
    return result


def _panel_integral(function, a, b, nodes=24):
    """Gauss-Legendre integral of a scalar function over a finite panel."""
    abscissae, weights = leggauss(nodes)
    mid = 0.5 * (a + b)
    half = 0.5 * (b - a)
    return float(half * (weights @ np.asarray([function(mid + half * x) for x in abscissae])))


_CELL_KEYS = (
    "mu_prior_family",
    "mu_prior_mean",
    "mu_prior_scale",
    "mu_prior_df",
    "tau_prior_family",
    "tau_prior_scale",
    "tau_prior_df",
)


def _describe_cell(index, spec):
    mu = f"mu ~ {spec['mu_prior_family']}(location {spec['mu_prior_mean']}, scale {spec['mu_prior_scale']}"
    if spec["mu_prior_family"] == "student_t":
        mu += f", df {spec['mu_prior_df']}"
    tau = f"tau ~ {spec['tau_prior_family']}(scale {spec['tau_prior_scale']}"
    if spec["tau_prior_family"] == "half_student_t":
        tau += f", df {spec['tau_prior_df']}"
    return f"{mu}), {tau})"


def prior_sensitivity_grid(rows: Sequence[Mapping], *, priors: Sequence[Mapping]) -> dict:
    """Re-fit the model over an explicitly provided grid of prior cells.

    ``priors`` is a non-empty list of mappings, each supplying every prior
    argument of :func:`fit_bayesian_robust_meta` (``mu_prior_df`` and
    ``tau_prior_df`` included, ``None`` for the non-t families). No cell
    inherits defaults. The returned table lists per-cell posterior summaries
    for ``mu`` and ``tau``, flags the most dispersed cell per outcome, and
    adds a descriptive overview. The overview never selects or recommends a
    prior.
    """
    if not isinstance(priors, (list, tuple)):
        raise ValueError("priors must be a list of prior specification mappings")
    if len(priors) == 0:
        raise ValueError("priors grid must contain at least one prior specification")
    if len(priors) > MAX_PRIOR_GRID_CELLS:
        # Every cell is a full posterior fit; the field used to be unbounded so
        # a single request could queue ~10 minutes of CPU (859 KiB payload =
        # ~5000 cells). The cap is a module-level budget, not a view change.
        raise ValueError(
            f"priors grid must contain at most {MAX_PRIOR_GRID_CELLS} prior "
            f"specifications; {len(priors)} were supplied"
        )
    ids, _, _, measure = _parse_study_rows(rows)
    analysis_scale = _analysis_scale(measure)
    cells = []
    for index, spec in enumerate(priors):
        if not isinstance(spec, Mapping):
            raise ValueError(f"prior specification {index} must be a mapping")
        unknown = sorted(set(spec) - set(_CELL_KEYS))
        missing = sorted(set(_CELL_KEYS) - set(spec))
        if unknown or missing:
            raise ValueError(
                f"prior specification {index} has unknown keys {unknown} and is missing {missing}"
            )
        try:
            result = fit_bayesian_robust_meta(rows, **{key: spec[key] for key in _CELL_KEYS})
        except ValueError as exc:
            raise ValueError(f"prior specification {index}: {exc}") from exc
        cell = {"cell": index, "priors": result["priors"], "mu": result["mu"], "tau": result["tau"]}
        if "ratio_summary" in result:
            cell["ratio_summary"] = result["ratio_summary"]
        cells.append(cell)

    def widths(key):
        return [cell[key]["credible_interval_95"][1] - cell[key]["credible_interval_95"][0]
                for cell in cells]

    def span(values):
        return [min(values), max(values)]

    mu_widths, tau_widths = widths("mu"), widths("tau")
    worst_mu = max(range(len(cells)), key=lambda i: mu_widths[i])
    tightest_mu = min(range(len(cells)), key=lambda i: mu_widths[i])
    worst_tau = max(range(len(cells)), key=lambda i: tau_widths[i])
    probabilities = [cell["mu"]["probability_gt_zero"] for cell in cells]
    direction_consistent = all(p > 0.5 for p in probabilities) or all(p < 0.5 for p in probabilities)
    direction_text = "consistent" if direction_consistent else "not consistent"
    mu_mean_lo, mu_mean_hi = span([cell["mu"]["mean"] for cell in cells])
    tau_mean_lo, tau_mean_hi = span([cell["tau"]["mean"] for cell in cells])
    overview = (
        f"Across {len(cells)} prior specifications the pooled-effect posterior mean ranged from "
        f"{mu_mean_lo:.6g} to {mu_mean_hi:.6g} and its 95% credible interval width from "
        f"{span(mu_widths)[0]:.6g} to {span(mu_widths)[1]:.6g} on the {analysis_scale} scale; "
        f"the most dispersed cell is cell {worst_mu} ({_describe_cell(worst_mu, priors[worst_mu])}). "
        f"The posterior mean of tau ranged from {tau_mean_lo:.6g} to {tau_mean_hi:.6g}, with its "
        f"widest interval in cell {worst_tau}. The direction of the pooled effect (P(mu > 0) above "
        f"or below 0.5) was {direction_text} across the grid. This overview is descriptive only: it "
        f"reports how the summaries move across the provided prior grid and does not select or "
        f"recommend a prior."
    )
    return {
        "model": "bayesian_robust_meta_prior_sensitivity",
        "measure": measure,
        "analysis_scale": analysis_scale,
        "standard_error_scale": analysis_scale,
        "study_ids": ids,
        "study_count": len(ids),
        "cell_count": len(cells),
        "priors": [cell["priors"] for cell in cells],
        "cells": cells,
        "dispersion": {
            "mu": {
                "most_dispersed_cell": worst_mu,
                "most_dispersed_interval_width": mu_widths[worst_mu],
                "tightest_cell": tightest_mu,
                "interval_width_range": span(mu_widths),
                "mean_range": [mu_mean_lo, mu_mean_hi],
                "direction_consistent": direction_consistent,
            },
            "tau": {
                "most_dispersed_cell": worst_tau,
                "most_dispersed_interval_width": tau_widths[worst_tau],
                "interval_width_range": span(tau_widths),
                "mean_range": [tau_mean_lo, tau_mean_hi],
            },
        },
        "influence_overview": overview,
        "note": "Prior-sensitivity output is descriptive only; no prior is selected or recommended.",
    }
