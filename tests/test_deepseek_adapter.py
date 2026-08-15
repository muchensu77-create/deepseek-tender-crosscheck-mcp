from __future__ import annotations

import json

import pytest

from official_search_mcp.deepseek_adapter import (
    deepseek_tool_definitions,
    dispatch_deepseek_function,
)
from official_search_mcp.service import OfficialSearchService


def test_function_schemas_have_names_and_server_side_constraints() -> None:
    definitions = deepseek_tool_definitions()
    assert {item["function"]["name"] for item in definitions} == {
        "search_official_sources",
        "fetch_official_page",
        "search_and_fetch_official",
        "crosscheck_tender_information",
    }
    assert all(
        item["function"]["parameters"]["additionalProperties"] is False for item in definitions
    )


@pytest.mark.asyncio
async def test_bridge_rejects_invalid_json_unknown_tool_and_extra_fields(fixture_settings) -> None:
    service = OfficialSearchService(fixture_settings)
    invalid = json.loads(await dispatch_deepseek_function(service, "search_official_sources", "{"))
    assert invalid["error"]["code"] == "invalid_arguments_json"
    assert invalid["data"] is None
    assert invalid["warnings"] == []
    assert invalid["error"]["details"] == {}

    unknown = json.loads(await dispatch_deepseek_function(service, "made_up", "{}"))
    assert unknown["error"]["code"] == "unknown_tool"

    extra = json.loads(
        await dispatch_deepseek_function(
            service,
            "search_official_sources",
            '{"query":"公开政策","unexpected":"x"}',
        )
    )
    assert extra["error"]["code"] == "arguments_schema_error"

    utf8_oversize = json.loads(
        await dispatch_deepseek_function(
            service,
            "search_official_sources",
            json.dumps({"query": "中" * 6_000}, ensure_ascii=False),
        )
    )
    assert utf8_oversize["error"]["code"] == "arguments_too_large"

    invalid_unicode = json.loads(
        await dispatch_deepseek_function(
            service,
            "search_official_sources",
            '{"query":"\ud800"}',
        )
    )
    assert invalid_unicode["error"]["code"] == "invalid_arguments_encoding"

    huge_integer = json.loads(
        await dispatch_deepseek_function(
            service,
            "search_official_sources",
            '{"query":"公开政策","max_results":' + "9" * 5_000 + "}",
        )
    )
    assert huge_integer["error"]["code"] == "invalid_arguments_json"


@pytest.mark.asyncio
async def test_bridge_returns_structured_fixture_result(fixture_settings) -> None:
    service = OfficialSearchService(fixture_settings)
    payload = json.loads(
        await dispatch_deepseek_function(
            service,
            "search_official_sources",
            '{"query":"公开政策","domains":["gov.cn"],"max_results":1}',
        )
    )
    assert payload["ok"] is True
    assert payload["data"]["live_search_performed"] is False


@pytest.mark.asyncio
async def test_tender_bridge_rejects_model_attempt_to_skip_reverse_or_auto_approve(
    fixture_settings,
) -> None:
    service = OfficialSearchService(fixture_settings)
    skip_reverse = json.loads(
        await dispatch_deepseek_function(
            service,
            "crosscheck_tender_information",
            json.dumps(
                {"project_name": "离线虚构采购项目", "skip_reverse": True},
                ensure_ascii=False,
            ),
        )
    )
    assert skip_reverse["ok"] is False
    assert skip_reverse["error"]["code"] == "arguments_schema_error"

    auto_approve = json.loads(
        await dispatch_deepseek_function(
            service,
            "crosscheck_tender_information",
            json.dumps(
                {"project_name": "离线虚构采购项目", "auto_approve": True},
                ensure_ascii=False,
            ),
        )
    )
    assert auto_approve["ok"] is False
    assert auto_approve["error"]["code"] == "arguments_schema_error"

    model_controlled_policy = json.loads(
        await dispatch_deepseek_function(
            service,
            "crosscheck_tender_information",
            json.dumps(
                {
                    "project_name": "离线虚构采购项目",
                    "domains": ["gov.cn"],
                    "max_pages_to_fetch": 4,
                },
                ensure_ascii=False,
            ),
        )
    )
    assert model_controlled_policy["ok"] is False
    assert model_controlled_policy["error"]["code"] == "arguments_schema_error"
