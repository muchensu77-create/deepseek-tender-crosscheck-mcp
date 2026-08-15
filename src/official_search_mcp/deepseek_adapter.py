from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .service import OfficialSearchService


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchArguments(_Arguments):
    query: str = Field(min_length=2, max_length=300)
    domains: list[str] | None = Field(default=None, max_length=8)
    max_results: int = Field(default=5, ge=1, le=10)


class FetchArguments(_Arguments):
    url: str = Field(min_length=8, max_length=2_048)


class SearchAndFetchArguments(_Arguments):
    query: str = Field(min_length=2, max_length=300)
    domains: list[str] | None = Field(default=None, max_length=8)
    max_results: int = Field(default=3, ge=1, le=3)


class TenderCrosscheckArguments(_Arguments):
    project_name: str = Field(min_length=2, max_length=120)
    procurement_regime: Literal[
        "government_procurement", "mandatory_tender", "unknown"
    ] = "unknown"
    project_code: str | None = Field(default=None, min_length=4, max_length=60)
    package_code: str | None = Field(default=None, min_length=1, max_length=40)
    purchaser: str | None = Field(default=None, min_length=2, max_length=120)
    supplier_legal_name: str | None = Field(default=None, min_length=2, max_length=120)
    unified_social_credit_code: str | None = Field(
        default=None, min_length=18, max_length=18
    )
    claimed_amount: str | None = Field(default=None, min_length=1, max_length=50)
    claimed_date: str | None = Field(default=None, min_length=1, max_length=40)
    region: str | None = Field(default=None, min_length=1, max_length=60)
    notice_type: Literal[
        "tender_notice",
        "candidate_notice",
        "award_result",
        "contract_notice",
        "acceptance_record",
        "unknown",
    ] = "unknown"
    claimed_role: Literal[
        "bidder",
        "candidate",
        "winner",
        "contractor",
        "completed_performance",
        "unknown",
    ] = "unknown"


_ARGUMENT_MODELS: dict[str, type[_Arguments]] = {
    "search_official_sources": SearchArguments,
    "fetch_official_page": FetchArguments,
    "search_and_fetch_official": SearchAndFetchArguments,
    "crosscheck_tender_information": TenderCrosscheckArguments,
}

_DESCRIPTIONS = {
    "search_official_sources": (
        "实时搜索服务器授权的公开官网。结果只是未核验候选，包含原 URL、摘要、查询时间和后端。"
    ),
    "fetch_official_page": (
        "读取本进程近期搜索结果中的官网 URL，遵守 robots/SSRF 限制并返回正文片段和哈希。"
    ),
    "search_and_fetch_official": (
        "确定性串联官网搜索和受控读取，逐项保留候选来源；最多读取 3 个结果。"
    ),
    "crosscheck_tender_information": (
        "政府招投标专用双向复核：服务端强制先正向搜索取证，再逆向查更正、终止、"
        "废标、投诉、合同和验收记录；只输出待人工签核报告，绝不自动判定通过。"
    ),
}


def deepseek_tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI-compatible function schemas accepted by DeepSeek Chat Completions."""
    definitions: list[dict[str, Any]] = []
    for name, model in _ARGUMENT_MODELS.items():
        definitions.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": _DESCRIPTIONS[name],
                    "parameters": model.model_json_schema(),
                },
            }
        )
    return definitions


def _bridge_error(code: str, message: str) -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "ok": False,
            "data": None,
            "error": {
                "code": code,
                "message": message,
                "retryable": False,
                "details": {},
            },
            "warnings": [],
        },
        ensure_ascii=False,
    )


async def dispatch_deepseek_function(
    service: OfficialSearchService,
    name: str,
    arguments_json: str,
    *,
    timeout_seconds: float | None = None,
) -> str:
    """Validate one DeepSeek tool_call and return JSON for a role=tool message."""
    if name not in _ARGUMENT_MODELS:
        return _bridge_error("unknown_tool", "模型请求了未注册的工具名。")
    if not isinstance(arguments_json, str):
        return _bridge_error("invalid_arguments_type", "模型工具参数必须是 JSON 字符串。")
    try:
        arguments_bytes = arguments_json.encode("utf-8")
    except UnicodeError:
        return _bridge_error("invalid_arguments_encoding", "模型工具参数不是有效 UTF-8 文本。")
    if len(arguments_bytes) > 16 * 1024:
        return _bridge_error("arguments_too_large", "工具参数超过 16 KiB 上限。")
    try:
        raw = json.loads(arguments_json)
    except (ValueError, RecursionError):
        return _bridge_error("invalid_arguments_json", "模型工具参数不是有效 JSON。")
    if not isinstance(raw, dict):
        return _bridge_error("invalid_arguments_type", "模型工具参数必须是 JSON object。")
    try:
        arguments = _ARGUMENT_MODELS[name].model_validate(raw)
    except ValidationError as exc:
        return _bridge_error(
            "arguments_schema_error",
            f"模型工具参数不符合 schema（{exc.error_count()} 项错误）。",
        )

    async def invoke():
        if isinstance(arguments, SearchArguments):
            return await service.search_official_sources(
                arguments.query, arguments.domains, arguments.max_results
            )
        if isinstance(arguments, FetchArguments):
            return await service.fetch_official_page(arguments.url)
        if isinstance(arguments, SearchAndFetchArguments):
            return await service.search_and_fetch_official(
                arguments.query, arguments.domains, arguments.max_results
            )
        if isinstance(arguments, TenderCrosscheckArguments):
            return await service.crosscheck_tender_information(
                arguments.project_name,
                procurement_regime=arguments.procurement_regime,
                project_code=arguments.project_code,
                package_code=arguments.package_code,
                purchaser=arguments.purchaser,
                supplier_legal_name=arguments.supplier_legal_name,
                unified_social_credit_code=arguments.unified_social_credit_code,
                claimed_amount=arguments.claimed_amount,
                claimed_date=arguments.claimed_date,
                region=arguments.region,
                notice_type=arguments.notice_type,
                claimed_role=arguments.claimed_role,
                domains=None,
                max_results_per_query=3,
                max_pages_to_fetch=10,
            )
        raise AssertionError("unreachable argument model")

    effective_timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else (
            service.settings.tender_workflow_timeout_seconds + 15.0
            if isinstance(arguments, TenderCrosscheckArguments)
            else 65.0
        )
    )
    try:
        response = await asyncio.wait_for(invoke(), timeout=effective_timeout)
    except TimeoutError:
        return _bridge_error("tool_timeout", "工具调用超过宿主 deadline。")
    return json.dumps(response.model_dump(mode="json"), ensure_ascii=False)


ToolName = Literal[
    "search_official_sources",
    "fetch_official_page",
    "search_and_fetch_official",
    "crosscheck_tender_information",
]
