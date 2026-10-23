"""Request models (validation). Response shapes are built by `store.*_out` and checked against
contracts/openapi.yaml by gateway/tests/test_contract.py."""

from __future__ import annotations

from typing import Any, Literal, Self
from uuid import UUID

from agentlens_evals.classify import ALL_CATEGORIES
from pydantic import BaseModel, Field, model_validator


class AgentConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    system_prompt_id: str = Field(min_length=1)
    system_prompt_sha: str = Field(min_length=1)
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_steps: int = Field(default=12, ge=1, le=50)
    max_cost_usd: float | None = Field(default=None, gt=0, le=100_000)
    max_output_tokens: int = Field(default=2048, ge=64, le=200_000)
    tool_allowlist: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class RunCreate(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    config_id: UUID | None = None
    config: AgentConfigCreate | None = None
    source: Literal["interactive", "eval"] | None = None
    eval_run_id: UUID | None = None

    @model_validator(mode="after")
    def _one_config(self) -> Self:
        if (self.config_id is None) == (self.config is None):
            raise ValueError("provide exactly one of config_id or config")
        return self


class EvalCaseIn(BaseModel):
    case_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_\-]{2,63}$")
    question: str = Field(min_length=1)
    category: Literal["lookup", "calc", "text_extract", "multi_hop", "unanswerable"]
    difficulty: Literal["easy", "medium", "hard"]
    expected: dict[str, Any]
    required_tools: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] | None = None
    status: Literal["draft", "verified"] = "draft"
    tags: list[str] = Field(default_factory=list)


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str | None = None
    content_hash: str = Field(min_length=1)
    cases: list[EvalCaseIn] = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def _unique_keys(self) -> Self:
        keys = [c.case_key for c in self.cases]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate case_key in dataset")
        return self


class EvalRunCreate(BaseModel):
    dataset_id: UUID
    config_id: UUID
    label: str = "manual"
    trigger: Literal["manual", "ci", "nightly"] = "manual"
    git_sha: str | None = None
    repeats: int = Field(default=1, ge=1, le=10)


class EvalRunUpdate(BaseModel):
    is_baseline: bool | None = None
    notes: str | None = None


class EvalRunComplete(BaseModel):
    status: Literal["completed", "failed", "cancelled"]
    gate_passed: bool | None = None


class EvalResultCreate(BaseModel):
    case_key: str
    repeat_idx: int = Field(default=0, ge=0)
    run_id: UUID
    passed: bool
    scores: dict[str, Any] = Field(default_factory=dict)
    failure_category: str | None = None
    judge_rationale: str | None = None

    @model_validator(mode="after")
    def _category_matches_outcome(self) -> Self:
        """Mirrors the DB CHECK: every failure is categorised, a pass carries no category."""
        if self.passed and self.failure_category is not None:
            raise ValueError(f"{self.case_key}: a passed result must not have a failure_category")
        if not self.passed:
            if self.failure_category is None:
                raise ValueError(f"{self.case_key}: a failed result needs a failure_category")
            if self.failure_category not in ALL_CATEGORIES:
                raise ValueError(
                    f"{self.case_key}: unknown failure_category {self.failure_category!r}"
                )
        return self
