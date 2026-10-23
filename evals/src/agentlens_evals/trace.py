"""Turn a run's event stream into the facts the scorer and classifier need.

Everything here is derived from the trace (what the tools actually returned), never from the
model's own claims. That is what makes the groundedness and failure checks auditable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from agentlens_agent.events import RunFinished, SpanFinished

_ACCESSION = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?")


def numbers_in_text(text: str) -> list[float]:
    """Numbers appearing in free text. Accession numbers and ISO dates are removed first so they
    cannot masquerade as financial values."""
    text = _DATE.sub(" ", _ACCESSION.sub(" ", text))
    out: list[float] = []
    for m in _NUMBER.findall(text):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            continue
    return out


def _walk(node: Any, numbers: list[float], rows: list[dict[str, Any]]) -> None:
    if isinstance(node, dict):
        if "value" in node and "end" in node and isinstance(node["value"], (int, float)):
            rows.append(node)
        for v in node.values():
            _walk(v, numbers, rows)
    elif isinstance(node, list):
        for v in node:
            _walk(v, numbers, rows)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        numbers.append(float(node))


@dataclass
class TraceFacts:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    call_errors: list[bool] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    numbers: list[float] = field(default_factory=list)  # every number any tool produced
    fact_rows: list[dict[str, Any]] = field(default_factory=list)  # XBRL fact rows seen
    accessions: set[str] = field(default_factory=set)
    no_submit: bool = False
    invalid_answer: bool = False

    @property
    def called_tools(self) -> list[str]:
        return [n for n, _ in self.calls]

    @property
    def last_tool_errored(self) -> bool:
        return bool(self.call_errors) and self.call_errors[-1]


def extract_facts(events: list[Any]) -> TraceFacts:
    t = TraceFacts()
    for ev in events:
        if not isinstance(ev, SpanFinished):
            continue
        if ev.type == "agent":
            t.no_submit = bool(ev.attributes.get("no_submit"))
            t.invalid_answer = bool(ev.attributes.get("invalid_answer"))
        elif ev.type == "tool_call":
            t.calls.append((ev.name, dict(ev.input or {})))
            t.call_errors.append(ev.status == "error")
            content = str((ev.output or {}).get("content", ""))
            t.outputs.append(content)
            t.accessions.update(_ACCESSION.findall(content))
            t.numbers.extend(numbers_in_text(content))
            try:
                parsed = json.loads(content)
            except ValueError:
                continue
            _walk(parsed, t.numbers, t.fact_rows)
    return t


def final_event(events: list[Any]) -> RunFinished:
    last = events[-1]
    if not isinstance(last, RunFinished):
        raise ValueError("event stream did not end with RunFinished")
    return last
