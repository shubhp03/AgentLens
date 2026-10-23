import pytest
from agentlens_tools.edgar_parse import (
    extract_section,
    html_to_text,
    match_company,
    normalize_cik,
    parse_recent_filings,
    search_concepts,
    select_facts,
)

TICKERS = {
    "0": {"cik_str": 1, "ticker": "AAA", "title": "Alpha Corp"},
    "1": {"cik_str": 2, "ticker": "BBB", "title": "Beta Alpha Holdings"},
}

FACTS = {
    "facts": {
        "us-gaap": {
            "Revenues": {
                "label": "Revenues",
                "units": {
                    "USD": [
                        # FY2022 annual, first reported in FY2022 10-K
                        {
                            "start": "2021-10-01",
                            "end": "2022-09-24",
                            "val": 100,
                            "accn": "A-22",
                            "fy": 2022,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2022-11-01",
                        },
                        # same period restated in FY2023 10-K (fy=2023 describes the FILING)
                        {
                            "start": "2021-10-01",
                            "end": "2022-09-24",
                            "val": 101,
                            "accn": "A-23",
                            "fy": 2023,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2023-11-01",
                        },
                        {
                            "start": "2022-09-25",
                            "end": "2023-09-30",
                            "val": 120,
                            "accn": "A-23",
                            "fy": 2023,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2023-11-01",
                        },
                        # quarterly fact must not leak into annual selection
                        {
                            "start": "2023-07-02",
                            "end": "2023-09-30",
                            "val": 30,
                            "accn": "A-23",
                            "fy": 2023,
                            "fp": "Q4",
                            "form": "10-K",
                            "filed": "2023-11-01",
                        },
                    ]
                },
            },
            "Assets": {
                "label": "Assets",
                "units": {
                    "USD": [
                        {
                            "end": "2023-09-30",
                            "val": 500,
                            "accn": "A-23",
                            "fy": 2023,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2023-11-01",
                        }
                    ]
                },
            },
        }
    }
}


def test_normalize_cik():
    assert normalize_cik(320193) == "0000320193"
    assert normalize_cik("CIK0000320193") == "0000320193"
    with pytest.raises(ValueError):
        normalize_cik("abc")


def test_match_company_exact_ticker_first():
    out = match_company(TICKERS, "aaa")
    assert out[0]["cik"] == "0000000001"
    names = [m["name"] for m in match_company(TICKERS, "alpha")]
    assert names == ["Alpha Corp", "Beta Alpha Holdings"]


def test_parse_recent_filings_filters_forms_and_limit():
    subs = {
        "filings": {
            "recent": {
                "accessionNumber": ["1", "2", "3"],
                "filingDate": ["d1", "d2", "d3"],
                "reportDate": ["p1", "p2", "p3"],
                "form": ["8-K", "10-K", "10-Q"],
                "primaryDocument": ["a", "b", "c"],
            }
        }
    }
    out = parse_recent_filings(subs, ["10-K", "10-Q"], limit=1)
    assert [f["accession"] for f in out] == ["2"]


def test_select_facts_annual_dedupes_keeping_latest_and_ignores_quarterly():
    rows = select_facts(FACTS, "Revenues", period_type="annual")
    assert [(r["end"], r["value"]) for r in rows] == [("2022-09-24", 101), ("2023-09-30", 120)]
    assert rows[0]["reported_in_fy"] == 2023  # documents the fy-of-filing gotcha


def test_select_facts_quarterly_instant_and_date_window():
    assert [r["value"] for r in select_facts(FACTS, "Revenues", period_type="quarterly")] == [30]
    assert [r["value"] for r in select_facts(FACTS, "Assets", period_type="instant")] == [500]
    rows = select_facts(FACTS, "Revenues", period_type="annual", end_from="2023-01-01")
    assert [r["value"] for r in rows] == [120]


def test_select_facts_unknown_concept_raises_keyerror():
    with pytest.raises(KeyError):
        select_facts(FACTS, "Nope")


def test_search_concepts():
    assert search_concepts(FACTS, "reven")[0]["concept"] == "us-gaap:Revenues"


DOC = """
TABLE OF CONTENTS
Item 1A. Risk Factors 12
Item 1B. Unresolved Staff Comments 20
Item 7. MD&A 21
PART I
Item 1. Business
We make things.
Item 1A. Risk Factors
Risk one. Risk two. Risk three is long and detailed.
Item 1B. Unresolved Staff Comments
None.
Item 7. Management's Discussion
Revenue grew.
Item 7A. Market Risk
Rates.
"""


def test_extract_section_skips_toc_and_stops_at_next_item():
    sec = extract_section(DOC, "1A")
    assert sec.startswith("Item 1A. Risk Factors\nRisk one")
    assert "Unresolved" not in sec
    assert extract_section(DOC, "7").endswith("Revenue grew.")
    assert extract_section(DOC, "1").endswith("We make things.")  # '1' must not match '1A'
    assert extract_section(DOC, "3") is None


def test_html_to_text_strips_scripts_and_ixbrl_header():
    html = "<html><ix:header>hidden</ix:header><script>x()</script><p>Item&nbsp;1.</p><p>Hello</p></html>"
    text = html_to_text(html)
    assert "hidden" not in text and "x()" not in text
    assert "Item 1." in text and "Hello" in text
