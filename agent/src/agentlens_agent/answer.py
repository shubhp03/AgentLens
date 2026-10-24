"""Structured final answer + the terminal `submit_answer` tool.

A terminal tool gives provider-agnostic structured output and an unambiguous "agent is done"
signal (no parsing free text for the answer).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Unit = Literal[
    "USD",
    "USD_thousands",
    "USD_millions",
    "USD_billions",
    "percent",
    "ratio",
    "shares",
    "count",
    "text",
]


class Citation(BaseModel):
    accession: str = Field(description="SEC accession number, e.g. 0000000000-00-000000")
    section: str | None = Field(default=None, description="10-K/10-Q item, e.g. '7' or '1A'")
    concept: str | None = Field(default=None, description="XBRL concept, e.g. 'us-gaap:Revenues'")


class Answer(BaseModel):
    abstained: bool = Field(
        default=False,
        description="True if the question cannot be answered from public SEC filings "
        "(future data, missing company, ambiguous/underspecified).",
    )
    value: float | None = Field(
        default=None, description="Numeric answer, if the question is numeric"
    )
    unit: Unit | None = Field(default=None, description="Unit of `value`")
    period: str | None = Field(
        default=None,
        description="Period LABEL only, nothing else: the company's own fiscal-year label as 'FY' plus the four-digit "
        "year (e.g. 'FY2023'; a fiscal year ending 2024-01-28 that the company calls fiscal 2024 is 'FY2024') or a "
        "quarter as 'Q2 2024'. Never add dates, parentheses or explanation here; put those in `text`.",
    )
    text: str = Field(description="Short explanation or the textual answer")
    citations: list[Citation] = Field(default_factory=list)


SUBMIT_ANSWER_NAME = "submit_answer"


def submit_answer_spec() -> dict[str, Any]:
    return {
        "name": SUBMIT_ANSWER_NAME,
        "description": (
            "Submit your FINAL answer. Call exactly once when done. Cite every filing you relied "
            "on. If the question cannot be answered from SEC filings, set abstained=true and "
            "explain why."
        ),
        "input_schema": Answer.model_json_schema(),
    }
