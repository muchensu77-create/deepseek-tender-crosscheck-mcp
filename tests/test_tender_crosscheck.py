from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from official_search_mcp.backends.base import RawSearchResult, SearchBackend
from official_search_mcp.errors import BackendError, PolicyError
from official_search_mcp.models import FetchData
from official_search_mcp.service import OfficialSearchService
from official_search_mcp.tender import (
    build_tender_query_plan,
    matched_claim_anchor_fields,
    prepare_tender_claim,
)

PROJECT_NAME = "智慧教育平台采购项目"
PROJECT_CODE = "ZC-2026-001"
PACKAGE_CODE = "包1"
PURCHASER = "上海市虚构采购中心"
SUPPLIER = "上海虚构科技有限公司"
CREDIT_CODE = "91310000MA1K123456"
CLAIMED_AMOUNT = "人民币100万元"
CLAIMED_DATE = "2026-08-15"
REGION = "上海市"


def _url(phase: str, number: int) -> str:
    return f"https://{phase}{number}.gov.cn/notices/{number}"


def _rows(phase: str, count: int, *, metadata_text: str = "") -> list[list[RawSearchResult]]:
    return [
        [
            RawSearchResult(
                title=f"{metadata_text or '招投标公开候选'} {number}",
                url=_url(phase, number),
                snippet=metadata_text or "仅为搜索候选摘要，不是已抓取正文。",
            )
        ]
        for number in range(1, count + 1)
    ]


class PlannedBackend(SearchBackend):
    is_live = True

    def __init__(
        self,
        name: str,
        planned_rows: list[list[RawSearchResult]],
        *,
        event_log: list[tuple[str, str, str]] | None = None,
        failing_calls: set[int] | None = None,
        empty_calls: set[int] | None = None,
    ) -> None:
        self.name = name
        self.planned_rows = planned_rows
        self.event_log = event_log if event_log is not None else []
        self.failing_calls = failing_calls or set()
        self.empty_calls = empty_calls or set()
        self.queries: list[str] = []

    async def search(self, query: str, max_results: int) -> list[RawSearchResult]:
        call_index = len(self.queries)
        self.queries.append(query)
        self.event_log.append(("search", self.name, query))
        if call_index in self.failing_calls:
            raise BackendError(
                "planned_search_failure",
                "离线测试注入的搜索失败。",
                retryable=True,
            )
        if call_index in self.empty_calls:
            return []
        rows = self.planned_rows[call_index] if call_index < len(self.planned_rows) else []
        return rows[:max_results]


class RecordingFetcher:
    def __init__(
        self,
        content_by_url: dict[str, str] | None = None,
        *,
        event_log: list[tuple[str, str, str]] | None = None,
    ) -> None:
        self.content_by_url = content_by_url or {}
        self.event_log = event_log if event_log is not None else []

    async def fetch(self, url: str, *, restrict_domains=None):
        del restrict_domains
        self.event_log.append(("fetch", "controlled_stub", url))
        content = self.content_by_url.get(url, "这是公开页面的离线虚构正文片段。")
        raw = content.encode("utf-8")
        return (
            FetchData(
                requested_url=url,
                final_url=url,
                canonical_url=url,
                redirect_chain=[url],
                title="已抓取页面标题",
                content_excerpt=content,
                extracted_char_count=len(content),
                content_truncated=False,
                fetched_at=datetime.now(UTC),
                content_sha256=hashlib.sha256(raw).hexdigest(),
                content_type="text/html",
                bytes_read=len(raw),
                extraction_method="offline_test_stub",
                retrieval_status="retrieved",
                robots_policy="allowed",
                evidence_status="retrieved_not_verified",
            ),
            [],
        )


class DegradedFetcher(RecordingFetcher):
    def __init__(self, content_by_url: dict[str, str], degraded_urls: set[str]) -> None:
        super().__init__(content_by_url)
        self.degraded_urls = degraded_urls

    async def fetch(self, url: str, *, restrict_domains=None):
        data, warnings = await super().fetch(url, restrict_domains=restrict_domains)
        if url in self.degraded_urls:
            data = data.model_copy(update={"retrieval_status": "degraded_dynamic"})
        return data, warnings


class TruncatedFetcher(RecordingFetcher):
    async def fetch(self, url: str, *, restrict_domains=None):
        data, warnings = await super().fetch(url, restrict_domains=restrict_domains)
        data = data.model_copy(
            update={
                "content_truncated": True,
                "extracted_char_count": len(data.content_excerpt) + 10_000,
            }
        )
        return data, [*warnings, "正文已截断。"]


def _claim_arguments() -> dict[str, str]:
    return {
        "procurement_regime": "government_procurement",
        "project_code": PROJECT_CODE,
        "package_code": PACKAGE_CODE,
        "purchaser": PURCHASER,
        "supplier_legal_name": SUPPLIER,
        "unified_social_credit_code": CREDIT_CODE,
        "claimed_amount": CLAIMED_AMOUNT,
        "claimed_date": CLAIMED_DATE,
        "region": REGION,
        "notice_type": "award_result",
        "claimed_role": "winner",
    }


def _full_claim_text() -> str:
    return " ".join(
        (
            PROJECT_NAME,
            PROJECT_CODE,
            PACKAGE_CODE,
            PURCHASER,
            SUPPLIER,
            CREDIT_CODE,
            CLAIMED_AMOUNT,
            CLAIMED_DATE,
            REGION,
        )
    )


def test_project_code_anchor_requires_token_boundary() -> None:
    claim = prepare_tender_claim(
        project_name=PROJECT_NAME,
        procurement_regime="government_procurement",
        project_code="ZC-01",
        package_code=None,
        purchaser=None,
        supplier_legal_name=None,
        unified_social_credit_code=None,
        claimed_amount=None,
        claimed_date=None,
        region=None,
        notice_type="unknown",
        claimed_role="unknown",
    )
    assert matched_claim_anchor_fields(claim, "另一个项目编号 ZC-010") == []
    assert matched_claim_anchor_fields(claim, "项目编号：ZC 01") == ["project_code"]

    separated_claim = claim.model_copy(update={"project_code": "AB-2026-001"})
    unrelated = "与本项目无关：AB公司于2026年完成001号其他任务。"
    assert matched_claim_anchor_fields(separated_claim, unrelated) == []
    assert matched_claim_anchor_fields(separated_claim, "编号 AB／2026／001") == [
        "project_code"
    ]

    chinese_claim = claim.model_copy(update={"project_code": "川政采招〔2026〕1号"})
    assert matched_claim_anchor_fields(
        chinese_claim, "项目编号川政采招〔2026〕1号。"
    ) == ["project_code"]


def test_four_digit_year_cannot_be_used_as_project_code() -> None:
    with pytest.raises(PolicyError, match="纯数字"):
        prepare_tender_claim(
            project_name=PROJECT_NAME,
            procurement_regime="government_procurement",
            project_code="2026",
            package_code=None,
            purchaser=None,
            supplier_legal_name=None,
            unified_social_credit_code=None,
            claimed_amount=None,
            claimed_date=None,
            region=None,
            notice_type="unknown",
            claimed_role="unknown",
        )

    with pytest.raises(PolicyError, match="YYYYMMDD"):
        prepare_tender_claim(
            project_name=PROJECT_NAME,
            procurement_regime="government_procurement",
            project_code="20260815",
            package_code=None,
            purchaser=None,
            supplier_legal_name=None,
            unified_social_credit_code=None,
            claimed_amount=None,
            claimed_date=None,
            region=None,
            notice_type="unknown",
            claimed_role="unknown",
        )


def test_pure_numeric_project_code_requires_an_adjacent_field_label() -> None:
    claim = prepare_tender_claim(
        project_name=PROJECT_NAME,
        procurement_regime="government_procurement",
        project_code="12345678",
        package_code=None,
        purchaser=None,
        supplier_legal_name=None,
        unified_social_credit_code=None,
        claimed_amount=None,
        claimed_date=None,
        region=None,
        notice_type="unknown",
        claimed_role="unknown",
    )
    assert matched_claim_anchor_fields(claim, "发布日期12345678") == []
    assert matched_claim_anchor_fields(claim, "采购编号：12345678") == [
        "project_code"
    ]


def test_query_plan_does_not_repeat_project_fallback_at_schema_limits() -> None:
    claim = prepare_tender_claim(
        project_name="智" * 120,
        procurement_regime="unknown",
        project_code=None,
        package_code="包" * 40,
        purchaser=None,
        supplier_legal_name=None,
        unified_social_credit_code=None,
        claimed_amount=None,
        claimed_date=None,
        region=None,
        notice_type="unknown",
        claimed_role="unknown",
    )
    forward, reverse = build_tender_query_plan(claim)
    assert len(forward) == 3 and len(reverse) == 5
    assert all(len(item.query) <= 300 for item in [*forward, *reverse])


@pytest.mark.asyncio
async def test_tender_workflow_enforces_three_forward_then_five_reverse_queries(
    fixture_settings,
) -> None:
    events: list[tuple[str, str, str]] = []
    forward = PlannedBackend("forward_live_stub", _rows("forward", 3), event_log=events)
    reverse = PlannedBackend("reverse_live_stub", _rows("reverse", 5), event_log=events)
    fetcher = RecordingFetcher(
        {
            _url(phase, number): _full_claim_text()
            for phase, count in (("forward", 3), ("reverse", 5))
            for number in range(1, count + 1)
        },
        event_log=events,
    )
    service = OfficialSearchService(
        fixture_settings,
        backend=forward,
        reverse_backend=reverse,
        fetcher=fetcher,
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        domains=["gov.cn"],
        max_results_per_query=1,
        max_pages_to_fetch=10,
    )

    assert response.ok and response.data
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
    assert [run.phase for run in response.data.search_runs] == [
        "forward",
        "forward",
        "forward",
        "reverse",
        "reverse",
        "reverse",
        "reverse",
        "reverse",
    ]
    assert len(forward.queries) == 3
    assert len(reverse.queries) == 5
    rendered_plan = "\n".join((*forward.queries, *reverse.queries))
    for required_term in (
        "中标",
        "成交",
        "合同公告",
        "履约验收",
        "更正公告",
        "变更公告",
        "澄清公告",
        "终止公告",
        "中止",
        "暂停",
        "废标",
        "流标",
        "投诉处理",
        "监督检查",
        "重新采购",
        "重新招标",
    ):
        assert required_term in rendered_plan
    for supplied_anchor in (PURCHASER, SUPPLIER, PACKAGE_CODE, REGION, CLAIMED_AMOUNT):
        assert supplied_anchor in rendered_plan

    first_reverse_event = next(
        index
        for index, event in enumerate(events)
        if event[0] == "search" and event[1] == "reverse_live_stub"
    )
    assert all(event[1] == "forward_live_stub" for event in events[:3])
    assert all(event[1] != "reverse_live_stub" for event in events[:first_reverse_event])
    first_fetch_event = next(index for index, event in enumerate(events) if event[0] == "fetch")
    assert first_fetch_event == 8
    assert all(event[0] == "search" for event in events[:first_fetch_event])

    coverage = response.data.coverage
    assert coverage.required_forward_queries == 3
    assert coverage.successful_forward_queries == 3
    assert coverage.required_reverse_queries == 5
    assert coverage.successful_reverse_queries == 5
    assert coverage.different_reverse_backend_configured is True
    assert response.data.system_status == "two_pass_completed_pending_human"
    assert response.data.human_gate.manual_review_required is True
    assert response.data.human_gate.system_may_auto_approve is False
    latest_query_time = max(
        run.response.data.queried_at
        for run in response.data.search_runs
        if run.response.data is not None
    )
    assert response.data.cutoff_at == latest_query_time
    assert response.data.cutoff_at < response.data.completed_at


@pytest.mark.asyncio
async def test_reverse_query_failure_never_produces_completed_status(fixture_settings) -> None:
    forward = PlannedBackend("forward_live_stub", _rows("forward", 3))
    reverse = PlannedBackend(
        "reverse_live_stub",
        _rows("reverse", 5),
        failing_calls={2},
    )
    service = OfficialSearchService(
        fixture_settings,
        backend=forward,
        reverse_backend=reverse,
        fetcher=RecordingFetcher({_url("forward", 1): _full_claim_text()}),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        max_results_per_query=1,
        max_pages_to_fetch=10,
    )

    assert response.ok and response.data
    assert response.data.coverage.successful_reverse_queries == 4
    assert response.data.system_status == "inconclusive"
    assert response.data.human_gate.system_may_auto_approve is False
    assert any("R03" in reason and "失败" in reason for reason in response.data.review_reasons)


@pytest.mark.asyncio
async def test_empty_reverse_query_is_evidence_gap_not_negative_proof(fixture_settings) -> None:
    forward = PlannedBackend("forward_live_stub", _rows("forward", 3))
    reverse = PlannedBackend(
        "reverse_live_stub",
        _rows("reverse", 5),
        empty_calls={1},
    )
    service = OfficialSearchService(
        fixture_settings,
        backend=forward,
        reverse_backend=reverse,
        fetcher=RecordingFetcher({_url("forward", 1): _full_claim_text()}),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        max_results_per_query=1,
        max_pages_to_fetch=8,
    )

    assert response.ok and response.data
    r02 = next(run for run in response.data.search_runs if run.query_id == "R02")
    assert r02.response.ok and r02.response.data
    assert r02.response.data.result_count == 0
    assert response.data.system_status == "inconclusive"
    assert response.data.human_gate.manual_review_required is True
    assert any(
        "R02" in reason and "零结果不代表风险不存在" in reason
        for reason in response.data.review_reasons
    )


@pytest.mark.asyncio
async def test_all_empty_searches_report_no_candidate_observed_not_absence(
    fixture_settings,
) -> None:
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("forward_live_stub", [[], [], []]),
        reverse_backend=PlannedBackend("reverse_live_stub", [[], [], [], [], []]),
        fetcher=RecordingFetcher(),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        max_results_per_query=1,
        max_pages_to_fetch=8,
    )

    assert response.ok and response.data
    assert response.data.system_status == "no_candidate_observed"
    assert response.data.human_gate.manual_review_required is True
    assert any("不等于项目或风险记录不存在" in item for item in response.data.review_reasons)


@pytest.mark.asyncio
async def test_claim_values_cannot_inject_search_operators(fixture_settings) -> None:
    forward = PlannedBackend("same_live_stub", [[], [], []])
    reverse = PlannedBackend("same_live_stub", [[], [], [], [], []])
    service = OfficialSearchService(
        fixture_settings,
        backend=forward,
        reverse_backend=reverse,
        fetcher=RecordingFetcher(),
    )

    response = await service.crosscheck_tender_information(
        '智慧项目" OR site:evil.example intitle:secret -终止 -废标',
        supplier_legal_name="虚构供应商 NOT filetype:pdf",
        max_results_per_query=1,
        max_pages_to_fetch=4,
    )

    assert response.ok and response.data
    actual_queries = [*forward.queries, *reverse.queries]
    assert len(actual_queries) == 8
    assert all("site:evil.example" not in query for query in actual_queries)
    assert all("intitle:" not in query for query in actual_queries)
    assert all("filetype:" not in query for query in actual_queries)
    assert all("-终止" not in query and "-废标" not in query for query in actual_queries)
    assert all(query.count("site:") == 1 for query in actual_queries)
    assert all("site:gov.cn" in query for query in actual_queries)


@pytest.mark.asyncio
async def test_hidden_format_character_in_claim_is_rejected(fixture_settings) -> None:
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("unused", []),
        fetcher=RecordingFetcher(),
    )

    response = await service.crosscheck_tender_information("智慧\u202e项目")

    assert not response.ok
    assert response.data is None
    assert response.error and response.error.code == "invalid_tender_claim"


@pytest.mark.asyncio
async def test_workflow_rejects_deadline_that_cannot_cover_fixed_budget(
    settings_factory,
) -> None:
    forward = PlannedBackend("forward_live_stub", _rows("forward", 3))
    reverse = PlannedBackend("reverse_live_stub", _rows("reverse", 5))
    settings = settings_factory(
        max_concurrency=1,
        tender_workflow_timeout_seconds=150,
    )
    service = OfficialSearchService(
        settings,
        backend=forward,
        reverse_backend=reverse,
        fetcher=RecordingFetcher(),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
    )

    assert not response.ok
    assert response.error and response.error.code == "tender_timeout_budget_conflict"
    assert response.error.details["minimum_required_seconds"] > 150
    assert forward.queries == []
    assert reverse.queries == []


@pytest.mark.asyncio
async def test_field_observations_ignore_search_title_and_snippet(fixture_settings) -> None:
    search_only_metadata = f"{SUPPLIER} {PURCHASER} {CLAIMED_AMOUNT}"
    forward = PlannedBackend(
        "forward_live_stub",
        _rows("forward", 3, metadata_text=search_only_metadata),
    )
    reverse = PlannedBackend(
        "reverse_live_stub",
        _rows("reverse", 5, metadata_text=search_only_metadata),
    )
    service = OfficialSearchService(
        fixture_settings,
        backend=forward,
        reverse_backend=reverse,
        fetcher=RecordingFetcher(
            {_url("forward", 1): (f"本页公布{PROJECT_NAME}，项目编号为 ZC 2026 001。")}
        ),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        project_code=PROJECT_CODE,
        purchaser=PURCHASER,
        supplier_legal_name=SUPPLIER,
        claimed_amount=CLAIMED_AMOUNT,
        max_results_per_query=1,
        max_pages_to_fetch=8,
    )

    assert response.ok and response.data
    observations = {item.field: item for item in response.data.field_observations}
    assert observations["project_name"].status == "observed_in_retrieved_excerpt"
    assert observations["project_code"].status == "observed_in_retrieved_excerpt"
    assert observations["supplier_legal_name"].status == ("not_observed_in_retrieved_excerpts")
    assert observations["purchaser"].status == "not_observed_in_retrieved_excerpts"
    assert observations["claimed_amount"].status == "not_observed_in_retrieved_excerpts"
    assert observations["supplier_legal_name"].supporting_urls == []


@pytest.mark.asyncio
async def test_package_amount_and_date_require_value_boundaries(fixture_settings) -> None:
    content = " ".join(
        (
            PROJECT_NAME,
            PROJECT_CODE,
            PURCHASER,
            SUPPLIER,
            CREDIT_CODE,
            REGION,
            "包10",
            "预算1100万元",
            "文件流水号2026-08-150",
        )
    )
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("forward_live_stub", _rows("forward", 3)),
        reverse_backend=PlannedBackend("reverse_live_stub", _rows("reverse", 5)),
        fetcher=RecordingFetcher(
            {
                _url(phase, number): content
                for phase, count in (("forward", 3), ("reverse", 5))
                for number in range(1, count + 1)
            }
        ),
    )
    arguments = _claim_arguments()
    arguments.update(
        {
            "package_code": "包1",
            "claimed_amount": "100万元",
            "claimed_date": "2026-08-15",
        }
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **arguments,
        max_results_per_query=1,
        max_pages_to_fetch=10,
    )

    assert response.ok and response.data
    observations = {item.field: item for item in response.data.field_observations}
    assert observations["package_code"].status == "not_observed_in_retrieved_excerpts"
    assert observations["claimed_amount"].status == "not_observed_in_retrieved_excerpts"
    assert observations["claimed_date"].status == "not_observed_in_retrieved_excerpts"
    assert response.data.system_status == "inconclusive"


@pytest.mark.parametrize(
    ("claimed_package", "page_package"),
    [("A1", "XA1"), ("包A", "包AB")],
)
@pytest.mark.asyncio
async def test_package_ascii_edges_do_not_match_longer_identifiers(
    fixture_settings,
    claimed_package: str,
    page_package: str,
) -> None:
    content = " ".join(
        (
            PROJECT_NAME,
            PROJECT_CODE,
            PURCHASER,
            SUPPLIER,
            CREDIT_CODE,
            CLAIMED_AMOUNT,
            CLAIMED_DATE,
            REGION,
            page_package,
        )
    )
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("forward_live_stub", _rows("forward", 3)),
        reverse_backend=PlannedBackend("reverse_live_stub", _rows("reverse", 5)),
        fetcher=RecordingFetcher(
            {
                _url(phase, number): content
                for phase, count in (("forward", 3), ("reverse", 5))
                for number in range(1, count + 1)
            }
        ),
    )
    arguments = _claim_arguments()
    arguments["package_code"] = claimed_package

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **arguments,
        max_results_per_query=1,
        max_pages_to_fetch=10,
    )

    assert response.ok and response.data
    package = next(
        item for item in response.data.field_observations if item.field == "package_code"
    )
    assert package.status == "not_observed_in_retrieved_excerpts"
    assert response.data.system_status == "inconclusive"


@pytest.mark.asyncio
async def test_risk_keyword_forces_risk_status_and_human_gate(fixture_settings) -> None:
    forward = PlannedBackend("forward_live_stub", _rows("forward", 3))
    reverse = PlannedBackend("reverse_live_stub", _rows("reverse", 5))
    service = OfficialSearchService(
        fixture_settings,
        backend=forward,
        reverse_backend=reverse,
        fetcher=RecordingFetcher(
            {
                _url("forward", 1): _full_claim_text(),
                _url("reverse", 1): (
                    f"{PROJECT_NAME}（项目编号 {PROJECT_CODE}）发布更正公告，并终止本次采购。"
                ),
            }
        ),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        max_results_per_query=1,
        max_pages_to_fetch=8,
    )

    assert response.ok and response.data
    assert response.data.system_status == "risk_signals_found"
    assert {signal.keyword for signal in response.data.risk_signals} >= {"更正", "终止"}
    assert all(
        signal.signal_status == "text_indicator_requires_manual_review"
        for signal in response.data.risk_signals
    )
    assert response.data.human_gate.manual_review_required is True
    assert response.data.human_gate.system_may_auto_approve is False


@pytest.mark.asyncio
async def test_unrelated_risk_page_is_not_attributed_to_claim(fixture_settings) -> None:
    forward = PlannedBackend("forward_live_stub", _rows("forward", 3))
    reverse = PlannedBackend("reverse_live_stub", _rows("reverse", 5))
    service = OfficialSearchService(
        fixture_settings,
        backend=forward,
        reverse_backend=reverse,
        fetcher=RecordingFetcher(
            {
                _url("forward", 1): _full_claim_text(),
                _url("reverse", 1): "另一个无关项目发布更正公告，并终止采购。",
            }
        ),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        max_results_per_query=1,
        max_pages_to_fetch=8,
    )

    assert response.ok and response.data
    assert response.data.risk_signals == []
    assert response.data.system_status != "risk_signals_found"


@pytest.mark.asyncio
async def test_project_name_anchor_can_surface_risk_when_page_omits_code(
    fixture_settings,
) -> None:
    forward = PlannedBackend("forward_live_stub", _rows("forward", 3))
    reverse = PlannedBackend("reverse_live_stub", _rows("reverse", 5))
    service = OfficialSearchService(
        fixture_settings,
        backend=forward,
        reverse_backend=reverse,
        fetcher=RecordingFetcher(
            {
                _url("forward", 1): _full_claim_text(),
                _url("reverse", 1): f"{PROJECT_NAME}发布终止公告。",
            }
        ),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        max_results_per_query=1,
        max_pages_to_fetch=8,
    )

    assert response.ok and response.data
    signal = next(item for item in response.data.risk_signals if item.keyword == "终止")
    assert signal.matched_anchor_fields == ["project_name"]
    assert response.data.system_status == "risk_signals_found"


@pytest.mark.asyncio
async def test_risk_status_is_not_hidden_by_missing_strong_identifiers(
    fixture_settings,
) -> None:
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("forward_live_stub", _rows("forward", 3)),
        reverse_backend=PlannedBackend("reverse_live_stub", _rows("reverse", 5)),
        fetcher=RecordingFetcher(
            {_url("reverse", 1): f"{PROJECT_NAME}现发布暂停及终止公告。"}
        ),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        procurement_regime="government_procurement",
        max_results_per_query=1,
        max_pages_to_fetch=8,
    )

    assert response.ok and response.data
    assert response.data.system_status == "risk_signals_found"
    assert {item.keyword for item in response.data.risk_signals} >= {"暂停", "终止"}
    assert any("项目/采购编号" in item for item in response.data.review_reasons)


@pytest.mark.asyncio
async def test_short_project_name_risk_page_is_not_silently_ignored(
    fixture_settings,
) -> None:
    project_name = "保安服务"
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("forward_live_stub", _rows("forward", 3)),
        reverse_backend=PlannedBackend("reverse_live_stub", _rows("reverse", 5)),
        fetcher=RecordingFetcher(
            {
                _url("forward", 1): f"{project_name} {PROJECT_CODE}",
                _url("reverse", 1): f"{project_name}现发布终止公告。",
            }
        ),
    )

    response = await service.crosscheck_tender_information(
        project_name,
        procurement_regime="government_procurement",
        project_code=PROJECT_CODE,
        max_results_per_query=1,
        max_pages_to_fetch=10,
    )

    assert response.ok and response.data
    assert any(item.keyword == "终止" for item in response.data.risk_signals)
    assert response.data.system_status == "risk_signals_found"


@pytest.mark.parametrize(
    "risk_phrase",
    [
        "认定本项目中标结果无效",
        "认定本项目成交结果无效",
        "认定本项目采购结果无效",
        "本项目验收不合格",
        "本项目验收不通过",
        "现解除合同",
        "本项目合同解除",
        "本中标结果公告作废",
        "原公告已废止",
        "现撤回本项目公告",
        "本项目重新开展采购",
        "本项目重新开展招标",
        "本项目中标结果公告无效",
        "政府采购投诉决定书",
        "本项目重新组织采购活动",
        "本项目验收结果不合格",
        "本项目合同被解除",
        "本项目停止采购",
        "本项目现予终 止",
        "本项目发布采购失败公告",
        "本项目采购活动失败",
        "本项目重新实施采购",
        "现解除本项目采购合同",
        "本项目未通过验收",
        "本项目现予终\u200b止",
    ],
)
@pytest.mark.asyncio
async def test_material_adverse_phrases_always_surface_risk_signal(
    fixture_settings,
    risk_phrase: str,
) -> None:
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("forward_live_stub", _rows("forward", 3)),
        reverse_backend=PlannedBackend("reverse_live_stub", _rows("reverse", 5)),
        fetcher=RecordingFetcher(
            {
                _url("forward", 1): _full_claim_text(),
                _url("reverse", 1): f"{_full_claim_text()} {risk_phrase}",
            }
        ),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        max_results_per_query=1,
        max_pages_to_fetch=10,
    )

    assert response.ok and response.data
    assert response.data.risk_signals
    assert response.data.system_status == "risk_signals_found"


@pytest.mark.asyncio
async def test_degraded_reverse_pages_can_never_complete_two_pass_status(
    fixture_settings,
) -> None:
    reverse_urls = {_url("reverse", number) for number in range(1, 6)}
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("forward_live_stub", _rows("forward", 3)),
        reverse_backend=PlannedBackend("reverse_live_stub", _rows("reverse", 5)),
        fetcher=DegradedFetcher(
            {_url("forward", 1): _full_claim_text()},
            degraded_urls=reverse_urls,
        ),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        max_results_per_query=1,
        max_pages_to_fetch=10,
    )

    assert response.ok and response.data
    assert response.data.coverage.fetched_failure_count == 0
    assert response.data.coverage.degraded_or_no_text_page_count == 5
    assert response.data.system_status == "inconclusive"
    assert response.data.human_gate.system_may_auto_approve is False


@pytest.mark.asyncio
async def test_truncated_pages_are_explicit_gaps_and_can_never_complete(
    fixture_settings,
) -> None:
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("forward_live_stub", _rows("forward", 3)),
        reverse_backend=PlannedBackend("reverse_live_stub", _rows("reverse", 5)),
        fetcher=TruncatedFetcher(
            {
                _url(phase, number): _full_claim_text()
                for phase, count in (("forward", 3), ("reverse", 5))
                for number in range(1, count + 1)
            }
        ),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        max_results_per_query=1,
        max_pages_to_fetch=10,
    )

    assert response.ok and response.data
    assert response.data.coverage.fully_retrieved_page_count == 0
    assert response.data.coverage.degraded_or_no_text_page_count == 8
    assert response.data.system_status == "inconclusive"
    assert any("被截断" in reason for reason in response.data.review_reasons)


@pytest.mark.asyncio
async def test_unrelated_reverse_candidates_do_not_count_as_completed_reverse_pass(
    fixture_settings,
) -> None:
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("forward_live_stub", _rows("forward", 3)),
        reverse_backend=PlannedBackend("reverse_live_stub", _rows("reverse", 5)),
        fetcher=RecordingFetcher({_url("forward", 1): _full_claim_text()}),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        max_results_per_query=1,
        max_pages_to_fetch=8,
    )

    assert response.ok and response.data
    assert response.data.coverage.successful_reverse_queries == 5
    assert response.data.coverage.relevant_retrieved_reverse_query_ids == []
    assert response.data.coverage.uncovered_reverse_query_ids == [
        "R01",
        "R02",
        "R03",
        "R04",
        "R05",
    ]
    assert response.data.system_status == "inconclusive"


@pytest.mark.asyncio
async def test_name_only_reverse_pages_are_weak_when_claim_has_project_code(
    fixture_settings,
) -> None:
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("forward_live_stub", _rows("forward", 3)),
        reverse_backend=PlannedBackend("reverse_live_stub", _rows("reverse", 5)),
        fetcher=RecordingFetcher(
            {
                **{
                    _url("forward", number): _full_claim_text()
                    for number in range(1, 4)
                },
                **{
                    _url("reverse", number): (
                        f"{PROJECT_NAME}升级改造项目的一般公开信息。"
                    )
                    for number in range(1, 6)
                },
            }
        ),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        max_results_per_query=1,
        max_pages_to_fetch=8,
    )

    assert response.ok and response.data
    assert response.data.risk_signals == []
    assert response.data.coverage.relevant_retrieved_reverse_query_ids == []
    assert response.data.coverage.uncovered_reverse_query_ids == [
        "R01",
        "R02",
        "R03",
        "R04",
        "R05",
    ]
    assert response.data.system_status == "inconclusive"


@pytest.mark.asyncio
async def test_claim_identity_cannot_be_stitched_across_different_pages(
    fixture_settings,
) -> None:
    code_url = "https://code.gov.cn/notices/identity"
    name_url = "https://name.gov.cn/notices/identity"
    rows = [
        RawSearchResult("编号页", code_url, "候选"),
        RawSearchResult("名称页", name_url, "候选"),
    ]
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("forward_live_stub", [rows, rows, rows]),
        reverse_backend=PlannedBackend(
            "reverse_live_stub", [rows, rows, rows, rows, rows]
        ),
        fetcher=RecordingFetcher(
            {
                code_url: " ".join(
                    (
                        PROJECT_CODE,
                        PACKAGE_CODE,
                        PURCHASER,
                        SUPPLIER,
                        CREDIT_CODE,
                        CLAIMED_AMOUNT,
                        CLAIMED_DATE,
                        REGION,
                    )
                ),
                name_url: PROJECT_NAME,
            }
        ),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        max_results_per_query=2,
        max_pages_to_fetch=10,
    )

    assert response.ok and response.data
    observations = {item.field: item for item in response.data.field_observations}
    assert observations["project_name"].status == "not_evaluated_no_retrieved_pages"
    assert observations["project_code"].status == "not_evaluated_no_retrieved_pages"
    assert response.data.coverage.relevant_retrieved_forward_query_ids == []
    assert response.data.coverage.relevant_retrieved_reverse_query_ids == []
    assert response.data.system_status == "inconclusive"


@pytest.mark.asyncio
async def test_other_claim_fields_cannot_be_stitched_from_unrelated_page(
    fixture_settings,
) -> None:
    identity_url = "https://identity.gov.cn/notices/project"
    directory_url = "https://directory.gov.cn/companies/list"
    rows = [
        RawSearchResult("项目介绍", identity_url, "候选"),
        RawSearchResult("企业名录", directory_url, "候选"),
    ]
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("forward_live_stub", [rows, rows, rows]),
        reverse_backend=PlannedBackend(
            "reverse_live_stub", [rows, rows, rows, rows, rows]
        ),
        fetcher=RecordingFetcher(
            {
                identity_url: f"{PROJECT_NAME} {PROJECT_CODE} 普通项目介绍。",
                directory_url: f"{PACKAGE_CODE} {SUPPLIER} {CREDIT_CODE}",
            }
        ),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        max_results_per_query=2,
        max_pages_to_fetch=10,
    )

    assert response.ok and response.data
    observations = {item.field: item for item in response.data.field_observations}
    assert response.data.coverage.identity_coherent_page_count == 2
    assert observations["project_name"].status == "observed_in_retrieved_excerpt"
    assert observations["project_code"].status == "observed_in_retrieved_excerpt"
    assert observations["package_code"].status == "not_observed_in_retrieved_excerpts"
    assert observations["supplier_legal_name"].status == (
        "not_observed_in_retrieved_excerpts"
    )
    assert observations["unified_social_credit_code"].status == (
        "not_observed_in_retrieved_excerpts"
    )
    assert response.data.system_status == "inconclusive"


@pytest.mark.asyncio
async def test_supplier_cannot_be_bound_from_a_different_package_page(
    fixture_settings,
) -> None:
    package_one_url = "https://package1.gov.cn/notices/project"
    package_two_url = "https://package2.gov.cn/notices/project"
    rows = [
        RawSearchResult("包1公告", package_one_url, "候选"),
        RawSearchResult("包2公告", package_two_url, "候选"),
    ]
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("forward_live_stub", [rows, rows, rows]),
        reverse_backend=PlannedBackend(
            "reverse_live_stub", [rows, rows, rows, rows, rows]
        ),
        fetcher=RecordingFetcher(
            {
                package_one_url: " ".join(
                    (
                        PROJECT_NAME,
                        PROJECT_CODE,
                        PACKAGE_CODE,
                        PURCHASER,
                        CLAIMED_AMOUNT,
                        CLAIMED_DATE,
                        REGION,
                    )
                ),
                package_two_url: " ".join(
                    (PROJECT_NAME, PROJECT_CODE, "包2", SUPPLIER, CREDIT_CODE)
                ),
            }
        ),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        max_results_per_query=2,
        max_pages_to_fetch=10,
    )

    assert response.ok and response.data
    assert all(
        item.status == "observed_in_retrieved_excerpt"
        for item in response.data.field_observations
    )
    assert response.data.coverage.key_binding_page_count == 0
    assert response.data.system_status == "inconclusive"
    assert any("跨标包" in reason for reason in response.data.review_reasons)


@pytest.mark.asyncio
async def test_supplier_branch_name_cannot_impersonate_claimed_legal_entity(
    fixture_settings,
) -> None:
    supplier = "上海科技有限公司"
    content = " ".join(
        (
            PROJECT_NAME,
            PROJECT_CODE,
            PACKAGE_CODE,
            PURCHASER,
            f"中标人为{supplier}分公司",
            CREDIT_CODE,
            CLAIMED_AMOUNT,
            CLAIMED_DATE,
            REGION,
        )
    )
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("forward_live_stub", _rows("forward", 3)),
        reverse_backend=PlannedBackend("reverse_live_stub", _rows("reverse", 5)),
        fetcher=RecordingFetcher(
            {
                _url(phase, number): content
                for phase, count in (("forward", 3), ("reverse", 5))
                for number in range(1, count + 1)
            }
        ),
    )
    arguments = _claim_arguments()
    arguments["supplier_legal_name"] = supplier

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **arguments,
        max_results_per_query=1,
        max_pages_to_fetch=10,
    )

    assert response.ok and response.data
    supplier_observation = next(
        item
        for item in response.data.field_observations
        if item.field == "supplier_legal_name"
    )
    assert supplier_observation.status == "not_observed_in_retrieved_excerpts"
    assert response.data.coverage.key_binding_page_count == 0
    assert response.data.system_status == "inconclusive"


@pytest.mark.asyncio
async def test_duplicate_phase_urls_cannot_hide_unfetched_unique_candidates(
    fixture_settings,
) -> None:
    common = [f"https://common{index}.gov.cn/notice" for index in range(1, 6)]
    novel = [f"https://novel{index}.gov.cn/notice" for index in range(1, 7)]

    def row(url: str) -> RawSearchResult:
        return RawSearchResult("公开候选", url, "候选摘要")

    forward_rows = [
        [row(common[0]), row(common[3])],
        [row(common[1]), row(common[4])],
        [row(common[2])],
    ]
    reverse_rows = [
        [row(common[0]), row(novel[0]), row(novel[5])],
        [row(common[1]), row(novel[1])],
        [row(common[2]), row(novel[2])],
        [row(common[3]), row(novel[3])],
        [row(common[4]), row(novel[4])],
    ]
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("forward_live_stub", forward_rows),
        reverse_backend=PlannedBackend("reverse_live_stub", reverse_rows),
        fetcher=RecordingFetcher(
            {url: _full_claim_text() for url in [*common, *novel]}
        ),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        max_results_per_query=3,
        max_pages_to_fetch=10,
    )

    assert response.ok and response.data
    fetched_urls = {page.url for page in response.data.pages}
    assert set(novel[:5]) <= fetched_urls
    assert novel[5] not in fetched_urls
    assert response.data.coverage.unique_selected_for_fetch_count == 10
    assert response.data.coverage.unfetched_candidate_count == 1
    assert response.data.coverage.budget_exhausted is True
    assert response.data.system_status == "inconclusive"


@pytest.mark.asyncio
async def test_rank_four_backend_candidate_is_preserved_and_fetched(
    fixture_settings,
) -> None:
    urls = [f"https://rank{rank}.gov.cn/notice" for rank in range(1, 5)]
    rows = [
        RawSearchResult(f"第{rank}名", url, "候选")
        for rank, url in enumerate(urls, start=1)
    ]
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("forward_live_stub", [rows, rows, rows]),
        reverse_backend=PlannedBackend(
            "reverse_live_stub", [rows, rows, rows, rows, rows]
        ),
        fetcher=RecordingFetcher(
            {
                **{url: _full_claim_text() for url in urls[:3]},
                urls[3]: f"{_full_claim_text()} 本项目现发布终止公告。",
            }
        ),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        max_results_per_query=3,
        max_pages_to_fetch=10,
    )

    assert response.ok and response.data
    first_run = response.data.search_runs[0].response.data
    assert first_run and first_run.result_count == 3
    assert first_run.overflow_count == 1
    assert first_run.overflow_results[0].url == urls[3]
    assert urls[3] in {page.url for page in response.data.pages}
    assert response.data.coverage.search_result_limit_hit_query_ids == [
        "F01",
        "F02",
        "F03",
        "R01",
        "R02",
        "R03",
        "R04",
        "R05",
    ]
    assert any(item.keyword == "终止" for item in response.data.risk_signals)
    assert response.data.system_status == "risk_signals_found"


@pytest.mark.asyncio
async def test_search_result_limit_hit_blocks_mechanical_completion(
    fixture_settings,
) -> None:
    urls = [f"https://neutral{rank}.gov.cn/notice" for rank in range(1, 5)]
    rows = [RawSearchResult("中性候选", url, "候选") for url in urls]
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("forward_live_stub", [rows, rows, rows]),
        reverse_backend=PlannedBackend(
            "reverse_live_stub", [rows, rows, rows, rows, rows]
        ),
        fetcher=RecordingFetcher({url: _full_claim_text() for url in urls}),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        max_results_per_query=3,
        max_pages_to_fetch=10,
    )

    assert response.ok and response.data
    assert response.data.coverage.search_result_limit_hit_query_ids
    assert response.data.system_status == "inconclusive"


@pytest.mark.asyncio
async def test_same_url_is_refetched_after_reverse_discovery_with_phase_provenance(
    fixture_settings,
) -> None:
    shared_url = "https://shared.gov.cn/notices/1"
    row = RawSearchResult(title="同一项目公告", url=shared_url, snippet="候选")
    service = OfficialSearchService(
        fixture_settings,
        backend=PlannedBackend("forward_live_stub", [[row], [row], [row]]),
        reverse_backend=PlannedBackend(
            "reverse_live_stub", [[row], [row], [row], [row], [row]]
        ),
        fetcher=RecordingFetcher({shared_url: _full_claim_text()}),
    )

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        max_results_per_query=1,
        max_pages_to_fetch=8,
    )

    assert response.ok and response.data
    assert len(response.data.pages) == 2
    forward_page, reverse_page = response.data.pages
    assert forward_page.url == reverse_page.url == shared_url
    assert forward_page.discovered_by_query_ids == ["F01", "F02", "F03"]
    assert reverse_page.discovered_by_query_ids == ["R01", "R02", "R03", "R04", "R05"]
    assert forward_page.fetch.data and reverse_page.fetch.data
    assert forward_page.fetch.data.fetched_at <= reverse_page.fetch.data.fetched_at


@pytest.mark.asyncio
async def test_offline_fixture_can_never_produce_completed_status(fixture_settings) -> None:
    service = OfficialSearchService(fixture_settings, fetcher=RecordingFetcher())

    response = await service.crosscheck_tender_information(
        PROJECT_NAME,
        **_claim_arguments(),
        domains=["gov.cn"],
        max_results_per_query=1,
        max_pages_to_fetch=4,
    )

    assert response.ok and response.data
    assert response.data.coverage.live_search_performed is False
    assert response.data.system_status == "offline_fixture_only"
    assert response.data.system_status != "two_pass_completed_pending_human"
    assert response.data.human_gate.manual_review_required is True
    assert response.data.human_gate.system_may_auto_approve is False
    assert any("不可作生产结论" in reason for reason in response.data.review_reasons)
