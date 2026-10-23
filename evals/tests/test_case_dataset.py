import json
from pathlib import Path

import pytest
from agentlens_evals.case import EvalCase, load_dataset

GOLDEN = Path(__file__).resolve().parents[1] / "datasets" / "golden_v0.jsonl"


def test_seed_dataset_loads_and_is_all_draft():
    cases = load_dataset(GOLDEN)
    assert len(cases) == 6 and all(c.status == "draft" for c in cases)
    assert load_dataset(GOLDEN, verified_only=True) == []


def base(**over):
    d = {
        "case_key": "lookup-test-case",
        "question": "q?",
        "category": "lookup",
        "difficulty": "easy",
        "expected": {"kind": "numeric", "value": 1.0, "unit": "USD"},
        "status": "draft",
    }
    d.update(over)
    return d


def test_verified_requires_provenance_and_values():
    with pytest.raises(ValueError):
        EvalCase.model_validate(base(status="verified"))
    ok = base(status="verified", provenance={"source": "10-K", "verified_by": "me"})
    assert EvalCase.model_validate(ok).status == "verified"
    with pytest.raises(ValueError):
        EvalCase.model_validate(
            base(
                status="verified",
                expected={"kind": "numeric"},
                provenance={"source": "s", "verified_by": "me"},
            )
        )


def test_unanswerable_must_abstain_and_vice_versa():
    with pytest.raises(ValueError):
        EvalCase.model_validate(base(category="unanswerable"))
    with pytest.raises(ValueError):
        EvalCase.model_validate(base(expected={"kind": "abstain"}))


def test_duplicate_keys_rejected(tmp_path):
    f = tmp_path / "d.jsonl"
    f.write_text(json.dumps(base()) + "\n" + json.dumps(base()) + "\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_dataset(f)
