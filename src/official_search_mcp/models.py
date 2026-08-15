from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolError(StrictModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class SearchResult(StrictModel):
    rank: int = Field(ge=1)
    title: str
    original_url: str
    url: str
    snippet: str
    queried_at: datetime
    source_backend: str
    verification_status: Literal["candidate_unverified"] = "candidate_unverified"


class SearchData(StrictModel):
    query: str
    effective_query: str
    domains: list[str]
    queried_at: datetime
    source_backend: str
    live_search_performed: bool
    result_count: int
    results: list[SearchResult]
    raw_result_count: int
    raw_result_limit: int
    overflow_count: int
    overflow_results: list[SearchResult]
    result_limit_hit: bool
    evidence_notice: str = (
        "搜索结果仅是候选线索；URL、标题和摘要尚未构成已核验证据，必须读取原网页并人工判断。"
    )


class SearchResponse(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    ok: bool
    data: SearchData | None = None
    error: ToolError | None = None
    warnings: list[str] = Field(default_factory=list)


class FetchData(StrictModel):
    requested_url: str
    final_url: str
    canonical_url: str
    redirect_chain: list[str]
    title: str
    content_excerpt: str
    extracted_char_count: int = Field(ge=0)
    content_truncated: bool
    fetched_at: datetime
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str
    bytes_read: int = Field(ge=0)
    source_backend: Literal["controlled_http_fetch"] = "controlled_http_fetch"
    extraction_method: str
    retrieval_status: Literal["retrieved", "degraded_dynamic", "no_extractable_text"]
    robots_policy: Literal["allowed"] = "allowed"
    evidence_status: Literal["retrieved_not_verified"] = "retrieved_not_verified"
    evidence_notice: str = (
        "页面已按该 URL 抓取并计算哈希，但内容真实性、主体身份和业务结论仍需核验。"
    )


class FetchResponse(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    ok: bool
    data: FetchData | None = None
    error: ToolError | None = None
    warnings: list[str] = Field(default_factory=list)


class FetchAttempt(StrictModel):
    source: SearchResult
    fetch: FetchResponse


class SearchAndFetchData(StrictModel):
    search: SearchData
    pages: list[FetchAttempt]


class SearchAndFetchResponse(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    ok: bool
    data: SearchAndFetchData | None = None
    error: ToolError | None = None
    warnings: list[str] = Field(default_factory=list)


TenderNoticeType = Literal[
    "tender_notice",
    "candidate_notice",
    "award_result",
    "contract_notice",
    "acceptance_record",
    "unknown",
]

TenderClaimedRole = Literal[
    "bidder",
    "candidate",
    "winner",
    "contractor",
    "completed_performance",
    "unknown",
]

TenderRegime = Literal["government_procurement", "mandatory_tender", "unknown"]


class TenderClaim(StrictModel):
    project_name: str
    procurement_regime: TenderRegime = "unknown"
    project_code: str | None = None
    package_code: str | None = None
    purchaser: str | None = None
    supplier_legal_name: str | None = None
    unified_social_credit_code: str | None = None
    claimed_amount: str | None = None
    claimed_date: str | None = None
    region: str | None = None
    notice_type: TenderNoticeType = "unknown"
    claimed_role: TenderClaimedRole = "unknown"


class TenderSearchRun(StrictModel):
    query_id: str
    phase: Literal["forward", "reverse"]
    purpose: str
    response: SearchResponse


class TenderPageReview(StrictModel):
    evidence_id: str
    url: str
    discovered_by_query_ids: list[str]
    fetch: FetchResponse


class TenderFieldObservation(StrictModel):
    field: Literal[
        "project_name",
        "project_code",
        "package_code",
        "purchaser",
        "supplier_legal_name",
        "unified_social_credit_code",
        "claimed_amount",
        "claimed_date",
        "region",
    ]
    expected_value: str
    status: Literal[
        "observed_in_retrieved_excerpt",
        "not_observed_in_retrieved_excerpts",
        "not_evaluated_no_retrieved_pages",
    ]
    supporting_evidence_ids: list[str]
    supporting_urls: list[str]
    match_rule: Literal[
        "nfkc_alphanumeric_exact_substring_v1",
        "identifier_token_boundary_v1",
        "date_identifier_boundary_v1",
        "bounded_literal_v1",
        "legal_name_exact_without_extension_v1",
    ] = "nfkc_alphanumeric_exact_substring_v1"
    notice: str = (
        "这里只表示该值出现在同页绑定项目名称与强身份锚点的已抓取片段中；"
        "不证明主体角色、标包、金额语义或法律事实。"
    )


class TenderRiskSignal(StrictModel):
    category: Literal[
        "correction_or_change",
        "failed_cancelled_or_terminated",
        "challenge_complaint_or_supervision",
        "contract_or_acceptance_adverse",
    ]
    keyword: str
    matched_anchor_fields: list[Literal["project_code", "project_name"]]
    evidence_id: str
    url: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signal_status: Literal["text_indicator_requires_manual_review"] = (
        "text_indicator_requires_manual_review"
    )
    notice: str = "关键词命中不是已确认冲突；必须人工核对公告类型、项目/标包关联和发布时间。"


class TenderCoverage(StrictModel):
    required_forward_queries: int
    successful_forward_queries: int
    required_reverse_queries: int
    successful_reverse_queries: int
    candidate_count: int
    selected_for_fetch_count: int
    unique_selected_for_fetch_count: int
    unfetched_candidate_count: int
    fetched_success_count: int
    fetched_failure_count: int
    fully_retrieved_page_count: int
    degraded_or_no_text_page_count: int
    identity_coherent_page_count: int
    key_binding_page_count: int
    relevant_retrieved_forward_query_ids: list[str]
    relevant_retrieved_reverse_query_ids: list[str]
    uncovered_forward_query_ids: list[str]
    uncovered_reverse_query_ids: list[str]
    search_result_limit_hit_query_ids: list[str]
    distinct_official_hosts: list[str]
    distinct_content_hashes: int
    duplicate_content_hash_groups: list[list[str]]
    distinct_search_backends: list[str]
    different_reverse_backend_configured: bool
    live_search_performed: bool
    budget_exhausted: bool


class TenderHumanGate(StrictModel):
    manual_review_required: Literal[True] = True
    system_may_auto_approve: Literal[False] = False
    platform_must_record_reviewer_identity: Literal[True] = True
    permitted_human_outcomes: list[
        Literal["approved_by_human", "rejected_by_human", "needs_more_evidence"]
    ] = Field(
        default_factory=lambda: [
            "approved_by_human",
            "rejected_by_human",
            "needs_more_evidence",
        ]
    )


class TenderCrosscheckData(StrictModel):
    run_id: str
    server_version: str
    workflow_version: Literal["cn_tender_bidirectional/1.0"] = "cn_tender_bidirectional/1.0"
    started_at: datetime
    cutoff_at: datetime
    completed_at: datetime
    raw_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    claim: TenderClaim
    domains: list[str]
    search_runs: list[TenderSearchRun]
    pages: list[TenderPageReview]
    field_observations: list[TenderFieldObservation]
    risk_signals: list[TenderRiskSignal]
    coverage: TenderCoverage
    system_status: Literal[
        "two_pass_completed_pending_human",
        "risk_signals_found",
        "inconclusive",
        "no_candidate_observed",
        "insufficient_identifiers",
        "offline_fixture_only",
    ]
    review_reasons: list[str]
    human_gate: TenderHumanGate = Field(default_factory=TenderHumanGate)
    evidence_notice: str = (
        "本报告只记录固定规则下的正向搜索、逆向搜索和已抓取片段。它不构成自动核验通过，"
        "不能由中标公告推断合同签订、履约、验收或付款；网页正文始终按不可信证据处理。"
    )


class TenderCrosscheckResponse(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    ok: bool
    data: TenderCrosscheckData | None = None
    error: ToolError | None = None
    warnings: list[str] = Field(default_factory=list)
