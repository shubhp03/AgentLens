"""Golden dataset schema (JSONL, one EvalCase per line). See docs/EVAL_DESIGN.md."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Category = Literal["lookup", "calc", "text_extract", "multi_hop", "unanswerable"]


class Expected(BaseModel):
    kind: Literal["numeric", "text", "abstain"]
    value: float | None = None
    unit: str | None = None  # same vocabulary as Answer.unit
    period: str | None = None  # normalized label, e.g. "FY2023"
    rel_tolerance: float = Field(default=0.005, ge=0)  # 0.5% default
    must_contain: list[str] = Field(default_factory=list)  # for kind="text"


class Provenance(BaseModel):
    source: str = Field(description="Where the gold answer came from (URL / accession / XBRL)")
    accession: str | None = None
    xbrl_concept: str | None = None
    retrieved_at: str | None = None  # ISO date the gold was read from the source
    verified_by: str | None = None  # who hand-checked it against the filing


class EvalCase(BaseModel):
    case_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_\-]{2,63}$")
    question: str
    category: Category
    difficulty: Literal["easy", "medium", "hard"]
    expected: Expected
    required_tools: list[str] = Field(default_factory=list)
    provenance: Provenance | None = None
    status: Literal["draft", "verified"] = "draft"
    split: Literal["dev", "test"] = "dev"  # "test" = held-out: do not look at it while tuning
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _verified_cases_are_complete(self) -> EvalCase:
        if self.status == "verified":
            if self.provenance is None or not self.provenance.verified_by:
                raise ValueError("verified cases need provenance.verified_by")
            if self.expected.kind == "numeric" and (
                self.expected.value is None or self.expected.unit is None
            ):
                raise ValueError("verified numeric cases need expected.value and expected.unit")
            if self.expected.kind == "text" and not self.expected.must_contain:
                raise ValueError("verified text cases need expected.must_contain")
        if (self.category == "unanswerable") != (self.expected.kind == "abstain"):
            raise ValueError("category 'unanswerable' <=> expected.kind 'abstain'")
        return self


def load_dataset(path: str | Path, *, verified_only: bool = False) -> list[EvalCase]:
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for lineno, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            case = EvalCase.model_validate(json.loads(line))
        except Exception as e:
            raise ValueError(f"{path}:{lineno}: {e}") from e
        if case.case_key in seen:
            raise ValueError(f"{path}:{lineno}: duplicate case_key {case.case_key!r}")
        seen.add(case.case_key)
        cases.append(case)
    return [c for c in cases if c.status == "verified"] if verified_only else cases
