from __future__ import annotations

from typing import Annotated, Literal

from mcp.server import MCPServer
from pydantic import Field

from . import __version__
from .models import (
    FetchResponse,
    SearchAndFetchResponse,
    SearchResponse,
    TenderCrosscheckResponse,
)
from .service import OfficialSearchService


def create_server(service: OfficialSearchService | None = None) -> MCPServer:
    search_service = service or OfficialSearchService()
    server = MCPServer(
        "official-search-mcp",
        version=__version__,
        instructions=(
            "Search and read public official websites. For tender claims, use the server-enforced "
            "forward-then-reverse crosscheck tool. Every result remains pending human review."
        ),
    )

    @server.tool()
    async def search_official_sources(
        query: Annotated[str, Field(min_length=2, max_length=300)],
        domains: Annotated[list[str] | None, Field(max_length=8)] = None,
        max_results: Annotated[int, Field(ge=1, le=10)] = 5,
    ) -> SearchResponse:
        """实时搜索获授权官网；返回标题、后端原始/规范 URL、摘要、时间和后端，不宣称已核验。"""
        return await search_service.search_official_sources(query, domains, max_results)

    @server.tool()
    async def fetch_official_page(
        url: Annotated[str, Field(min_length=8, max_length=2_048)],
    ) -> FetchResponse:
        """受控读取最近搜索给出的公开官网 URL；遵守 robots，并返回最终 URL、正文片段和哈希。"""
        return await search_service.fetch_official_page(url)

    @server.tool()
    async def search_and_fetch_official(
        query: Annotated[str, Field(min_length=2, max_length=300)],
        domains: Annotated[list[str] | None, Field(max_length=8)] = None,
        max_results: Annotated[int, Field(ge=1, le=3)] = 3,
    ) -> SearchAndFetchResponse:
        """先搜索再受控读取最多 3 个候选，并逐项保留搜索来源与抓取错误。"""
        return await search_service.search_and_fetch_official(query, domains, max_results)

    @server.tool()
    async def crosscheck_tender_information(
        project_name: Annotated[str, Field(min_length=2, max_length=120)],
        procurement_regime: Literal[
            "government_procurement", "mandatory_tender", "unknown"
        ] = "unknown",
        project_code: Annotated[str | None, Field(min_length=4, max_length=60)] = None,
        package_code: Annotated[str | None, Field(min_length=1, max_length=40)] = None,
        purchaser: Annotated[str | None, Field(min_length=2, max_length=120)] = None,
        supplier_legal_name: Annotated[
            str | None, Field(min_length=2, max_length=120)
        ] = None,
        unified_social_credit_code: Annotated[
            str | None, Field(min_length=18, max_length=18)
        ] = None,
        claimed_amount: Annotated[str | None, Field(min_length=1, max_length=50)] = None,
        claimed_date: Annotated[str | None, Field(min_length=1, max_length=40)] = None,
        region: Annotated[str | None, Field(min_length=1, max_length=60)] = None,
        notice_type: Literal[
            "tender_notice",
            "candidate_notice",
            "award_result",
            "contract_notice",
            "acceptance_record",
            "unknown",
        ] = "unknown",
        claimed_role: Literal[
            "bidder",
            "candidate",
            "winner",
            "contractor",
            "completed_performance",
            "unknown",
        ] = "unknown",
    ) -> TenderCrosscheckResponse:
        """先做固定正向搜索，再做固定逆向反证搜索；输出冲突/缺口并强制人工签核。"""
        return await search_service.crosscheck_tender_information(
            project_name,
            procurement_regime=procurement_regime,
            project_code=project_code,
            package_code=package_code,
            purchaser=purchaser,
            supplier_legal_name=supplier_legal_name,
            unified_social_credit_code=unified_social_credit_code,
            claimed_amount=claimed_amount,
            claimed_date=claimed_date,
            region=region,
            notice_type=notice_type,
            claimed_role=claimed_role,
            domains=None,
            max_results_per_query=3,
            max_pages_to_fetch=10,
        )

    return server


mcp = create_server()


def main() -> None:
    """Run the local MCP server over the official Python SDK's stdio transport."""
    mcp.run()


if __name__ == "__main__":
    main()
