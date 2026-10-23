from uuid import uuid4

from agentlens_agent.answer import Answer
from agentlens_agent.events import (
    RunFinished,
    RunStarted,
    SpanFinished,
    Usage,
    from_wire,
    to_wire,
    utcnow,
)


def test_wire_roundtrip_all_event_kinds():
    rid, sid = uuid4(), uuid4()
    now = utcnow()
    events = [
        RunStarted(run_id=rid),
        SpanFinished(
            run_id=rid,
            span_id=sid,
            seq=1,
            type="llm_call",
            name="m",
            started_at=now,
            ended_at=now,
            usage=Usage(input_tokens=5, output_tokens=2),
            cost_usd=0.001,
        ),
        RunFinished(
            run_id=rid,
            status="completed",
            termination_reason="answered",
            final_answer=Answer(text="ok", value=1.0, unit="USD"),
        ),
    ]
    for ev in events:
        line = to_wire(ev)
        assert line.endswith("\n") and "\n" not in line[:-1]
        assert from_wire(line) == ev


def test_usage_addition():
    assert Usage(input_tokens=1, output_tokens=2) + Usage(
        input_tokens=3, cache_read_tokens=4
    ) == Usage(input_tokens=4, output_tokens=2, cache_read_tokens=4)
