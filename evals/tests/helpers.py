"""Shared builders: run a scripted agent against fake tools and get a scored RunRecord."""

from __future__ import annotations

import json
from collections.abc import Callable
from uuid import uuid4

from agentlens_agent.answer import SUBMIT_ANSWER_NAME
from agentlens_agent.config import AgentConfig
from agentlens_agent.cost import ModelPrice, PricingTable
from agentlens_agent.events import Usage
from agentlens_agent.llm.base import LLMError, LLMResponse, ToolCall, ToolSpec
from agentlens_agent.llm.fake import ScriptedLLM
from agentlens_agent.loop import AgentRunner
from agentlens_agent.registry import StaticToolRegistry
from agentlens_evals.case import EvalCase, Expected
from agentlens_evals.runner import RunRecord, run_one

USAGE = Usage(input_tokens=100, output_tokens=50)
TOOLS = ["resolve_company", "get_financial_facts", "calculate"]


def fact(end: str, value: float, start: str | None = None, accn: str = "0000320193-23-000106"):
    return {
        "start": start or f"{int(end[:4]) - 1}-10-01",
        "end": end,
        "value": value,
        "unit": "USD",
        "accession": accn,
        "form": "10-K",
    }


FACTS_FY22_FY23 = json.dumps([fact("2022-09-24", 394_328e6), fact("2023-09-30", 383_285e6)])


def case(**kw) -> EvalCase:
    kw.setdefault("case_key", "lookup-test-case")
    kw.setdefault("question", "q?")
    kw.setdefault("category", "lookup")
    kw.setdefault("difficulty", "easy")
    kw.setdefault(
        "expected",
        Expected(kind="numeric", value=383285, unit="USD_millions", period="FY2023"),
    )
    return EvalCase(**kw)


def call(name: str, **args) -> ToolCall:
    return ToolCall(f"id-{name}-{len(args)}", name, args)


def submit(**kw) -> ToolCall:
    kw.setdefault("text", "answer")
    return ToolCall("sub", SUBMIT_ANSWER_NAME, kw)


def resp(*calls: ToolCall, text: str | None = None) -> LLMResponse:
    return LLMResponse(text, tuple(calls), USAGE)


def good_answer(**kw) -> ToolCall:
    base = dict(value=383285.0, unit="USD_millions", period="FY2023")
    base.update(kw)
    return submit(**base)


async def execute(
    c: EvalCase,
    script: list[LLMResponse | LLMError],
    outputs: dict[str, str | Callable[[dict], str]] | None = None,
    *,
    max_steps: int = 6,
    price: float = 1.0,
    max_cost: float | None = None,
) -> RunRecord:
    outputs = outputs or {}

    def handler(name: str):
        async def h(args: dict) -> str:
            out = outputs.get(name, "ok")
            if callable(out):
                return out(args)
            return out

        return h

    reg = StaticToolRegistry({n: (ToolSpec(n, n, {"type": "object"}), handler(n)) for n in TOOLS})
    cfg = AgentConfig(
        name="t",
        provider="fake",
        model="scripted",
        max_steps=max_steps,
        max_cost_usd=max_cost,
        tool_allowlist=TOOLS,
    )
    pricing = PricingTable(
        {"fake:scripted": ModelPrice(input_usd_per_mtok=price, output_usd_per_mtok=price)}
    )

    def factory(question: str):
        return AgentRunner(
            llm=ScriptedLLM(list(script)),
            tools=reg,
            config=cfg,
            system_prompt="s",
            pricing=pricing,
            run_id=uuid4(),
            sleep=_no_sleep,
        ).run(question)

    return await run_one(c, 0, factory)


async def _no_sleep(_: float) -> None:
    return None
