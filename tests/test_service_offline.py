from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from official_search_mcp.backends.base import RawSearchResult, SearchBackend
from official_search_mcp.models import FetchData
from official_search_mcp.service import CandidateRegistry, OfficialSearchService


class StubFetcher:
    def __init__(self) -> None:
        self.restrict_domains = None

    async def fetch(self, url: str, *, restrict_domains=None):
        self.restrict_domains = restrict_domains
        return (
            FetchData(
                requested_url=url,
                final_url=url,
                canonical_url=url,
                redirect_chain=[url],
                title="离线虚构页面",
                content_excerpt="仅用于离线单元测试。",
                extracted_char_count=len("仅用于离线单元测试。"),
                content_truncated=False,
                fetched_at=datetime.now(UTC),
                content_sha256="a" * 64,
                content_type="text/html",
                extraction_method="stub",
                retrieval_status="retrieved",
                robots_policy="allowed",
                bytes_read=24,
                evidence_status="retrieved_not_verified",
            ),
            [],
        )


class RawUrlBackend(SearchBackend):
    name = "raw_url_stub"
    is_live = False

    async def search(self, query: str, max_results: int):
        del query, max_results
        return [
            RawSearchResult(
                title="公开结果",
                url="HTTPS://WWW.GOV.CN:443/public#source-fragment",
                snippet="候选片段",
            )
        ]


class SlowBackend(SearchBackend):
    name = "slow_stub"
    is_live = True

    async def search(self, query: str, max_results: int):
        del query, max_results
        await asyncio.sleep(1)
        return []


@pytest.mark.asyncio
async def test_fixture_search_is_labeled_offline_and_filters_private(fixture_settings) -> None:
    service = OfficialSearchService(fixture_settings)
    response = await service.search_official_sources("公开政策", ["gov.cn"], 5)
    assert response.ok
    assert response.data is not None
    assert response.data.live_search_performed is False
    assert response.data.source_backend == "offline_fixture"
    assert all("127.0.0.1" not in item.url for item in response.data.results)
    assert all(item.original_url for item in response.data.results)
    assert any("离线 fixture" in warning for warning in response.warnings)


@pytest.mark.asyncio
async def test_domains_parameter_cannot_expand_policy(fixture_settings) -> None:
    service = OfficialSearchService(fixture_settings)
    response = await service.search_official_sources("公开政策", ["attacker.example"], 2)
    assert not response.ok
    assert response.error and response.error.code == "domain_not_authorized"


@pytest.mark.asyncio
async def test_fetch_rejects_model_invented_url_but_accepts_search_candidate(
    fixture_settings,
) -> None:
    fetcher = StubFetcher()
    service = OfficialSearchService(fixture_settings, fetcher=fetcher)
    rejected = await service.fetch_official_page("https://www.gov.cn/invented-by-model")
    assert not rejected.ok
    assert rejected.error and rejected.error.code == "url_not_in_search_candidates"

    search = await service.search_official_sources("公开政策", ["gov.cn"], 1)
    assert search.data and search.data.results
    accepted = await service.fetch_official_page(search.data.results[0].url)
    assert accepted.ok
    assert accepted.data and accepted.data.evidence_status == "retrieved_not_verified"
    assert fetcher.restrict_domains == ("gov.cn",)


@pytest.mark.asyncio
async def test_combined_tool_preserves_search_source(fixture_settings) -> None:
    service = OfficialSearchService(fixture_settings, fetcher=StubFetcher())
    response = await service.search_and_fetch_official("公开政策", ["gov.cn"], 2)
    assert response.ok and response.data
    assert len(response.data.pages) == 2
    assert response.data.pages[0].source.verification_status == "candidate_unverified"
    assert response.data.pages[0].fetch.data
    assert response.data.pages[0].fetch.data.content_sha256 == "a" * 64


@pytest.mark.asyncio
async def test_search_preserves_backend_url_and_returns_normalized_url(fixture_settings) -> None:
    service = OfficialSearchService(fixture_settings, backend=RawUrlBackend())
    response = await service.search_official_sources("公开政策", ["gov.cn"], 1)
    assert response.ok and response.data
    result = response.data.results[0]
    assert result.original_url == "HTTPS://WWW.GOV.CN:443/public#source-fragment"
    assert result.url == "https://www.gov.cn/public"


@pytest.mark.asyncio
async def test_search_deadline_includes_gate_and_backend(settings_factory) -> None:
    settings = settings_factory(search_timeout_seconds=0.01)
    service = OfficialSearchService(settings, backend=SlowBackend())
    response = await service.search_official_sources("公开政策", ["gov.cn"], 1)
    assert not response.ok
    assert response.error and response.error.code == "search_timeout"


@pytest.mark.asyncio
async def test_candidate_registry_never_widens_same_url_domain_scope() -> None:
    registry = CandidateRegistry()
    url = "https://notice.agency.gov.cn/item/1"
    await registry.add([url], ("agency.gov.cn",))
    await registry.add([url], ("gov.cn",))
    assert await registry.domains_for(url) == ("agency.gov.cn",)

    reverse_order = CandidateRegistry()
    await reverse_order.add([url], ("gov.cn",))
    await reverse_order.add([url], ("agency.gov.cn",))
    assert await reverse_order.domains_for(url) == ("agency.gov.cn",)
