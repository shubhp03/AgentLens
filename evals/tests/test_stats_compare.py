import pytest
from agentlens_evals.compare import GateConfig, compare_case_outcomes, gate
from agentlens_evals.stats import mcnemar_exact, wilson_interval


def test_wilson_known_values():
    lo, hi = wilson_interval(8, 10)
    assert lo == pytest.approx(0.490, abs=0.005) and hi == pytest.approx(0.943, abs=0.005)
    assert wilson_interval(0, 0) == (0.0, 1.0)
    lo, hi = wilson_interval(10, 10)
    assert hi == 1.0 and lo < 1.0


def test_mcnemar_exact():
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(5, 5) == 1.0
    assert mcnemar_exact(0, 10) == pytest.approx(2 / 1024)
    assert mcnemar_exact(10, 0) == mcnemar_exact(0, 10)


def test_regression_requires_significance():
    keys = [f"c{i}" for i in range(40)]
    base = dict.fromkeys(keys, True)
    cand = {k: (i >= 12) for i, k in enumerate(keys)}  # 12 cases newly fail
    cmp = compare_case_outcomes(base, cand)
    assert cmp.base_only == 12 and cmp.cand_only == 0 and cmp.is_regression()
    cand_noise = {k: (i >= 2) for i, k in enumerate(keys)}  # only 2 fail: not significant
    assert not compare_case_outcomes(base, cand_noise).is_regression()


def test_compare_needs_overlap():
    with pytest.raises(ValueError):
        compare_case_outcomes({"a": True}, {"b": True})


def test_gate_messages():
    cfg = GateConfig(min_pass_rate=0.8, max_cost_per_task_usd=0.10)
    assert gate(None, 0.9, 0.05, cfg) == []
    assert len(gate(None, 0.7, 0.2, cfg)) == 2
