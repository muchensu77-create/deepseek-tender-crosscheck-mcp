from __future__ import annotations

import os

import pytest

from official_search_mcp.config import Settings
from official_search_mcp.service import OfficialSearchService


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_TESTS") != "1",
    reason="set RUN_LIVE_TESTS=1 to make real public-internet requests",
)
@pytest.mark.asyncio
async def test_real_search_and_controlled_fetch() -> None:
    settings = Settings(
        backend="ddgs",
        default_domains=("gov.cn",),
        official_suffixes=("gov.cn",),
        max_results=10,
        min_domain_interval_seconds=0.2,
        requests_per_minute=60,
    )
    service = OfficialSearchService(settings)
    search = await service.search_official_sources("政府采购 条例", ["gov.cn"], 5)
    assert search.ok, search.model_dump(mode="json")
    assert search.data and search.data.live_search_performed
    assert search.data.results
    assert all(result.url.startswith(("https://", "http://")) for result in search.data.results)

    attempts = []
    for result in search.data.results[:3]:
        fetched = await service.fetch_official_page(result.url)
        attempts.append(fetched)
        if fetched.ok:
            break
    successful = next((item for item in attempts if item.ok), None)
    assert successful is not None, [item.model_dump(mode="json") for item in attempts]
    assert successful.data
    assert successful.data.final_url
    assert successful.data.fetched_at
    assert len(successful.data.content_sha256) == 64
