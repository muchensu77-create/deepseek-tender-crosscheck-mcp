from __future__ import annotations

import aiohttp
import pytest

from official_search_mcp.errors import OfficialSearchError, PolicyError
from official_search_mcp.fetcher import (
    ControlledFetcher,
    DownloadedResponse,
    RobotsDecision,
    _read_limited_body,
)
from official_search_mcp.limits import RequestGate
from official_search_mcp.security import DomainPolicy, normalize_public_url


def _fetcher(settings_factory) -> ControlledFetcher:
    settings = settings_factory()
    policy = DomainPolicy(
        official_suffixes=settings.official_suffixes,
        allowed_domains=settings.allowed_domains,
        default_domains=settings.default_domains,
    )
    gate = RequestGate(
        max_concurrency=2,
        requests_per_minute=120,
        minimum_domain_interval_seconds=0,
    )
    return ControlledFetcher(settings, policy, gate)


def test_redirect_rejects_https_downgrade_and_cross_domain(settings_factory) -> None:
    fetcher = _fetcher(settings_factory)
    with pytest.raises(PolicyError, match="HTTPS"):
        fetcher._next_redirect("https://www.gov.cn/a", "http://www.gov.cn/b")
    with pytest.raises(PolicyError):
        fetcher._next_redirect("https://www.gov.cn/a", "https://evil.example/b")
    with pytest.raises(PolicyError):
        fetcher._next_redirect(
            "https://www.gov.cn/a",
            "https://other.gov.cn/b",
            restrict_domains=("www.gov.cn",),
        )


class _FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def iter_chunked(self, _size: int):
        for chunk in self.chunks:
            yield chunk


class _FakeResponse:
    def __init__(self, chunks: list[bytes], content_length: int | None = None) -> None:
        self.content = _FakeContent(chunks)
        self.content_length = content_length


@pytest.mark.asyncio
async def test_streaming_body_limit_checks_header_and_actual_bytes() -> None:
    with pytest.raises(OfficialSearchError) as header_error:
        await _read_limited_body(_FakeResponse([], content_length=100), 10)
    assert header_error.value.code == "response_too_large"

    with pytest.raises(OfficialSearchError) as chunk_error:
        await _read_limited_body(_FakeResponse([b"1234", b"5678"]), 6)
    assert chunk_error.value.code == "response_too_large"

    assert await _read_limited_body(_FakeResponse([b"123", b"456"]), 6) == b"123456"


@pytest.mark.asyncio
async def test_robots_404_allows_but_disallow_blocks(settings_factory, monkeypatch) -> None:
    fetcher = _fetcher(settings_factory)

    async def missing(*args, **kwargs):
        del args, kwargs
        return DownloadedResponse(
            status=404,
            final_url="https://www.gov.cn/robots.txt",
            redirect_chain=("https://www.gov.cn/robots.txt",),
            content_type="text/plain",
            charset="utf-8",
            body=b"",
        )

    monkeypatch.setattr(fetcher, "_download", missing)
    assert (await fetcher._robots_decision("https://www.gov.cn/public")).allowed

    fetcher._robots_cache.clear()

    calls = 0

    async def disallow(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        return DownloadedResponse(
            status=200,
            final_url="https://www.gov.cn/robots.txt",
            redirect_chain=("https://www.gov.cn/robots.txt",),
            content_type="text/plain",
            charset="utf-8",
            body=(
                b"User-agent: *\n"
                b"Allow: /public/\n"
                b"Disallow: /public/private/\n"
                b"Disallow: /secret\n"
                b"Disallow: /downloads/*.pdf$\n"
                b"Disallow: /a?x=~\n"
                b"Request-rate: 120/1m\n"
            ),
        )

    monkeypatch.setattr(fetcher, "_download", disallow)
    public = await fetcher._robots_decision("https://www.gov.cn/public/page")
    assert public.allowed
    assert public.crawl_delay == pytest.approx(0.5)
    assert not (await fetcher._robots_decision("https://www.gov.cn/public/private/a")).allowed
    assert not (await fetcher._robots_decision("https://www.gov.cn/secret/a")).allowed
    assert not (await fetcher._robots_decision("https://www.gov.cn/downloads/a.pdf")).allowed
    dot_url = normalize_public_url("https://www.gov.cn/public/../secret/a", fetcher.policy)
    encoded_dot_url = normalize_public_url(
        "https://www.gov.cn/public/%2e%2e/secret/a", fetcher.policy
    )
    encoded_query_url = normalize_public_url("https://www.gov.cn/a?x=%7e", fetcher.policy)
    assert dot_url == encoded_dot_url == "https://www.gov.cn/secret/a"
    assert not (await fetcher._robots_decision(dot_url)).allowed
    assert not (await fetcher._robots_decision(encoded_query_url)).allowed
    assert calls == 1


@pytest.mark.asyncio
async def test_robots_visit_time_fails_closed(settings_factory, monkeypatch) -> None:
    fetcher = _fetcher(settings_factory)

    async def visit_time(*args, **kwargs):
        del args, kwargs
        return DownloadedResponse(
            status=200,
            final_url="https://www.gov.cn/robots.txt",
            redirect_chain=("https://www.gov.cn/robots.txt",),
            content_type="text/plain",
            charset="utf-8",
            body=b"User-agent: *\nVisit-time: 0400 0845\n",
        )

    monkeypatch.setattr(fetcher, "_download", visit_time)
    with pytest.raises(PolicyError) as caught:
        await fetcher._robots_decision("https://www.gov.cn/public")
    assert caught.value.code == "robots_visit_time_unsupported"


@pytest.mark.asyncio
async def test_robots_failure_is_fail_closed(settings_factory, monkeypatch) -> None:
    fetcher = _fetcher(settings_factory)

    async def failed(*args, **kwargs):
        del args, kwargs
        raise OfficialSearchError("fetch_timeout", "timeout", retryable=True)

    monkeypatch.setattr(fetcher, "_download", failed)
    with pytest.raises(PolicyError) as caught:
        await fetcher._robots_decision("https://www.gov.cn/public")
    assert caught.value.code == "robots_unavailable"


@pytest.mark.asyncio
async def test_robots_invalid_charset_is_explainable_fail_closed(
    settings_factory, monkeypatch
) -> None:
    fetcher = _fetcher(settings_factory)

    async def invalid_charset(*args, **kwargs):
        del args, kwargs
        return DownloadedResponse(
            status=200,
            final_url="https://www.gov.cn/robots.txt",
            redirect_chain=("https://www.gov.cn/robots.txt",),
            content_type="text/plain",
            charset="not-a-real-codec",
            body=b"User-agent: *\nDisallow: /\n",
        )

    monkeypatch.setattr(fetcher, "_download", invalid_charset)
    with pytest.raises(PolicyError) as caught:
        await fetcher._robots_decision("https://www.gov.cn/public")
    assert caught.value.code == "robots_parse_failed"


@pytest.mark.asyncio
async def test_offline_fetch_extracts_hash_and_rejects_mime(settings_factory, monkeypatch) -> None:
    fetcher = _fetcher(settings_factory)

    async def html_page(*args, **kwargs):
        del args, kwargs
        return DownloadedResponse(
            status=200,
            final_url="https://www.gov.cn/public",
            redirect_chain=("https://www.gov.cn/public",),
            content_type="text/html; charset=utf-8",
            charset="utf-8",
            body=b"<html><title>Test</title><body>Public fictional content.</body></html>",
        )

    monkeypatch.setattr(fetcher, "_download", html_page)
    data, _warnings = await fetcher.fetch("https://www.gov.cn/public")
    assert data.title == "Test"
    assert len(data.content_sha256) == 64
    assert data.evidence_status == "retrieved_not_verified"

    async def binary_page(*args, **kwargs):
        del args, kwargs
        return DownloadedResponse(
            status=200,
            final_url="https://www.gov.cn/file.pdf",
            redirect_chain=("https://www.gov.cn/file.pdf",),
            content_type="application/pdf",
            charset=None,
            body=b"%PDF-offline-fixture",
        )

    monkeypatch.setattr(fetcher, "_download", binary_page)
    with pytest.raises(OfficialSearchError) as caught:
        await fetcher.fetch("https://www.gov.cn/file.pdf")
    assert caught.value.code == "unsupported_content_type"


class _RedirectResponse:
    status = 302
    headers = {"Location": "https://other.gov.cn/private"}
    charset = None

    def __init__(self, url: str) -> None:
        self.url = url

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback


class _RedirectSession:
    def __init__(self, requested: list[str]) -> None:
        self.requested = requested

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback

    def get(self, url: str, **kwargs):
        del kwargs
        self.requested.append(url)
        return _RedirectResponse(url)


@pytest.mark.asyncio
async def test_redirect_rechecks_robots_before_cross_origin_request(
    settings_factory, monkeypatch
) -> None:
    fetcher = _fetcher(settings_factory)
    robots_checked: list[str] = []
    requested: list[str] = []
    session_options: dict[str, object] = {}

    async def robots(url: str, **kwargs) -> RobotsDecision:
        del kwargs
        robots_checked.append(url)
        return RobotsDecision(
            allowed=not url.startswith("https://other.gov.cn/"),
            crawl_delay=0,
        )

    monkeypatch.setattr(fetcher, "_robots_decision", robots)
    monkeypatch.setattr(
        "official_search_mcp.fetcher.aiohttp.TCPConnector", lambda **kwargs: object()
    )

    def fake_session(**kwargs):
        session_options.update(kwargs)
        return _RedirectSession(requested)

    monkeypatch.setattr("official_search_mcp.fetcher.aiohttp.ClientSession", fake_session)

    with pytest.raises(PolicyError) as caught:
        await fetcher._download(
            "https://www.gov.cn/start",
            max_bytes=1024,
            enforce_robots=True,
        )
    assert caught.value.code == "robots_disallowed"
    assert robots_checked == [
        "https://www.gov.cn/start",
        "https://other.gov.cn/private",
    ]
    assert requested == ["https://www.gov.cn/start"]
    assert isinstance(session_options["cookie_jar"], aiohttp.DummyCookieJar)


@pytest.mark.asyncio
async def test_fetch_deadline_includes_all_work(settings_factory, monkeypatch) -> None:
    fetcher = _fetcher(lambda: settings_factory(fetch_timeout_seconds=0.01))

    async def slow_download(*args, **kwargs):
        del args, kwargs
        await __import__("asyncio").sleep(1)

    monkeypatch.setattr(fetcher, "_download", slow_download)
    with pytest.raises(OfficialSearchError) as caught:
        await fetcher.fetch("https://www.gov.cn/public")
    assert caught.value.code == "fetch_timeout"
