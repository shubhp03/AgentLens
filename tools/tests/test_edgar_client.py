import httpx
import pytest
from agentlens_tools.edgar_client import SecClient


async def test_user_agent_sent_and_cache_hit(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["User-Agent"])
        return httpx.Response(200, json={"ok": True})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    c = SecClient(
        user_agent="Test test@example.com", cache_dir=tmp_path, min_interval_s=0, client=http
    )
    assert await c.get_json("https://data.sec.gov/x") == {"ok": True}
    assert await c.get_json("https://data.sec.gov/x") == {"ok": True}  # served from disk cache
    assert calls == ["Test test@example.com"]


async def test_retries_then_succeeds(tmp_path, monkeypatch):
    n = {"i": 0}

    def handler(request):
        n["i"] += 1
        return httpx.Response(503) if n["i"] < 3 else httpx.Response(200, json={"v": 1})

    async def no_sleep(_):
        return None

    monkeypatch.setattr("agentlens_tools.edgar_client.asyncio.sleep", no_sleep)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    c = SecClient(user_agent="T t@e.com", cache_dir=tmp_path, min_interval_s=0, client=http)
    assert await c.get_json("https://data.sec.gov/y") == {"v": 1}
    assert n["i"] == 3


def test_requires_user_agent(monkeypatch, tmp_path):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(RuntimeError):
        SecClient(cache_dir=tmp_path)
