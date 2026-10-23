"""Executable spec for AgentRunner.run (requirements AG-01..AG-08)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from agentlens_agent.answer import SUBMIT_ANSWER_NAME
from agentlens_agent.config import AgentConfig
from agentlens_agent.cost import ModelPrice, PricingTable
from agentlens_agent.events import RunFinished, SpanFinished, Usage
from agentlens_agent.llm.base import LLMResponse, ToolCall, ToolSpec
from agentlens_agent.llm.fake import ScriptedLLM
from agentlens_agent.loop import AgentRunner
from agentlens_agent.registry import StaticToolRegistry

USAGE = Usage(input_tokens=100, output_tokens=50)
CALC = ToolSpec(
    "calculate", "calc", {"type": "object", "properties": {"expression": {"type": "string"}}}
)


def runner(script, *, max_steps=5, max_cost=None, tools=None):
    async def calc(args):
        return str(eval(args["expression"], {"__builtins__": {}}))  # test-only

    cfg = AgentConfig(
        name="t",
        provider="fake",
        model="scripted",
        max_steps=max_steps,
        max_cost_usd=max_cost,
        tool_allowlist=["calculate"],
    )
    pricing = PricingTable(
        {"fake:scripted": ModelPrice(input_usd_per_mtok=1000.0, output_usd_per_mtok=1000.0)}
    )
    return AgentRunner(
        llm=ScriptedLLM(script),
        tools=tools or StaticToolRegistry({"calculate": (CALC, calc)}),
        config=cfg,
        system_prompt="sys",
        pricing=pricing,
        run_id=uuid4(),
    )


def answer_call(**kw):
    kw.setdefault("text", "done")
    return ToolCall("a1", SUBMIT_ANSWER_NAME, kw)


async def collect(r, q="what is 2+2?"):
    return [ev async for ev in r.run(q)]


def assert_invariants(events):
    finals = [e for e in events if isinstance(e, RunFinished)]
    assert len(finals) == 1 and events[-1] is finals[0]
    spans = [e for e in events if isinstance(e, SpanFinished)]
    seqs = [s.seq for s in spans]
    assert len({s.span_id for s in spans}) == len(spans)
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


async def test_direct_answer():
    ev = await collect(runner([LLMResponse(None, (answer_call(value=4.0, unit="count"),), USAGE)]))
    assert_invariants(ev)
    fin = ev[-1]
    assert fin.termination_reason == "answered" and fin.final_answer.value == 4.0
    assert fin.totals.cost_usd > 0 and fin.totals.steps == 1


async def test_tool_then_answer():
    script = [
        LLMResponse(None, (ToolCall("c1", "calculate", {"expression": "2+2"}),), USAGE),
        LLMResponse(None, (answer_call(value=4.0, unit="count"),), USAGE),
    ]
    ev = await collect(runner(script))
    assert_invariants(ev)
    assert [s.type for s in ev if isinstance(s, SpanFinished) and s.type != "agent"] == [
        "llm_call",
        "tool_call",
        "llm_call",
    ]
    assert ev[-1].totals.tool_calls == 1


async def test_step_limit():
    loop_forever = [
        LLMResponse(None, (ToolCall(f"c{i}", "calculate", {"expression": "1+1"}),), USAGE)
        for i in range(3)
    ]
    ev = await collect(runner(loop_forever, max_steps=3))
    assert_invariants(ev)
    assert ev[-1].status == "failed" and ev[-1].termination_reason == "step_limit"


async def test_budget_exceeded():
    # 150 tokens * $1000/Mtok = $0.15 per call; budget $0.20 => second call blows it
    script = [
        LLMResponse(None, (ToolCall(f"c{i}", "calculate", {"expression": "1"}),), USAGE)
        for i in range(5)
    ]
    ev = await collect(runner(script, max_cost=0.20))
    assert_invariants(ev)
    assert ev[-1].termination_reason == "budget_exceeded"


async def test_tool_error_is_fed_back_not_fatal():
    script = [
        LLMResponse(None, (ToolCall("c1", "calculate", {"expression": "1/0"}),), USAGE),
        LLMResponse(None, (answer_call(abstained=True, text="could not compute"),), USAGE),
    ]
    ev = await collect(runner(script))
    assert_invariants(ev)
    tool_span = next(e for e in ev if isinstance(e, SpanFinished) and e.type == "tool_call")
    assert tool_span.status == "error"
    assert ev[-1].termination_reason == "answered"


# ---- behaviours beyond the five core spec tests --------------------------------------------


def test_missing_price_fails_at_construction():
    from agentlens_agent.cost import MissingPriceError

    with pytest.raises(MissingPriceError):
        AgentRunner(
            llm=ScriptedLLM([]),
            tools=StaticToolRegistry({}),
            config=AgentConfig(name="t", provider="fake", model="scripted", tool_allowlist=[]),
            system_prompt="s",
            pricing=PricingTable({}),
            run_id=uuid4(),
        )


async def test_root_span_is_last_and_children_point_at_it():
    script = [
        LLMResponse(None, (ToolCall("c1", "calculate", {"expression": "1+1"}),), USAGE),
        LLMResponse(None, (answer_call(value=2.0, unit="count"),), USAGE),
    ]
    ev = await collect(runner(script))
    spans = [e for e in ev if isinstance(e, SpanFinished)]
    root = spans[-1]
    assert root.type == "agent" and root.parent_span_id is None
    assert all(s.parent_span_id == root.span_id for s in spans[:-1])
    assert root.cost_usd == pytest.approx(ev[-1].totals.cost_usd)


async def test_retryable_llm_error_is_retried_with_backoff():
    from agentlens_agent.llm.base import LLMError

    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    r = runner(
        [
            LLMError("429", retryable=True),
            LLMResponse(None, (answer_call(value=1.0, unit="count"),), USAGE),
        ]
    )
    r._sleep, r._rng = fake_sleep, lambda: 0.5
    ev = await collect(r)
    assert_invariants(ev)
    llm_spans = [e for e in ev if isinstance(e, SpanFinished) and e.type == "llm_call"]
    assert [s.status for s in llm_spans] == ["error", "ok"]
    assert slept == [0.5]  # BACKOFF_BASE_S * 2**0 * (0.5 + 0.5)
    assert ev[-1].termination_reason == "answered"
    assert ev[-1].totals.steps == 1  # failed attempts are not steps


async def test_non_retryable_error_ends_run_as_provider_error():
    from agentlens_agent.llm.base import LLMError

    ev = await collect(runner([LLMError("bad key", retryable=False)]))
    assert_invariants(ev)
    assert ev[-1].status == "failed" and ev[-1].termination_reason == "provider_error"
    assert "bad key" in ev[-1].error


async def test_retries_exhausted():
    from agentlens_agent.llm.base import LLMError

    r = runner([LLMError("503", retryable=True)] * 3)

    async def no_sleep(_):
        pass

    r._sleep = no_sleep
    ev = await collect(r)
    assert ev[-1].termination_reason == "provider_error"
    assert len([e for e in ev if isinstance(e, SpanFinished) and e.type == "llm_call"]) == 3


async def test_oversized_tool_output_is_truncated_and_flagged():
    from agentlens_agent.loop import MAX_TOOL_CHARS

    async def big(_):
        return "x" * (MAX_TOOL_CHARS * 2)

    tools = StaticToolRegistry({"calculate": (CALC, big)})
    script = [
        LLMResponse(None, (ToolCall("c1", "calculate", {}),), USAGE),
        LLMResponse(None, (answer_call(value=1.0, unit="count"),), USAGE),
    ]
    r = runner(script, tools=tools)
    ev = await collect(r)
    tool_span = next(e for e in ev if isinstance(e, SpanFinished) and e.type == "tool_call")
    assert tool_span.attributes["truncated"] is True
    assert tool_span.attributes["original_size"] == MAX_TOOL_CHARS * 2
    sent = r.llm.calls[1]["messages"][-1].tool_results[0].content
    assert len(sent) < MAX_TOOL_CHARS + 200 and "truncated" in sent


async def test_tool_outside_allowlist_is_refused():
    script = [
        LLMResponse(None, (ToolCall("c1", "rm_rf", {}),), USAGE),
        LLMResponse(None, (answer_call(abstained=True),), USAGE),
    ]
    ev = await collect(runner(script))
    t = next(e for e in ev if isinstance(e, SpanFinished) and e.type == "tool_call")
    assert t.status == "error" and "not available" in t.error


async def test_parallel_tool_calls_run_concurrently():
    import asyncio

    active = peak = 0

    async def slow(_):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return "ok"

    tools = StaticToolRegistry({"calculate": (CALC, slow)})
    script = [
        LLMResponse(
            None,
            (ToolCall("c1", "calculate", {}), ToolCall("c2", "calculate", {})),
            USAGE,
        ),
        LLMResponse(None, (answer_call(abstained=True),), USAGE),
    ]
    ev = await collect(runner(script, tools=tools))
    assert peak == 2
    seqs = [e.seq for e in ev if isinstance(e, SpanFinished) and e.type == "tool_call"]
    assert seqs == sorted(seqs)  # emission order is deterministic: call order, not finish order


async def test_invalid_submit_gets_one_repair_then_succeeds():
    bad = ToolCall("a1", SUBMIT_ANSWER_NAME, {"value": "not a number", "unit": "bogus"})
    script = [
        LLMResponse(None, (bad,), USAGE),
        LLMResponse(None, (answer_call(value=4.0, unit="count"),), USAGE),
    ]
    r = runner(script)
    ev = await collect(r)
    assert ev[-1].termination_reason == "answered" and ev[-1].final_answer.value == 4.0
    assert r.llm.calls[1]["messages"][-1].tool_results[0].is_error


async def test_invalid_submit_twice_is_accepted_but_flagged():
    bad = ToolCall("a1", SUBMIT_ANSWER_NAME, {"value": "nope", "unit": "bogus"})
    ev = await collect(runner([LLMResponse(None, (bad,), USAGE), LLMResponse(None, (bad,), USAGE)]))
    root = [e for e in ev if isinstance(e, SpanFinished)][-1]
    assert root.attributes["invalid_answer"] is True
    assert ev[-1].termination_reason == "answered"


async def test_plain_text_gets_one_nudge():
    script = [
        LLMResponse("it is 4", (), USAGE),
        LLMResponse(None, (answer_call(value=4.0, unit="count"),), USAGE),
    ]
    r = runner(script)
    ev = await collect(r)
    assert ev[-1].final_answer.value == 4.0
    assert "submit_answer" in r.llm.calls[1]["messages"][-1].text


async def test_plain_text_twice_is_accepted_with_no_submit_flag():
    ev = await collect(runner([LLMResponse("four", (), USAGE), LLMResponse("four!", (), USAGE)]))
    root = [e for e in ev if isinstance(e, SpanFinished)][-1]
    assert root.attributes["no_submit"] is True
    assert ev[-1].final_answer.text == "four!"


async def test_llm_span_inputs_are_deltas_not_full_history():
    script = [
        LLMResponse(None, (ToolCall("c1", "calculate", {"expression": "1+1"}),), USAGE),
        LLMResponse(None, (answer_call(value=2.0, unit="count"),), USAGE),
    ]
    ev = await collect(runner(script))
    llm = [e for e in ev if isinstance(e, SpanFinished) and e.type == "llm_call"]
    assert len(llm[0].input["new_messages"]) == 1  # just the question
    assert [m["role"] for m in llm[1].input["new_messages"]] == ["assistant", "tool"]
