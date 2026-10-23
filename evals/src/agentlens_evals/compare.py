"""Paired comparison of two eval runs + the regression gate used by CI."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agentlens_evals.stats import mcnemar_exact, wilson_interval


@dataclass(frozen=True)
class Comparison:
    n: int
    base_pass_rate: float
    cand_pass_rate: float
    base_ci: tuple[float, float]
    cand_ci: tuple[float, float]
    delta: float
    base_only: int  # base passed, candidate failed
    cand_only: int  # candidate passed, base failed
    p_value: float

    def is_regression(self, alpha: float = 0.05) -> bool:
        """Candidate is statistically worse than base (paired, exact McNemar)."""
        return self.cand_only < self.base_only and self.p_value < alpha


def compare_case_outcomes(base: Mapping[str, bool], cand: Mapping[str, bool]) -> Comparison:
    """Inputs map case_key -> passed (case-level: majority of repeats). Uses the shared keys only."""
    keys = sorted(set(base) & set(cand))
    if not keys:
        raise ValueError("no overlapping case_keys between the two runs")
    b = sum(1 for k in keys if base[k] and not cand[k])
    c = sum(1 for k in keys if cand[k] and not base[k])
    bp, cp = sum(base[k] for k in keys), sum(cand[k] for k in keys)
    n = len(keys)
    return Comparison(
        n=n,
        base_pass_rate=bp / n,
        cand_pass_rate=cp / n,
        base_ci=wilson_interval(bp, n),
        cand_ci=wilson_interval(cp, n),
        delta=(cp - bp) / n,
        base_only=b,
        cand_only=c,
        p_value=mcnemar_exact(b, c),
    )


@dataclass(frozen=True)
class GateConfig:
    min_pass_rate: float | None = None
    max_cost_per_task_usd: float | None = None
    alpha: float = 0.05


def gate(
    cmp: Comparison | None, cand_pass_rate: float, cand_cost_per_task_usd: float, cfg: GateConfig
) -> list[str]:
    """Return a list of human-readable gate failures (empty list = pass)."""
    failures: list[str] = []
    if cfg.min_pass_rate is not None and cand_pass_rate < cfg.min_pass_rate:
        failures.append(f"pass rate {cand_pass_rate:.1%} < floor {cfg.min_pass_rate:.1%}")
    if cfg.max_cost_per_task_usd is not None and cand_cost_per_task_usd > cfg.max_cost_per_task_usd:
        failures.append(
            f"cost/task ${cand_cost_per_task_usd:.4f} > cap ${cfg.max_cost_per_task_usd:.4f}"
        )
    if cmp is not None and cmp.is_regression(cfg.alpha):
        failures.append(
            f"statistically significant regression vs baseline "
            f"(-{cmp.base_only - cmp.cand_only} net cases, p={cmp.p_value:.3f})"
        )
    return failures
