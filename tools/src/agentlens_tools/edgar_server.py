"""EDGAR MCP server (mcp 2.x). Read-only by design: the agent has no side-effecting tools.

Filing text is UNTRUSTED (could contain prompt-injection). It is returned under the key
`untrusted_text`, and the agent's system prompt instructs the model to treat it as data.
"""

from __future__ import annotations

import json
from typing import Any

from agentlens_tools.edgar_client import SecClient
from agentlens_tools.edgar_parse import (
    extract_section,
    html_to_text,
    match_company,
    parse_recent_filings,
    search_concepts,
    select_facts,
)

_client: SecClient | None = None


def client() -> SecClient:
    global _client
    if _client is None:
        _client = SecClient()
    return _client


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


async def resolve_company(query: str) -> str:
    """Find a company's CIK from a ticker (exact) or a name fragment. Returns up to 5 matches."""
    matches = match_company(await client().tickers(), query)
    if not matches:
        raise ValueError(f"no company matches {query!r}")
    return _dump(matches)


async def list_filings(cik: str, forms: str = "10-K,10-Q", limit: int = 8) -> str:
    """List recent filings (accession, form, filing date, period end, primary document)."""
    subs = await client().submissions(cik)
    return _dump(parse_recent_filings(subs, [f.strip() for f in forms.split(",")], min(limit, 25)))


async def search_xbrl_concepts(cik: str, keyword: str, taxonomy: str = "us-gaap") -> str:
    """Search a company's XBRL concept names/labels by keyword (e.g. 'revenue', 'operating income')."""
    found = search_concepts(await client().company_facts(cik), keyword, taxonomy)
    if not found:
        raise ValueError(f"no {taxonomy} concept matches {keyword!r}")
    return _dump(found)


async def get_financial_facts(
    cik: str,
    concept: str,
    period_type: str = "annual",
    end_from: str | None = None,
    end_to: str | None = None,
    taxonomy: str = "us-gaap",
) -> str:
    """Get reported values for an XBRL concept, de-duplicated, ordered by period end.

    concept: bare name, e.g. 'NetIncomeLoss'. period_type: annual | quarterly | instant.
    end_from/end_to: ISO dates bounding the period END date. Fiscal-year labels differ by
    company, so always check each fact's `start`/`end` dates; `reported_in_fy` is the fiscal
    year of the FILING, not of the value's period.
    """
    if period_type not in ("annual", "quarterly", "instant"):
        raise ValueError("period_type must be annual | quarterly | instant")
    facts = await client().company_facts(cik)
    try:
        rows = select_facts(
            facts,
            concept.split(":")[-1],
            taxonomy=taxonomy,
            period_type=period_type,
            end_from=end_from,
            end_to=end_to,
        )
    except KeyError:
        raise ValueError(
            f"concept {concept!r} not found for this company; use search_xbrl_concepts to discover names"
        ) from None
    return _dump(rows)


async def get_filing_section(
    cik: str,
    accession: str,
    item: str,
    primary_document: str,
    offset: int = 0,
    max_chars: int = 6000,
) -> str:
    """Read a 10-K item (e.g. '1A' risk factors, '7' MD&A) as plain text, paged by offset."""
    html = await client().filing_html(cik, accession, primary_document)
    section = extract_section(html_to_text(html), item)
    if section is None:
        raise ValueError(f"item {item!r} not found in {accession}")
    max_chars = min(max_chars, 12_000)
    chunk = section[offset : offset + max_chars]
    nxt = offset + max_chars
    return _dump(
        {
            "accession": accession,
            "item": item.upper(),
            "total_chars": len(section),
            "offset": offset,
            "next_offset": nxt if nxt < len(section) else None,
            "untrusted_text": chunk,
        }
    )


def build_server():
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer("agentlens-edgar")
    for fn in (
        resolve_company,
        list_filings,
        search_xbrl_concepts,
        get_financial_facts,
        get_filing_section,
    ):
        mcp.tool()(fn)
    return mcp


def main() -> None:
    from agentlens_tools._run import run_server

    client()  # fail fast at startup if SEC_USER_AGENT is missing, not on the first tool call mid-eval
    run_server(build_server())


if __name__ == "__main__":
    main()
