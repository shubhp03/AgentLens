"""The agent loop: the heart of the project.

`AgentRunner.run(question)` is an async generator that yields trace events
(see events.py) while it drives a conversation between an LLM and a set of tools:

    RunStarted
    SpanFinished(llm_call) -> SpanFinished(tool_call) x n -> SpanFinished(llm_call) -> ...
    SpanFinished(agent)    <- the root span, emitted LAST so it can carry the run totals
    RunFinished

How one run proceeds
--------------------
1. Build the tool list: the allow-listed MCP tools + the terminal `submit_answer` tool.
2. Repeat up to `config.max_steps` times ("a step" = one successful LLM response):
   a. Call the LLM. Retryable provider errors (429/5xx/timeouts) are retried up to
      MAX_LLM_ATTEMPTS with exponential backoff + jitter; every attempt is its own span.
   b. Price the call from the pricing table; if cumulative cost exceeds `max_cost_usd`,
      stop with `budget_exceeded`.
   c. If the model called `submit_answer`: validate it against the `Answer` schema.
      Valid -> done. Invalid -> tell the model what was wrong and allow ONE repair attempt.
   d. If the model produced plain text and no tool call: nudge it once to call
      `submit_answer`; if it still doesn't, accept the text as the answer and mark the root
      span `no_submit=True` (scorers treat that as a format error).
   e. Otherwise run the requested tools CONCURRENTLY, truncate oversized outputs, and feed
      the results back as the next message.
3. If the loop runs out of steps -> `step_limit`.

Design rules (all tested in agent/tests/test_loop.py)
-----------------------------------------------------
* Exactly one RunFinished per run; `seq` strictly increases in emission order; span ids are unique.
* Model and tool failures never raise out of the generator: they become spans + a termination reason.
  (The one deliberate exception: a missing price is a configuration error and raises at
  construction time, before any money is spent.)
* The root span gets the highest `seq` because it ends last. Its `span_id` is generated up front so
  every child can point at it via `parent_span_id`. (The DB has no FK on parent_span_id for this reason.)
* Tool access is enforced HERE against the allow-list, not left to the model's good behaviour.
* Cancellation (client disconnect) surfaces as asyncio.CancelledError/GeneratorExit at an await or
  yield; we deliberately do not catch it, so work stops immediately. The gateway records `cancelled`.
* `TerminationReason.tool_failure` is reserved (tool errors are fed back to the model, not fatal).
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from agentlens_agent.answer import SUBMIT_ANSWER_NAME, Answer, submit_answer_spec
from agentlens_agent.config import AgentConfig
from agentlens_agent.cost import PricingTable
from agentlens_agent.events import (
    RunFinished,
    RunStarted,
    RunStatus,
    RunTotals,
    SpanFinished,
    TerminationReason,
    Usage,
    utcnow,
)
from agentlens_agent.llm.base import (
    LLMClient,
    LLMError,
    LLMResponse,
    Message,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from agentlens_agent.registry import ToolOutput, ToolRegistry

MAX_TOOL_CHARS = 12_000
MAX_LLM_ATTEMPTS = 3
BACKOFF_BASE_S = 0.5
NUDGE = (
    "You have not submitted an answer. Call the `submit_answer` tool now with your final answer "
    "(set abstained=true if the question cannot be answered from SEC filings)."
)
_PREVIEW_CHARS = 1_000  # tool-result preview stored in llm_call span input


def truncate_tool_output(text: str, limit: int = MAX_TOOL_CHARS) -> tuple[str, dict[str, Any]]:
    """Cap tool output at `limit` chars with a visible marker. Returns (text, span attributes)."""
    if len(text) <= limit:
        return text, {}
    marker = f"\n[output truncated: showing first {limit} of {len(text)} characters]"
    return text[:limit] + marker, {"truncated": True, "original_size": len(text)}


def _message_to_dict(m: Message) -> dict[str, Any]:
    """Compact, JSON-safe view of a message for span inputs."""
    d: dict[str, Any] = {"role": m.role}
    if m.text:
        d["text"] = m.text
    if m.tool_calls:
        d["tool_calls"] = [
            {"id": c.id, "name": c.name, "arguments": c.arguments} for c in m.tool_calls
        ]
    if m.tool_results:
        d["tool_results"] = [
            {
                "tool_call_id": r.tool_call_id,
                "is_error": r.is_error,
                "content_chars": len(r.content),
                "content": r.content[:_PREVIEW_CHARS],
            }
            for r in m.tool_results
        ]
    return d


class AgentRunner:
    def __init__(
        self,
        *,
        llm: LLMClient,
        tools: ToolRegistry,
        config: AgentConfig,
        system_prompt: str,
        pricing: PricingTable,
        run_id: UUID,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.config = config
        self.system_prompt = system_prompt
        self.pricing = pricing
        self.run_id = run_id
        self._sleep = sleep
        self._rng = rng
        # Fail fast, before any spend: raises MissingPriceError if this model has no price.
        self.pricing.cost_usd(llm.provider, llm.model, Usage())

    def _backoff_s(self, attempt: int) -> float:
        return BACKOFF_BASE_S * (2 ** (attempt - 1)) * (0.5 + self._rng())

    async def _execute(
        self, call: ToolCall, allowlist: set[str]
    ) -> tuple[ToolCall, ToolOutput, Any, Any]:
        started = utcnow()
        if call.name not in allowlist:
            out = ToolOutput(
                f"tool {call.name!r} is not available. Available tools: {sorted(allowlist)}",
                is_error=True,
            )
        else:
            try:
                out = await self.tools.call(call.name, call.arguments)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # registry contract says it shouldn't raise; belt and braces
                out = ToolOutput(f"{type(e).__name__}: {e}", is_error=True)
        return call, out, started, utcnow()

    async def run(self, question: str) -> AsyncIterator[RunStarted | SpanFinished | RunFinished]:
        cfg = self.config
        run_started = utcnow()
        t_run = time.perf_counter()
        root_id = uuid4()
        seq = 0  # last assigned seq; the root span takes the final one
        total_usage = Usage()
        total_cost = 0.0
        steps = 0
        n_tool_calls = 0
        retries = 0
        truncations = 0
        root_attrs: dict[str, Any] = {}

        allowlist = set(cfg.tool_allowlist)
        tool_specs = [*self.tools.specs(cfg.tool_allowlist), ToolSpec(**submit_answer_spec())]
        messages: list[Message] = [Message(role="user", text=question)]
        sent_upto = 0  # messages[:sent_upto] were already shown in an earlier llm_call span
        nudged = False
        repaired = False

        status: RunStatus = "failed"
        reason: TerminationReason = "step_limit"
        answer: Answer | None = None
        error: str | None = None

        yield RunStarted(run_id=self.run_id, started_at=run_started)

        for _ in range(cfg.max_steps):
            # ---- 1. call the LLM (with retries) --------------------------------------
            new_messages = [_message_to_dict(m) for m in messages[sent_upto:]]
            span_input = {
                "n_messages": len(messages),
                "new_messages": new_messages,
                "tools": [t.name for t in tool_specs],
            }
            resp: LLMResponse | None = None
            last_error: LLMError | None = None
            for attempt in range(1, MAX_LLM_ATTEMPTS + 1):
                s_started = utcnow()
                try:
                    resp = await self.llm.complete(
                        system=self.system_prompt,
                        messages=list(messages),
                        tools=tool_specs,
                        temperature=cfg.temperature,
                        max_tokens=cfg.max_output_tokens,
                    )
                except LLMError as e:
                    last_error = e
                    seq += 1
                    yield SpanFinished(
                        run_id=self.run_id,
                        span_id=uuid4(),
                        parent_span_id=root_id,
                        seq=seq,
                        type="llm_call",
                        name=self.llm.model,
                        model=self.llm.model,
                        started_at=s_started,
                        ended_at=utcnow(),
                        status="error",
                        input=span_input,
                        error=str(e),
                        attributes={"attempt": attempt, "retryable": e.retryable},
                    )
                    if e.retryable and attempt < MAX_LLM_ATTEMPTS:
                        retries += 1
                        await self._sleep(self._backoff_s(attempt))
                        continue
                    break
                cost = self.pricing.cost_usd(self.llm.provider, self.llm.model, resp.usage)
                total_usage = total_usage + resp.usage
                total_cost += cost
                steps += 1
                seq += 1
                yield SpanFinished(
                    run_id=self.run_id,
                    span_id=uuid4(),
                    parent_span_id=root_id,
                    seq=seq,
                    type="llm_call",
                    name=self.llm.model,
                    model=self.llm.model,
                    started_at=s_started,
                    ended_at=utcnow(),
                    input=span_input,
                    output={
                        "text": resp.text,
                        "tool_calls": [
                            {"id": c.id, "name": c.name, "arguments": c.arguments}
                            for c in resp.tool_calls
                        ],
                        "stop_reason": resp.stop_reason,
                    },
                    usage=resp.usage,
                    cost_usd=cost,
                    attributes={"attempt": attempt},
                )
                break

            if resp is None:
                reason = "provider_error"
                error = str(last_error)
                break
            sent_upto = len(messages)

            # ---- 2. budget -----------------------------------------------------------
            if cfg.max_cost_usd is not None and total_cost > cfg.max_cost_usd:
                reason = "budget_exceeded"
                error = f"cost ${total_cost:.4f} exceeded the ${cfg.max_cost_usd:.4f} budget"
                break

            calls = resp.tool_calls
            submit = next((c for c in calls if c.name == SUBMIT_ANSWER_NAME), None)

            # ---- 3. the model is trying to finish ------------------------------------
            if submit is not None:
                try:
                    answer = Answer.model_validate(submit.arguments)
                except ValidationError as e:
                    if not repaired:
                        repaired = True
                        messages.append(Message(role="assistant", text=resp.text, tool_calls=calls))
                        messages.append(
                            Message(
                                role="tool",
                                tool_results=tuple(
                                    ToolResult(
                                        c.id,
                                        f"submit_answer arguments were invalid; fix and call it again.\n{str(e)[:1500]}"
                                        if c is submit
                                        else "skipped: fix submit_answer first",
                                        is_error=True,
                                    )
                                    for c in calls
                                ),
                            )
                        )
                        continue
                    answer = Answer(
                        text=(resp.text or json.dumps(submit.arguments, default=str))[:2000]
                    )
                    root_attrs["invalid_answer"] = True
                status, reason = "completed", "answered"
                break

            # ---- 4. plain text, no tool call -----------------------------------------
            if not calls:
                if not nudged:
                    nudged = True
                    messages.append(Message(role="assistant", text=resp.text or "(no response)"))
                    messages.append(Message(role="user", text=NUDGE))
                    continue
                answer = Answer(text=resp.text or "")
                root_attrs["no_submit"] = True
                status, reason = "completed", "answered"
                break

            # ---- 5. run the requested tools concurrently -----------------------------
            messages.append(Message(role="assistant", text=resp.text, tool_calls=calls))
            executed = await asyncio.gather(*(self._execute(c, allowlist) for c in calls))
            results: list[ToolResult] = []
            for call, out, t_start, t_end in executed:
                content, extra = truncate_tool_output(out.content)
                if extra:
                    truncations += 1
                n_tool_calls += 1
                seq += 1
                yield SpanFinished(
                    run_id=self.run_id,
                    span_id=uuid4(),
                    parent_span_id=root_id,
                    seq=seq,
                    type="tool_call",
                    name=call.name,
                    started_at=t_start,
                    ended_at=t_end,
                    status="error" if out.is_error else "ok",
                    input=call.arguments,
                    output={"content": content},
                    error=content[:500] if out.is_error else None,
                    attributes={"tool_call_id": call.id, **extra},
                )
                results.append(ToolResult(call.id, content, out.is_error))
            messages.append(Message(role="tool", tool_results=tuple(results)))
        else:
            error = f"reached the {cfg.max_steps}-step limit without a final answer"

        # ---- wrap up: root span (last), then the terminal event ----------------------
        ended = utcnow()
        totals = RunTotals(
            usage=total_usage,
            cost_usd=total_cost,
            latency_ms=int((time.perf_counter() - t_run) * 1000),
            steps=steps,
            tool_calls=n_tool_calls,
        )
        root_attrs.update(steps=steps, retries=retries, truncations=truncations)
        seq += 1
        yield SpanFinished(
            run_id=self.run_id,
            span_id=root_id,
            parent_span_id=None,
            seq=seq,
            type="agent",
            name="agent",
            started_at=run_started,
            ended_at=ended,
            status="ok" if status == "completed" else "error",
            input={"question": question},
            output={"final_answer": answer.model_dump(mode="json")} if answer else {},
            error=error,
            usage=total_usage,
            cost_usd=total_cost,
            attributes=root_attrs,
        )
        yield RunFinished(
            run_id=self.run_id,
            status=status,
            termination_reason=reason,
            final_answer=answer,
            totals=totals,
            error=error,
            ended_at=ended,
        )
