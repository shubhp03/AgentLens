"""Event protocol: agent service -> gateway (NDJSON, one event per line).

Source of truth for the wire format. `scripts/export_schemas.py` exports it to
`contracts/agent_events.schema.json`; the gateway's DTOs must match that schema.
Every event carries a `kind` discriminator.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter

from agentlens_agent.answer import Answer

SpanType = Literal["agent", "llm_call", "tool_call"]
SpanStatus = Literal["ok", "error"]
RunStatus = Literal["completed", "failed", "cancelled"]
TerminationReason = Literal[
    "answered",
    "step_limit",
    "budget_exceeded",
    "tool_failure",
    "provider_error",
    "timeout",
    "cancelled",
]


def utcnow() -> datetime:
    return datetime.now(UTC)


class Usage(BaseModel):
    """Token usage. `input_tokens` EXCLUDES cache-read tokens (those are priced separately)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
        )


class RunTotals(BaseModel):
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float = 0.0
    latency_ms: int = 0
    steps: int = 0
    tool_calls: int = 0


class RunStarted(BaseModel):
    kind: Literal["run.started"] = "run.started"
    run_id: UUID
    started_at: datetime = Field(default_factory=utcnow)


class _SpanBase(BaseModel):
    run_id: UUID
    span_id: UUID  # client-generated so the gateway can upsert idempotently
    parent_span_id: UUID | None = None
    seq: int = Field(ge=0, description="Monotonic per run; defines display order")
    type: SpanType
    name: str  # model id for llm_call, tool name for tool_call, 'agent' for the root span


class SpanStarted(_SpanBase):
    """Optional (P1): lets the dashboard show in-flight tool calls."""

    kind: Literal["span.started"] = "span.started"
    started_at: datetime = Field(default_factory=utcnow)


class SpanFinished(_SpanBase):
    kind: Literal["span.finished"] = "span.finished"
    started_at: datetime
    ended_at: datetime
    status: SpanStatus = "ok"
    model: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    usage: Usage | None = None
    cost_usd: float | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)  # attempt, truncated, original_size


class RunFinished(BaseModel):
    kind: Literal["run.finished"] = "run.finished"
    run_id: UUID
    status: RunStatus
    termination_reason: TerminationReason
    final_answer: Answer | None = None
    totals: RunTotals = Field(default_factory=RunTotals)
    error: str | None = None
    ended_at: datetime = Field(default_factory=utcnow)


Event = Annotated[
    RunStarted | SpanStarted | SpanFinished | RunFinished,
    Field(discriminator="kind"),
]
EventAdapter: TypeAdapter[Event] = TypeAdapter(Event)


def to_wire(ev: RunStarted | SpanStarted | SpanFinished | RunFinished) -> str:
    """One NDJSON line (newline-terminated)."""
    return ev.model_dump_json() + "\n"


def from_wire(line: str) -> RunStarted | SpanStarted | SpanFinished | RunFinished:
    return EventAdapter.validate_json(line)
