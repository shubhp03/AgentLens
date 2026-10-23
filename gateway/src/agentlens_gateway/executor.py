"""Run orchestration: queue -> run against the agent service -> persist the trace -> fan out live events.

One asyncio task per run, gated by a semaphore (`max_concurrent_runs`); excess runs wait in status 'queued'.
Terminal states the gateway decides itself: timeout, cancelled, provider_error (agent unreachable/protocol error),
gateway_restart (shutdown or crash recovery). Everything else is reported by the agent's own `run.finished`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from agentlens_agent.events import RunFinished, SpanFinished

from agentlens_gateway.agent_client import AgentClient, AgentError
from agentlens_gateway.bus import RunBus
from agentlens_gateway.store import Store, run_finished_event

log = logging.getLogger("agentlens.gateway.executor")


class RunExecutor:
    def __init__(
        self,
        store: Store,
        agent: AgentClient,
        bus: RunBus,
        *,
        max_concurrent: int,
        run_timeout_s: float,
    ) -> None:
        self.store, self.agent, self.bus = store, agent, bus
        self.run_timeout_s = run_timeout_s
        self._sem = asyncio.Semaphore(max_concurrent)
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._user_cancelled: set[UUID] = set()
        self._closing = False

    def submit(self, run_id: UUID, question: str, config: dict[str, Any]) -> None:
        task = asyncio.create_task(self._run(run_id, question, config), name=f"run-{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(
            lambda _t: (self._tasks.pop(run_id, None), self._user_cancelled.discard(run_id))
        )

    def is_active(self, run_id: UUID) -> bool:
        return run_id in self._tasks

    async def cancel(self, run_id: UUID, wait_s: float = 5.0) -> bool:
        """Cancel a queued or running run. Returns False if this process isn't running it."""
        task = self._tasks.get(run_id)
        if task is None:
            return False
        self._user_cancelled.add(run_id)
        task.cancel()
        await asyncio.wait({task}, timeout=wait_s)
        return True

    async def shutdown(self, wait_s: float = 10.0) -> None:
        """Graceful stop: in-flight runs are ended as failed/gateway_restart (not left 'running' forever)."""
        self._closing = True
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=wait_s)

    # ------------------------------------------------------------------ internals
    async def _run(self, run_id: UUID, question: str, config: dict[str, Any]) -> None:
        try:
            async with self._sem:
                if not await self.store.mark_running(run_id):
                    return  # no longer queued (e.g. cancelled before it started)
                try:
                    async with asyncio.timeout(self.run_timeout_s):
                        await self._consume(run_id, question, config)
                except TimeoutError:
                    await self._fail(
                        run_id,
                        "failed",
                        "timeout",
                        f"run exceeded the {self.run_timeout_s:g}s limit",
                    )
                except AgentError as e:
                    await self._fail(run_id, "failed", "provider_error", str(e))
        except asyncio.CancelledError:
            if run_id in self._user_cancelled or not self._closing:
                await asyncio.shield(
                    self._fail(run_id, "cancelled", "cancelled", "cancelled by request")
                )
            else:
                await asyncio.shield(
                    self._fail(
                        run_id,
                        "failed",
                        "gateway_restart",
                        "gateway shut down while this run was in flight",
                    )
                )
        except Exception as e:  # last resort: a run must always reach a terminal state
            log.exception("run %s crashed inside the gateway", run_id)
            try:
                await self._fail(
                    run_id, "failed", "provider_error", f"gateway error: {type(e).__name__}"
                )
            except Exception:
                log.exception("could not record failure of run %s", run_id)
        finally:
            self.bus.close(run_id)

    async def _consume(self, run_id: UUID, question: str, config: dict[str, Any]) -> None:
        finished = False
        async for ev in self.agent.stream(run_id=run_id, question=question, config=config):
            if ev.run_id != run_id:
                raise AgentError(f"agent sent an event for a different run ({ev.run_id})")
            if isinstance(ev, SpanFinished):
                await self.store.upsert_span(
                    ev
                )  # persist BEFORE publishing: live viewers never see unsaved data
                self.bus.publish(run_id, ev.model_dump(mode="json"))
            elif isinstance(ev, RunFinished):
                if await self.store.finalize_run(ev):
                    self.bus.publish(run_id, ev.model_dump(mode="json"))
                finished = True
                break
            else:  # run.started / span.started: live-only
                self.bus.publish(run_id, ev.model_dump(mode="json"))
        if not finished:
            raise AgentError("agent stream ended before run.finished")

    async def _fail(self, run_id: UUID, status: str, reason: str, error: str | None) -> None:
        row = await self.store.fail_run(run_id, status=status, reason=reason, error=error)
        if row is not None:  # None = already terminal (the agent's own run.finished won the race)
            self.bus.publish(run_id, run_finished_event(row))
