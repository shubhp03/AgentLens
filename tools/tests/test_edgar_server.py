import pytest


def test_server_registers_all_tools():
    import asyncio

    from agentlens_tools.edgar_server import build_server

    names = {t.name for t in asyncio.run(build_server().list_tools())}
    assert names == {
        "resolve_company",
        "list_filings",
        "search_xbrl_concepts",
        "get_financial_facts",
        "get_filing_section",
    }


def test_main_fails_fast_without_user_agent(monkeypatch) -> None:
    import agentlens_tools._run as run_mod
    import agentlens_tools.edgar_server as srv

    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setattr(srv, "_client", None)
    monkeypatch.setattr(run_mod, "run_server", lambda server: pytest.fail("server must not start"))
    with pytest.raises(RuntimeError, match="SEC_USER_AGENT"):
        srv.main()
