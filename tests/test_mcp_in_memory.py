from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from mcp import Client

from official_search_mcp.models import FetchData
from official_search_mcp.server import create_server
from official_search_mcp.service import OfficialSearchService


class MCPStubFetcher:
    async def fetch(self, url: str, *, restrict_domains=None):
        del restrict_domains
        content = "离线虚构招投标页面，仅用于 MCP 结构化调用测试。"
        raw = content.encode("utf-8")
        return (
            FetchData(
                requested_url=url,
                final_url=url,
                canonical_url=url,
                redirect_chain=[url],
                title="离线虚构页面",
                content_excerpt=content,
                extracted_char_count=len(content),
                content_truncated=False,
                fetched_at=datetime.now(UTC),
                content_sha256=hashlib.sha256(raw).hexdigest(),
                content_type="text/html",
                bytes_read=len(raw),
                extraction_method="mcp_test_stub",
                retrieval_status="retrieved",
                robots_policy="allowed",
                evidence_status="retrieved_not_verified",
            ),
            [],
        )


@pytest.mark.asyncio
async def test_mcp_lists_and_calls_structured_search_tool(fixture_settings) -> None:
    server = create_server(OfficialSearchService(fixture_settings))
    async with Client(server) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools.tools}
        assert names == {
            "search_official_sources",
            "fetch_official_page",
            "search_and_fetch_official",
            "crosscheck_tender_information",
        }
        result = await client.call_tool(
            "search_official_sources",
            {"query": "公开政策", "domains": ["gov.cn"], "max_results": 1},
        )
    assert not result.is_error
    assert result.structured_content
    assert result.structured_content["ok"] is True
    assert result.structured_content["data"]["live_search_performed"] is False


@pytest.mark.asyncio
async def test_mcp_calls_tender_crosscheck_with_mandatory_human_gate(
    fixture_settings,
) -> None:
    service = OfficialSearchService(fixture_settings, fetcher=MCPStubFetcher())
    server = create_server(service)
    async with Client(server) as client:
        result = await client.call_tool(
            "crosscheck_tender_information",
            {
                "project_name": "离线虚构政府采购项目",
                "procurement_regime": "government_procurement",
            },
        )

    assert not result.is_error
    assert result.structured_content
    payload = result.structured_content
    assert payload["ok"] is True
    assert payload["data"]["workflow_version"] == "cn_tender_bidirectional/1.0"
    assert len(payload["data"]["search_runs"]) == 8
    assert payload["data"]["system_status"] == "offline_fixture_only"
    assert payload["data"]["human_gate"] == {
        "manual_review_required": True,
        "system_may_auto_approve": False,
        "platform_must_record_reviewer_identity": True,
        "permitted_human_outcomes": [
            "approved_by_human",
            "rejected_by_human",
            "needs_more_evidence",
        ],
    }
