"""Pure parsing/selection logic for SEC data (no network): fully unit-testable.

Gotchas this module encodes (and the eval suite should probe):
  * XBRL `fy`/`fp` on a fact describe the FILING the fact appeared in, NOT the fact's own period.
    Select periods by `start`/`end` dates and duration instead.
  * The same period appears in several filings (originals + later comparatives/restatements):
    de-duplicate, keeping the most recently filed value.
  * Fiscal-year labels vary by company (FY ends Jun/Sep/Jan...), so tools expose period-end dates.
  * 10-K HTML has a table of contents that repeats "Item 1A" etc.; pick the longest span.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from typing import Any

from bs4 import BeautifulSoup


def normalize_cik(value: str | int) -> str:
    digits = re.sub(r"\D", "", str(value))
    if not digits:
        raise ValueError(f"not a CIK: {value!r}")
    return digits.zfill(10)


def accession_nodash(accession: str) -> str:
    return accession.replace("-", "")


def match_company(
    tickers: dict[str, dict[str, Any]], query: str, limit: int = 5
) -> list[dict[str, str]]:
    """Exact ticker match first, then case-insensitive substring of the company name."""
    q = query.strip().lower()
    rows = list(tickers.values())
    exact = [r for r in rows if str(r["ticker"]).lower() == q]
    seen = {id(r) for r in exact}
    partial = [r for r in rows if q in str(r["title"]).lower() and id(r) not in seen]
    return [
        {"cik": normalize_cik(r["cik_str"]), "ticker": r["ticker"], "name": r["title"]}
        for r in (exact + partial)[:limit]
    ]


def parse_recent_filings(
    submissions: dict[str, Any], forms: Iterable[str] = ("10-K", "10-Q"), limit: int = 10
) -> list[dict[str, str]]:
    recent = submissions["filings"]["recent"]
    wanted = set(forms)
    out: list[dict[str, str]] = []
    for acc, filed, period, form, doc in zip(
        recent["accessionNumber"],
        recent["filingDate"],
        recent["reportDate"],
        recent["form"],
        recent["primaryDocument"],
        strict=False,
    ):
        if form in wanted:
            out.append(
                {
                    "accession": acc,
                    "form": form,
                    "filed": filed,
                    "period_end": period,
                    "primary_document": doc,
                }
            )
        if len(out) >= limit:
            break
    return out


def search_concepts(
    companyfacts: dict[str, Any], keyword: str, taxonomy: str = "us-gaap", limit: int = 20
) -> list[dict[str, str]]:
    """Find XBRL concepts by keyword in name or label (models routinely guess concept names wrong)."""
    k = keyword.lower()
    out = []
    for name, node in companyfacts.get("facts", {}).get(taxonomy, {}).items():
        label = node.get("label") or ""
        if k in name.lower() or k in label.lower():
            out.append({"concept": f"{taxonomy}:{name}", "label": label})
        if len(out) >= limit:
            break
    return out


def _duration_days(f: dict[str, Any]) -> int | None:
    if "start" not in f:
        return None
    return (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days


_PERIOD_DURATION = {"annual": (350, 380), "quarterly": (80, 100)}


def select_facts(
    companyfacts: dict[str, Any],
    concept: str,
    *,
    taxonomy: str = "us-gaap",
    period_type: str = "annual",
    end_from: str | None = None,
    end_to: str | None = None,
    unit: str | None = None,
) -> list[dict[str, Any]]:
    """Return de-duplicated facts for a concept, ordered by period end.

    period_type: 'annual' (~1y duration), 'quarterly' (~3m duration), 'instant' (balance sheet).
    Raises KeyError if the concept is absent (callers should suggest search_concepts).
    """
    node = companyfacts["facts"][taxonomy][concept]
    units = node["units"]
    chosen = unit or ("USD" if "USD" in units else next(iter(units)))
    lo = date.fromisoformat(end_from) if end_from else None
    hi = date.fromisoformat(end_to) if end_to else None

    best: dict[tuple[str | None, str], dict[str, Any]] = {}
    for f in units[chosen]:
        end = date.fromisoformat(f["end"])
        if (lo and end < lo) or (hi and end > hi):
            continue
        days = _duration_days(f)
        if period_type == "instant":
            if days is not None:
                continue
        else:
            bounds = _PERIOD_DURATION[period_type]
            if days is None or not (bounds[0] <= days <= bounds[1]):
                continue
        key = (f.get("start"), f["end"])
        if key not in best or f["filed"] > best[key]["filed"]:
            best[key] = f

    rows = sorted(best.values(), key=lambda f: f["end"])
    return [
        {
            "start": f.get("start"),
            "end": f["end"],
            "value": f["val"],
            "unit": chosen,
            "accession": f["accn"],
            "form": f["form"],
            "filed": f["filed"],
            "reported_in_fy": f.get("fy"),  # fiscal year of the FILING, not of this period
            "reported_in_fp": f.get("fp"),
        }
        for f in rows
    ]


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "ix:header"]):
        tag.decompose()
    text = soup.get_text("\n").replace("\xa0", " ")
    lines = (re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(ln for ln in lines))


ITEM_ORDER = [
    "1", "1A", "1B", "1C", "2", "3", "4", "5", "6", "7", "7A", "8",
    "9", "9A", "9B", "9C", "10", "11", "12", "13", "14", "15", "16",
]  # fmt: skip
_ANY_ITEM = re.compile(r"^\s*item\s+(\d{1,2}[A-C]?)(?![0-9A-Za-z])", re.IGNORECASE | re.MULTILINE)


def extract_section(text: str, item: str) -> str | None:
    """Extract a 10-K item (e.g. '1A', '7'). Heuristic: of all heading candidates, keep the span
    with the most text (skips the table of contents). Returns None if not found."""
    item = item.upper()
    if item not in ITEM_ORDER:
        raise ValueError(f"unknown item {item!r}; valid: {ITEM_ORDER}")
    idx = ITEM_ORDER.index(item)
    headings = [(m.start(), m.group(1).upper()) for m in _ANY_ITEM.finditer(text)]
    best: tuple[int, int] | None = None
    for pos, (start, found) in enumerate(headings):
        if found != item:
            continue
        end = len(text)
        for nxt_start, nxt in headings[pos + 1 :]:
            if nxt in ITEM_ORDER and ITEM_ORDER.index(nxt) > idx:
                end = nxt_start
                break
        if best is None or (end - start) > (best[1] - best[0]):
            best = (start, end)
    return text[best[0] : best[1]].strip() if best else None
