"""Eval orchestration: dataset x repeats -> results.jsonl (+ one trace file per run).

Direct mode: runs the agent in-process (no gateway, no DB), so the eval harness is usable on its own
and in CI. Properties:

* bounded concurrency (asyncio.Semaphore)
* resumable: finished (case_key, repeat) pairs found in an existing results.jsonl are skipped
* hard budget: stops *starting* runs once spend + in-flight reservations would exceed `--budget-usd`
* a crash inside one run is recorded as a failed result, never kills the whole eval
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentlens_agent.events import RunFinished, to_wire
from pydantic import BaseModel

from agentlens_evals.case import EvalCase
from agentlens_evals.classify import FailureCategory, classify_failure
from agentlens_evals.score import score_run
from agentlens_evals.trace import extract_facts, final_event


class RunRecord(BaseModel):
    """One line of results.jsonl. `case_key`, `repeat`, `passed` are what `compare` reads."""

    case_key: str
    repeat: int
    passed: bool
    run_id: str | None = (
        None  # the agent run (matches traces/<case>__<repeat>.jsonl and the gateway run id)
    )
    failure_category: FailureCategory | None = None
    termination_reason: str
    answer: dict[str, Any] | None = None
    cost_usd: float = 0.0
    latency_ms: int = 0
    steps: int = 0
    tool_calls: int = 0
    called_tools: list[str] = []
    tool_recall: float | None = None
    redundant_rate: float = 0.0
    error_rate: float = 0.0
    citation_validity: float | None = None
    numeric_grounded: bool | None = None
    rel_error: float | None = None
    error: str | None = None


# A run factory turns a question into an async stream of events. (Real: AgentRunner(...).run)
RunFactory = Callable[[str], Any]


def _write_trace(trace_dir: Path, case_key: str, repeat: int, events: list[Any]) -> None:
    trace_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / f"{case_key}__{repeat}.jsonl").write_text("".join(to_wire(e) for e in events))


def _append_line(path: Path, line: str) -> None:
    with path.open("a") as f:
        f.write(line + "\n")


def _crash_record(case: EvalCase, repeat: int, exc: BaseException, started: float) -> RunRecord:
    return RunRecord(
        case_key=case.case_key,
        repeat=repeat,
        passed=False,
        failure_category="provider_error",
        termination_reason="provider_error",
        latency_ms=int((time.perf_counter() - started) * 1000),
        error=f"{type(exc).__name__}: {exc}"[:500],
    )


async def run_one(
    case: EvalCase, repeat: int, factory: RunFactory, trace_dir: Path | None = None
) -> RunRecord:
    started = time.perf_counter()
    events: list[Any] = []
    try:
        async for ev in factory(case.question):
            events.append(ev)
        final: RunFinished = final_event(events)
    except Exception as e:  # noqa: BLE001 - one bad run must not kill the eval
        return _crash_record(case, repeat, e, started)

    facts = extract_facts(events)
    score = score_run(case, final, facts)
    category = classify_failure(case, final, facts, score)
    if trace_dir is not None:
        await asyncio.to_thread(_write_trace, trace_dir, case.case_key, repeat, events)
    ans = final.final_answer
    return RunRecord(
        case_key=case.case_key,
        repeat=repeat,
        run_id=str(final.run_id),
        passed=score.passed,
        failure_category=category,
        termination_reason=final.termination_reason,
        answer=ans.model_dump(mode="json") if ans else None,
        cost_usd=final.totals.cost_usd,
        latency_ms=final.totals.latency_ms,
        steps=final.totals.steps,
        tool_calls=final.totals.tool_calls,
        called_tools=facts.called_tools,
        tool_recall=score.tool_recall,
        redundant_rate=score.redundant_rate,
        error_rate=score.error_rate,
        citation_validity=score.citation_validity,
        numeric_grounded=score.numeric_grounded,
        rel_error=score.numeric.rel_error if score.numeric else None,
        error=final.error,
    )


def load_done(results_path: Path) -> dict[tuple[str, int], RunRecord]:
    done: dict[tuple[str, int], RunRecord] = {}
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            if line.strip():
                r = RunRecord.model_validate_json(line)
                done[(r.case_key, r.repeat)] = r
    return done


@dataclass
class EvalOutcome:
    records: list[RunRecord]
    skipped_for_budget: int
    spent_usd: float


async def run_eval(
    cases: list[EvalCase],
    factory: RunFactory,
    *,
    repeats: int,
    budget_usd: float,
    per_run_cap_usd: float | None,
    results_path: Path,
    trace_dir: Path | None = None,
    concurrency: int = 4,
    on_record: Callable[[RunRecord], Awaitable[None] | None] | None = None,
) -> EvalOutcome:
    """Run every (case, repeat) not already in `results_path`. `per_run_cap_usd` (the agent's own
    max_cost_usd) is what we reserve per in-flight run; with no cap we reserve nothing and rely on
    the post-hoc spend check, so always set a cap on real runs."""
    results_path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        results_path.touch
    )  # a run that starts nothing (budget too small) must still leave a readable, empty file
    done = load_done(results_path)
    spent = sum(r.cost_usd for r in done.values())
    todo = [(c, i) for c in cases for i in range(repeats) if (c.case_key, i) not in done]

    sem = asyncio.Semaphore(concurrency)
    cond = asyncio.Condition()
    in_flight = 0
    skipped = 0
    reserve = per_run_cap_usd or 0.0

    def affordable(n_in_flight: int) -> bool:
        return spent < budget_usd and spent + (n_in_flight + 1) * reserve <= budget_usd

    async def one(case: EvalCase, repeat: int) -> None:
        nonlocal spent, in_flight, skipped
        async with sem:
            async with cond:
                # Wait while in-flight reservations are what block us; skip only when this run would
                # not fit even if nothing else were running (that is real budget exhaustion).
                while not affordable(in_flight) and in_flight > 0:
                    await cond.wait()
                if not affordable(0) or not affordable(in_flight):
                    skipped += 1
                    return
                in_flight += 1
            try:
                rec = await run_one(case, repeat, factory, trace_dir)
            except BaseException:
                async with cond:
                    in_flight -= 1
                    cond.notify_all()
                raise
            async with cond:  # release the reservation and book the real spend atomically
                in_flight -= 1
                spent += rec.cost_usd
                done[(rec.case_key, rec.repeat)] = rec
                await asyncio.to_thread(_append_line, results_path, rec.model_dump_json())
                cond.notify_all()
            if on_record is not None:
                res = on_record(rec)
                if asyncio.iscoroutine(res):
                    await res

    await asyncio.gather(*(one(c, i) for c, i in todo))
    return EvalOutcome(list(done.values()), skipped, spent)


def meta_path(results_path: Path) -> Path:
    return results_path.with_name("meta.json")


def write_meta(results_path: Path, meta: dict[str, Any]) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path(results_path).write_text(json.dumps(meta, indent=2, default=str))
