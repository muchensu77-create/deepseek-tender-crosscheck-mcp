from __future__ import annotations

import os

import pytest

from official_search_mcp.config import Settings
from official_search_mcp.service import OfficialSearchService


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("RUN_TENDER_LIVE_TESTS") != "1",
    reason="set RUN_TENDER_LIVE_TESTS=1 to run the public tender two-pass smoke",
)
@pytest.mark.asyncio
async def test_real_public_tender_forward_and_reverse_workflow() -> None:
    settings = Settings(
        backend="ddgs",
        ddgs_backends="yahoo,duckduckgo,yandex",
        default_domains=("ccgp.gov.cn",),
        official_suffixes=("gov.cn",),
        max_results=10,
        search_timeout_seconds=20,
        tender_workflow_timeout_seconds=180,
        min_domain_interval_seconds=0.5,
        requests_per_minute=40,
    )
    service = OfficialSearchService(settings)
    response = await service.crosscheck_tender_information(
        "2026年智慧教室AI分析系统",
        procurement_regime="government_procurement",
        project_code="N5101042026000076",
        package_code="1",
        purchaser="成都市锦江区教育科学研究院",
        supplier_legal_name="四川广川合创科技有限公司",
        claimed_amount="841,000.00元",
        claimed_date="2026-07-01",
        region="四川省成都市锦江区",
        notice_type="award_result",
        claimed_role="winner",
        domains=["ccgp.gov.cn"],
        max_results_per_query=1,
        max_pages_to_fetch=4,
    )

    assert response.data, response.model_dump(mode="json")
    assert [run.query_id for run in response.data.search_runs] == [
        "F01",
        "F02",
        "F03",
        "R01",
        "R02",
        "R03",
        "R04",
        "R05",
    ]
    forward = response.data.search_runs[:3]
    reverse = response.data.search_runs[3:]
    assert any(
        run.response.data and run.response.data.live_search_performed for run in forward
    )
    assert any(
        run.response.data and run.response.data.live_search_performed for run in reverse
    )
    assert response.data.pages
    assert response.data.human_gate.manual_review_required is True
    assert response.data.human_gate.system_may_auto_approve is False
