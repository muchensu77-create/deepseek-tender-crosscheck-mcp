from __future__ import annotations

import pytest

from official_search_mcp.backends.brave import BraveSearchBackend
from official_search_mcp.backends.ddgs import DDGSSearchBackend
from official_search_mcp.backends.factory import create_search_backend
from official_search_mcp.backends.searxng import SearXNGSearchBackend
from official_search_mcp.errors import BackendError


@pytest.mark.asyncio
async def test_searxng_adapter_parses_json_without_network(monkeypatch) -> None:
    async def fake_get_json(url, **kwargs):
        assert url == "http://127.0.0.1:8080/search"
        assert kwargs["params"]["format"] == "json"
        return {
            "results": [
                {
                    "title": "公开结果",
                    "url": "https://www.gov.cn/public",
                    "content": "候选片段",
                }
            ]
        }

    monkeypatch.setattr("official_search_mcp.backends.searxng.get_json", fake_get_json)
    backend = SearXNGSearchBackend("http://127.0.0.1:8080", 5)
    results = await backend.search("query", 3)
    assert results[0].url == "https://www.gov.cn/public"
    assert results[0].snippet == "候选片段"


@pytest.mark.asyncio
async def test_brave_adapter_uses_header_but_does_not_return_key(monkeypatch) -> None:
    credential = "test" + "-credential"

    async def fake_get_json(url, **kwargs):
        assert url.startswith("https://api.search.brave.com/")
        assert kwargs["headers"]["X-Subscription-Token"] == credential
        assert kwargs["params"]["count"] == 20
        return {
            "web": {
                "results": [
                    {
                        "title": "公开结果",
                        "url": "https://www.gov.cn/public",
                        "description": "候选片段",
                    }
                ]
            }
        }

    monkeypatch.setattr("official_search_mcp.backends.brave.get_json", fake_get_json)
    backend = BraveSearchBackend(credential, 5)
    results = await backend.search("query", 40)
    assert results[0].url == "https://www.gov.cn/public"
    assert credential not in repr(results)


@pytest.mark.asyncio
async def test_brave_rejects_query_beyond_upstream_limit() -> None:
    backend = BraveSearchBackend("test-credential", 5)
    with pytest.raises(BackendError, match="400"):
        await backend.search("x" * 401, 3)


def test_searxng_normalizes_operator_url_and_rejects_query() -> None:
    backend = SearXNGSearchBackend(" https://searx.example/prefix ", 5)
    assert backend.endpoint == "https://searx.example/prefix/search"
    with pytest.raises(BackendError):
        SearXNGSearchBackend("https://searx.example/?token=not-allowed", 5)


def test_auto_backend_precedence_and_public_config(settings_factory) -> None:
    searxng_settings = settings_factory(
        backend="auto",
        searxng_url="http://127.0.0.1:8080",
        brave_api_key="ignored" + "-credential",
    )
    assert create_search_backend(searxng_settings).name == "searxng"

    brave_settings = settings_factory(
        backend="auto",
        searxng_url=None,
        brave_api_key="test" + "-credential",
    )
    assert create_search_backend(brave_settings).name == "brave_search_api"
    summary = brave_settings.public_summary()
    assert summary["brave_api_key_configured"] is True
    assert "credential" not in repr(summary)

    ddgs_settings = settings_factory(
        backend="auto",
        searxng_url=None,
        brave_api_key=None,
        ddgs_backends="yahoo,duckduckgo,yandex",
    )
    assert create_search_backend(ddgs_settings).name == "ddgs:yahoo,duckduckgo,yandex"


@pytest.mark.asyncio
async def test_ddgs_sequential_fallback_preserves_actual_connector(monkeypatch) -> None:
    backend = DDGSSearchBackend(5, "yahoo,duckduckgo")
    calls: list[str] = []

    async def fake_connector(connector, query, max_results, timeout_seconds):
        del query, max_results, timeout_seconds
        calls.append(connector)
        if connector == "yahoo":
            raise RuntimeError("offline simulated failure")
        return [
            {
                "title": "公开结果",
                "href": "https://www.gov.cn/public",
                "body": "候选片段",
            }
        ]

    monkeypatch.setattr(backend, "_search_connector", fake_connector)
    results = await backend.search("公开查询", 3)
    assert calls == ["yahoo", "duckduckgo"]
    assert results[0].source_backend == "ddgs:duckduckgo"
