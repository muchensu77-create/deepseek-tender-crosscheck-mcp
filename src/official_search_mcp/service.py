from __future__ import annotations

import asyncio
import hashlib
import html
import json
import math
import re
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from . import __version__
from .backends import create_search_backend
from .backends.base import SearchBackend
from .config import Settings
from .errors import OfficialSearchError, PolicyError
from .fetcher import ControlledFetcher
from .limits import RequestGate
from .models import (
    FetchAttempt,
    FetchResponse,
    SearchAndFetchData,
    SearchAndFetchResponse,
    SearchData,
    SearchResponse,
    SearchResult,
    TenderCoverage,
    TenderCrosscheckData,
    TenderCrosscheckResponse,
    TenderPageReview,
    TenderSearchRun,
    ToolError,
)
from .security import DomainPolicy, normalize_public_url
from .tender import (
    TenderQuerySpec,
    build_tender_query_plan,
    detect_risk_signals,
    is_key_binding_page,
    is_strongly_related_to_claim,
    observe_claim_fields,
    prepare_tender_claim,
)

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HTML_TAG = re.compile(r"<[^>]{0,500}>")


def _safe_text(value: str, limit: int) -> str:
    unescaped = html.unescape(_HTML_TAG.sub(" ", value))
    return re.sub(r"\s+", " ", unescaped).strip()[:limit]


def _tool_error(exc: OfficialSearchError) -> ToolError:
    return ToolError(
        code=exc.code,
        message=exc.message,
        retryable=exc.retryable,
        details=exc.details,
    )


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    expires_at: float
    domains: tuple[str, ...]


class CandidateRegistry:
    """Short-lived provenance registry that prevents model-invented fetch URLs."""

    def __init__(self, ttl_seconds: float = 15 * 60, max_entries: int = 500) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._urls: dict[str, CandidateRecord] = {}
        self._lock = asyncio.Lock()

    async def add(self, urls: list[str], domains: tuple[str, ...]) -> None:
        now = time.monotonic()
        async with self._lock:
            self._purge(now)
            for url in urls:
                existing = self._urls.get(url)
                combined_domains = (
                    (*existing.domains, *domains) if existing is not None else domains
                )
                self._urls[url] = CandidateRecord(
                    expires_at=now + self.ttl_seconds,
                    domains=self._narrowest_scope(url, combined_domains),
                )
            if len(self._urls) > self.max_entries:
                ordered = sorted(self._urls.items(), key=lambda item: item[1].expires_at)
                for url, _record in ordered[: len(self._urls) - self.max_entries]:
                    self._urls.pop(url, None)

    async def domains_for(self, url: str) -> tuple[str, ...] | None:
        now = time.monotonic()
        async with self._lock:
            self._purge(now)
            record = self._urls.get(url)
            return record.domains if record is not None else None

    def _purge(self, now: float) -> None:
        for url in [key for key, record in self._urls.items() if record.expires_at <= now]:
            self._urls.pop(url, None)

    @staticmethod
    def _narrowest_scope(url: str, domains: tuple[str, ...]) -> tuple[str, ...]:
        host = (urlsplit(url).hostname or "").lower()
        matching = tuple(
            dict.fromkeys(
                domain
                for domain in domains
                if host == domain or host.endswith(f".{domain}")
            )
        )
        if not matching:
            return domains
        maximum_labels = max(domain.count(".") for domain in matching)
        return tuple(
            domain for domain in matching if domain.count(".") == maximum_labels
        )


class OfficialSearchService:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        backend: SearchBackend | None = None,
        reverse_backend: SearchBackend | None = None,
        fetcher: ControlledFetcher | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.policy = DomainPolicy(
            official_suffixes=self.settings.official_suffixes,
            allowed_domains=self.settings.allowed_domains,
            default_domains=self.settings.default_domains,
        )
        self.gate = RequestGate(
            max_concurrency=self.settings.max_concurrency,
            requests_per_minute=self.settings.requests_per_minute,
            minimum_domain_interval_seconds=self.settings.min_domain_interval_seconds,
        )
        self.backend = backend or create_search_backend(self.settings)
        if reverse_backend is not None:
            self.reverse_backend = reverse_backend
        elif self.settings.reverse_backend:
            self.reverse_backend = create_search_backend(
                replace(self.settings, backend=self.settings.reverse_backend)
            )
        else:
            self.reverse_backend = self.backend
        self.fetcher = fetcher or ControlledFetcher(self.settings, self.policy, self.gate)
        self.candidates = CandidateRegistry()

    def _validate_query(self, query: str) -> str:
        normalized = query.strip()
        if not 2 <= len(normalized) <= 300:
            raise PolicyError("invalid_query", "查询长度必须在 2 到 300 个字符之间。")
        if _CONTROL_CHARS.search(normalized):
            raise PolicyError("invalid_query", "查询包含不允许的控制字符。")
        return normalized

    def _validate_max_results(self, max_results: int, *, auto_fetch: bool = False) -> int:
        maximum = min(self.settings.max_results, 3 if auto_fetch else self.settings.max_results)
        if not 1 <= max_results <= maximum:
            raise PolicyError(
                "invalid_max_results",
                f"max_results 必须在 1 到 {maximum} 之间。",
            )
        return max_results

    async def search_official_sources(
        self,
        query: str,
        domains: list[str] | None = None,
        max_results: int = 5,
    ) -> SearchResponse:
        return await self._search_with_backend(query, domains, max_results, self.backend)

    async def _search_with_backend(
        self,
        query: str,
        domains: list[str] | None,
        max_results: int,
        backend: SearchBackend,
        candidate_registry: CandidateRegistry | None = None,
    ) -> SearchResponse:
        warnings: list[str] = []
        try:
            normalized_query = self._validate_query(query)
            limit = self._validate_max_results(max_results)
            selected_domains = self.policy.resolve_requested_domains(domains)
            site_clause = " OR ".join(f"site:{domain}" for domain in selected_domains)
            effective_query = f"{normalized_query} ({site_clause})"
            queried_at = datetime.now(UTC)
            raw_limit = min(40, max(limit * 4, limit))
            try:
                async with asyncio.timeout(self.settings.search_timeout_seconds):
                    async with self.gate.operation():
                        raw_results = await backend.search(effective_query, raw_limit)
            except TimeoutError as exc:
                raise OfficialSearchError(
                    "search_timeout",
                    "搜索超过整个操作的时间上限。",
                    retryable=True,
                ) from exc

            results: list[SearchResult] = []
            overflow_results: list[SearchResult] = []
            seen: set[str] = set()
            filtered = 0
            for row in raw_results:
                try:
                    original_url = row.url.strip()
                    normalized_url = normalize_public_url(
                        original_url,
                        self.policy,
                        restrict_domains=selected_domains,
                    )
                except PolicyError:
                    filtered += 1
                    continue
                if normalized_url in seen:
                    continue
                seen.add(normalized_url)
                candidate = SearchResult(
                    rank=len(results) + len(overflow_results) + 1,
                    title=_safe_text(row.title, 500) or "（搜索后端未提供标题）",
                    original_url=original_url,
                    url=normalized_url,
                    snippet=_safe_text(row.snippet, 1_000),
                    queried_at=queried_at,
                    source_backend=row.source_backend or backend.name,
                    verification_status="candidate_unverified",
                )
                if len(results) < limit:
                    results.append(candidate)
                else:
                    overflow_results.append(candidate)
            all_valid_results = [*results, *overflow_results]
            result_limit_hit = bool(overflow_results) or len(raw_results) >= raw_limit
            registry = candidate_registry or self.candidates
            await registry.add(
                [result.url for result in all_valid_results], selected_domains
            )
            if filtered:
                warnings.append(f"已过滤 {filtered} 个越域、危险或格式无效的候选 URL。")
            if not results:
                warnings.append("搜索完成但没有返回符合授权域名策略的候选；没有生成 URL。")
            if result_limit_hit:
                warnings.append(
                    "搜索观察到超出主结果上限的候选或命中上游返回上限；"
                    "溢出 URL 已另存，不得宣称搜索完整。"
                )
            if not backend.is_live:
                warnings.append("当前是离线 fixture 后端，不代表本次运行进行了实时联网搜索。")
            if any(result.url.startswith("http://") for result in all_valid_results):
                warnings.append("部分候选仅提供明文 HTTP；抓取结果不能获得 TLS 身份保证。")
            return SearchResponse(
                ok=True,
                data=SearchData(
                    query=normalized_query,
                    effective_query=effective_query,
                    domains=list(selected_domains),
                    queried_at=queried_at,
                    source_backend=backend.name,
                    live_search_performed=backend.is_live,
                    result_count=len(results),
                    results=results,
                    raw_result_count=len(raw_results),
                    raw_result_limit=raw_limit,
                    overflow_count=len(overflow_results),
                    overflow_results=overflow_results,
                    result_limit_hit=result_limit_hit,
                ),
                warnings=warnings,
            )
        except OfficialSearchError as exc:
            return SearchResponse(ok=False, error=_tool_error(exc), warnings=warnings)
        except Exception as exc:
            error = ToolError(
                code="internal_error",
                message=f"搜索发生未预期错误（{type(exc).__name__}）；没有生成替代结果。",
                retryable=False,
            )
            return SearchResponse(ok=False, error=error, warnings=warnings)

    async def fetch_official_page(self, url: str) -> FetchResponse:
        return await self._fetch_official_page_from_registry(url, self.candidates)

    async def _fetch_official_page_from_registry(
        self,
        url: str,
        candidate_registry: CandidateRegistry,
    ) -> FetchResponse:
        warnings: list[str] = []
        try:
            normalized = normalize_public_url(url, self.policy)
            candidate_domains = await candidate_registry.domains_for(normalized)
            if not self.settings.allow_direct_fetch and candidate_domains is None:
                raise PolicyError(
                    "url_not_in_search_candidates",
                    "URL 不在本进程最近一次真实/fixture 搜索的候选集中；默认拒绝模型自造 URL。",
                    details={
                        "remedy": (
                            "先调用 search_official_sources，或由受信宿主显式开启 direct fetch。"
                        )
                    },
                )
            data, extraction_warnings = await self.fetcher.fetch(
                normalized,
                restrict_domains=(candidate_domains if candidate_domains is not None else None),
            )
            warnings.extend(extraction_warnings)
            return FetchResponse(ok=True, data=data, warnings=warnings)
        except OfficialSearchError as exc:
            return FetchResponse(ok=False, error=_tool_error(exc), warnings=warnings)
        except Exception as exc:
            return FetchResponse(
                ok=False,
                error=ToolError(
                    code="internal_error",
                    message=f"抓取发生未预期错误（{type(exc).__name__}）；没有补写正文。",
                    retryable=False,
                ),
                warnings=warnings,
            )

    async def search_and_fetch_official(
        self,
        query: str,
        domains: list[str] | None = None,
        max_results: int = 3,
    ) -> SearchAndFetchResponse:
        try:
            self._validate_max_results(max_results, auto_fetch=True)
        except OfficialSearchError as exc:
            return SearchAndFetchResponse(ok=False, error=_tool_error(exc))
        search = await self.search_official_sources(query, domains, max_results)
        if not search.ok or search.data is None:
            return SearchAndFetchResponse(
                ok=False,
                error=search.error,
                warnings=search.warnings,
            )
        pages: list[FetchAttempt] = []
        warnings = list(search.warnings)
        for source in search.data.results:
            fetched = await self.fetch_official_page(source.url)
            pages.append(FetchAttempt(source=source, fetch=fetched))
        successes = sum(1 for page in pages if page.fetch.ok)
        if successes < len(pages):
            warnings.append(
                f"{len(pages) - successes} 个候选读取失败；错误已逐项保留，没有伪造正文。"
            )
        return SearchAndFetchResponse(
            ok=bool(successes) or not pages,
            data=SearchAndFetchData(search=search.data, pages=pages),
            error=(
                None
                if successes or not pages
                else ToolError(
                    code="all_fetches_failed",
                    message="搜索产生候选，但全部页面读取失败。",
                    retryable=False,
                )
            ),
            warnings=warnings,
        )

    async def _run_tender_searches(
        self,
        specs: list[TenderQuerySpec],
        domains: list[str] | None,
        max_results: int,
        backend: SearchBackend,
        candidate_registry: CandidateRegistry,
    ) -> list[TenderSearchRun]:
        runs: list[TenderSearchRun] = []
        chunk_size = max(1, self.settings.max_concurrency)
        for start in range(0, len(specs), chunk_size):
            chunk = specs[start : start + chunk_size]
            responses = await asyncio.gather(
                *(
                    self._search_with_backend(
                        spec.query,
                        domains,
                        max_results,
                        backend,
                        candidate_registry,
                    )
                    for spec in chunk
                )
            )
            runs.extend(
                TenderSearchRun(
                    query_id=spec.query_id,
                    phase=spec.phase,
                    purpose=spec.purpose,
                    response=response,
                )
                for spec, response in zip(chunk, responses, strict=True)
            )
        return runs

    @staticmethod
    def _all_search_results(data: SearchData | None) -> list[SearchResult]:
        return [*data.results, *data.overflow_results] if data is not None else []

    @staticmethod
    def _select_tender_candidates(
        runs: list[TenderSearchRun],
        limit: int,
        *,
        excluded: set[str] | None = None,
    ) -> list[str]:
        excluded_urls = set(excluded or ())
        selected: list[str] = []
        indexes = [0] * len(runs)
        while len(selected) < limit:
            made_progress = False
            for run_index, run in enumerate(runs):
                results = OfficialSearchService._all_search_results(run.response.data)
                while indexes[run_index] < len(results):
                    candidate = results[indexes[run_index]].url
                    indexes[run_index] += 1
                    if candidate in excluded_urls or candidate in selected:
                        continue
                    selected.append(candidate)
                    made_progress = True
                    break
                if len(selected) >= limit:
                    break
            if not made_progress:
                break
        return selected

    async def _fetch_tender_candidates(
        self,
        urls: list[str],
        query_ids_by_url: dict[str, list[str]],
        candidate_registry: CandidateRegistry,
        *,
        evidence_offset: int,
    ) -> list[TenderPageReview]:
        pages: list[TenderPageReview] = []
        chunk_size = max(1, self.settings.max_concurrency)
        for start in range(0, len(urls), chunk_size):
            chunk = urls[start : start + chunk_size]
            responses = await asyncio.gather(
                *(
                    self._fetch_official_page_from_registry(url, candidate_registry)
                    for url in chunk
                )
            )
            for url, response in zip(chunk, responses, strict=True):
                pages.append(
                    TenderPageReview(
                        evidence_id=f"E{evidence_offset + len(pages) + 1:04d}",
                        url=url,
                        discovered_by_query_ids=query_ids_by_url.get(url, []),
                        fetch=response,
                    )
                )
        return pages

    async def crosscheck_tender_information(
        self,
        project_name: str,
        *,
        procurement_regime: str = "unknown",
        project_code: str | None = None,
        package_code: str | None = None,
        purchaser: str | None = None,
        supplier_legal_name: str | None = None,
        unified_social_credit_code: str | None = None,
        claimed_amount: str | None = None,
        claimed_date: str | None = None,
        region: str | None = None,
        notice_type: str = "unknown",
        claimed_role: str = "unknown",
        domains: list[str] | None = None,
        max_results_per_query: int = 3,
        max_pages_to_fetch: int = 10,
    ) -> TenderCrosscheckResponse:
        """Run a server-enforced forward search followed by an independent reverse pass."""
        warnings: list[str] = []
        run_id = str(uuid4())
        started_at = datetime.now(UTC)
        current_phase = "validation"
        try:
            if not 1 <= max_results_per_query <= min(3, self.settings.max_results):
                raise PolicyError(
                    "invalid_tender_search_budget",
                    "max_results_per_query 必须在 1 到 3 之间。",
                )
            if not 4 <= max_pages_to_fetch <= 10:
                raise PolicyError(
                    "invalid_tender_fetch_budget",
                    "max_pages_to_fetch 必须在 4 到 10 之间。",
                )
            forward_budget = max(2, max_pages_to_fetch // 2)
            reverse_budget = max_pages_to_fetch - forward_budget
            concurrency = self.settings.max_concurrency
            minimum_timeout = (
                (
                    math.ceil(3 / concurrency)
                    + math.ceil(5 / concurrency)
                )
                * self.settings.search_timeout_seconds
                + (
                    math.ceil(forward_budget / concurrency)
                    + math.ceil(reverse_budget / concurrency)
                )
                * self.settings.fetch_timeout_seconds
                + 10.0
            )
            if self.settings.tender_workflow_timeout_seconds < minimum_timeout:
                raise PolicyError(
                    "tender_timeout_budget_conflict",
                    "招投标全流程 deadline 小于当前并发和固定预算的最坏情况上限。",
                    details={
                        "configured_seconds": self.settings.tender_workflow_timeout_seconds,
                        "minimum_required_seconds": minimum_timeout,
                        "max_concurrency": concurrency,
                    },
                )
            raw_input_payload = {
                "project_name": project_name,
                "procurement_regime": procurement_regime,
                "project_code": project_code,
                "package_code": package_code,
                "purchaser": purchaser,
                "supplier_legal_name": supplier_legal_name,
                "unified_social_credit_code": unified_social_credit_code,
                "claimed_amount": claimed_amount,
                "claimed_date": claimed_date,
                "region": region,
                "notice_type": notice_type,
                "claimed_role": claimed_role,
                "domains": domains,
                "max_results_per_query": max_results_per_query,
                "max_pages_to_fetch": max_pages_to_fetch,
            }
            raw_input_sha256 = hashlib.sha256(
                json.dumps(
                    raw_input_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            selected_domains = self.policy.resolve_requested_domains(domains)
            claim = prepare_tender_claim(
                project_name=project_name,
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
            )
            forward_specs, reverse_specs = build_tender_query_plan(claim)
            input_payload = {
                "claim": claim.model_dump(mode="json"),
                "domains": list(selected_domains),
                "max_results_per_query": max_results_per_query,
                "max_pages_to_fetch": max_pages_to_fetch,
            }
            normalized_input_sha256 = hashlib.sha256(
                json.dumps(
                    input_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            plan_payload = {
                "workflow_version": "cn_tender_bidirectional/1.0",
                "forward": [asdict(spec) for spec in forward_specs],
                "reverse": [asdict(spec) for spec in reverse_specs],
                "domains": list(selected_domains),
                "forward_backend": self.backend.name,
                "reverse_backend": self.reverse_backend.name,
                "max_results_per_query": max_results_per_query,
                "max_pages_to_fetch": max_pages_to_fetch,
            }
            plan_sha256 = hashlib.sha256(
                json.dumps(
                    plan_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            run_candidates = CandidateRegistry()

            async with asyncio.timeout(self.settings.tender_workflow_timeout_seconds):
                # Freeze all positive discovery before starting the fixed reverse pass.
                # All eight searches finish before page downloads, so slow forward pages
                # cannot starve the reverse audit phase.
                current_phase = "forward_search"
                forward_runs = await self._run_tender_searches(
                    forward_specs,
                    list(selected_domains),
                    max_results_per_query,
                    self.backend,
                    run_candidates,
                )
                current_phase = "reverse_search"
                reverse_runs = await self._run_tender_searches(
                    reverse_specs,
                    list(selected_domains),
                    max_results_per_query,
                    self.reverse_backend,
                    run_candidates,
                )
                all_runs = [*forward_runs, *reverse_runs]
                current_phase = "forward_fetch"
                forward_urls = self._select_tender_candidates(
                    forward_runs, forward_budget
                )
                forward_query_map = self._tender_query_ids_by_url(forward_runs)
                pages = await self._fetch_tender_candidates(
                    forward_urls,
                    forward_query_map,
                    run_candidates,
                    evidence_offset=0,
                )

                current_phase = "reverse_fetch"
                reverse_query_map = self._tender_query_ids_by_url(reverse_runs)
                reverse_urls = self._select_tender_candidates(
                    reverse_runs,
                    reverse_budget,
                    excluded=set(forward_urls),
                )
                if len(reverse_urls) < reverse_budget:
                    reverse_urls.extend(
                        self._select_tender_candidates(
                            reverse_runs,
                            reverse_budget - len(reverse_urls),
                            excluded=set(reverse_urls),
                        )
                    )
                pages.extend(
                    await self._fetch_tender_candidates(
                        reverse_urls,
                        reverse_query_map,
                        run_candidates,
                        evidence_offset=len(pages),
                    )
                )

            for run in all_runs:
                warnings.extend(
                    f"{run.query_id}: {warning}" for warning in run.response.warnings
                )
            for page in pages:
                warnings.extend(
                    f"{page.evidence_id}: {warning}" for warning in page.fetch.warnings
                )

            risk_signals = detect_risk_signals(claim, pages)
            successful_forward = sum(run.response.ok for run in forward_runs)
            successful_reverse = sum(run.response.ok for run in reverse_runs)
            all_candidates = {
                result.url
                for run in all_runs
                for result in self._all_search_results(run.response.data)
            }
            fetched_successes = [
                page
                for page in pages
                if page.fetch.ok and page.fetch.data is not None
            ]
            fully_retrieved_pages = [
                page
                for page in fetched_successes
                if page.fetch.data is not None
                and page.fetch.data.retrieval_status == "retrieved"
                and not page.fetch.data.content_truncated
            ]
            relevant_query_ids: set[str] = set()
            identity_coherent_pages: list[TenderPageReview] = []
            for page in fully_retrieved_pages:
                data = page.fetch.data
                if data is None:
                    continue
                text = f"{data.title} {data.content_excerpt}"
                if is_strongly_related_to_claim(claim, text):
                    identity_coherent_pages.append(page)
                    relevant_query_ids.update(page.discovered_by_query_ids)
            observations = observe_claim_fields(claim, identity_coherent_pages)
            key_binding_urls = {
                page.url
                for page in identity_coherent_pages
                if page.fetch.data is not None
                and is_key_binding_page(
                    claim,
                    f"{page.fetch.data.title} {page.fetch.data.content_excerpt}",
                )
            }
            forward_query_ids = [spec.query_id for spec in forward_specs]
            reverse_query_ids = [spec.query_id for spec in reverse_specs]
            relevant_forward_ids = [
                query_id for query_id in forward_query_ids if query_id in relevant_query_ids
            ]
            relevant_reverse_ids = [
                query_id for query_id in reverse_query_ids if query_id in relevant_query_ids
            ]
            result_limit_hit_ids = [
                run.query_id
                for run in all_runs
                if run.response.data is not None and run.response.data.result_limit_hit
            ]
            content_hash_urls: dict[str, list[str]] = {}
            official_hosts: set[str] = set()
            for page in fetched_successes:
                data = page.fetch.data
                if data is None:
                    continue
                content_hash_urls.setdefault(data.content_sha256, []).append(page.url)
                host = urlsplit(data.final_url).hostname
                if host:
                    official_hosts.add(host.lower())
            duplicate_groups = [
                urls for urls in content_hash_urls.values() if len(urls) > 1
            ]
            backends = sorted(
                {
                    run.response.data.source_backend
                    for run in all_runs
                    if run.response.data is not None
                }
            )
            live_search_performed = bool(backends) and all(
                run.response.data.live_search_performed
                for run in all_runs
                if run.response.data is not None
            )
            fetched_unique_urls = {page.url for page in pages}
            unfetched_candidates = all_candidates - fetched_unique_urls
            coverage = TenderCoverage(
                required_forward_queries=len(forward_specs),
                successful_forward_queries=successful_forward,
                required_reverse_queries=len(reverse_specs),
                successful_reverse_queries=successful_reverse,
                candidate_count=len(all_candidates),
                selected_for_fetch_count=len(pages),
                unique_selected_for_fetch_count=len(fetched_unique_urls),
                unfetched_candidate_count=len(unfetched_candidates),
                fetched_success_count=len(fetched_successes),
                fetched_failure_count=len(pages) - len(fetched_successes),
                fully_retrieved_page_count=len(fully_retrieved_pages),
                degraded_or_no_text_page_count=(
                    len(fetched_successes) - len(fully_retrieved_pages)
                ),
                identity_coherent_page_count=len(identity_coherent_pages),
                key_binding_page_count=len(key_binding_urls),
                relevant_retrieved_forward_query_ids=relevant_forward_ids,
                relevant_retrieved_reverse_query_ids=relevant_reverse_ids,
                uncovered_forward_query_ids=[
                    query_id
                    for query_id in forward_query_ids
                    if query_id not in relevant_query_ids
                ],
                uncovered_reverse_query_ids=[
                    query_id
                    for query_id in reverse_query_ids
                    if query_id not in relevant_query_ids
                ],
                search_result_limit_hit_query_ids=result_limit_hit_ids,
                distinct_official_hosts=sorted(official_hosts),
                distinct_content_hashes=len(content_hash_urls),
                duplicate_content_hash_groups=duplicate_groups,
                distinct_search_backends=backends,
                different_reverse_backend_configured=(
                    self.reverse_backend.name != self.backend.name
                ),
                live_search_performed=live_search_performed,
                budget_exhausted=bool(unfetched_candidates),
            )
            review_reasons = self._tender_review_reasons(
                claim,
                forward_runs,
                reverse_runs,
                pages,
                observations,
                risk_signals,
                coverage,
            )
            system_status = self._tender_system_status(
                claim,
                forward_runs,
                reverse_runs,
                observations,
                risk_signals,
                coverage,
            )
            search_times = [
                run.response.data.queried_at
                for run in all_runs
                if run.response.data is not None
            ]
            cutoff_at = max(search_times, default=started_at)
            completed_at = datetime.now(UTC)
            data = TenderCrosscheckData(
                run_id=run_id,
                server_version=__version__,
                started_at=started_at,
                cutoff_at=cutoff_at,
                completed_at=completed_at,
                raw_input_sha256=raw_input_sha256,
                normalized_input_sha256=normalized_input_sha256,
                plan_sha256=plan_sha256,
                claim=claim,
                domains=list(selected_domains),
                search_runs=all_runs,
                pages=pages,
                field_observations=observations,
                risk_signals=risk_signals,
                coverage=coverage,
                system_status=system_status,
                review_reasons=review_reasons,
            )
            all_searches_failed = successful_forward + successful_reverse == 0
            return TenderCrosscheckResponse(
                ok=not all_searches_failed,
                data=data,
                error=(
                    ToolError(
                        code="all_tender_searches_failed",
                        message="正向和逆向搜索均失败；已保留每次失败，禁止自动判断。",
                        retryable=True,
                    )
                    if all_searches_failed
                    else None
                ),
                warnings=warnings,
            )
        except TimeoutError:
            return TenderCrosscheckResponse(
                ok=False,
                error=ToolError(
                    code="tender_workflow_timeout",
                    message="招投标双向复核超过全流程 deadline；不得把未完成流程当作无风险。",
                    retryable=True,
                    details={
                        "run_id": run_id,
                        "started_at": started_at.isoformat(),
                        "interrupted_phase": current_phase,
                    },
                ),
                warnings=warnings,
            )
        except OfficialSearchError as exc:
            return TenderCrosscheckResponse(
                ok=False,
                error=_tool_error(exc),
                warnings=warnings,
            )
        except Exception as exc:
            return TenderCrosscheckResponse(
                ok=False,
                error=ToolError(
                    code="internal_error",
                    message=(
                        f"招投标双向复核发生未预期错误（{type(exc).__name__}）；"
                        "没有生成替代结论。"
                    ),
                    retryable=False,
                ),
                warnings=warnings,
            )

    @staticmethod
    def _tender_query_ids_by_url(runs: list[TenderSearchRun]) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        for run in runs:
            for result in OfficialSearchService._all_search_results(run.response.data):
                query_ids = mapping.setdefault(result.url, [])
                if run.query_id not in query_ids:
                    query_ids.append(run.query_id)
        return mapping

    @staticmethod
    def _tender_review_reasons(
        claim,
        forward_runs,
        reverse_runs,
        pages,
        observations,
        risk_signals,
        coverage,
    ) -> list[str]:
        reasons = [
            "MCP 只生成待人工复核的证据台账；最终审批必须由实名人员在平台签署。",
            "字段命中只做规范化精确子串观察，未自动证明同一标包、主体角色或金额/日期语义绑定。",
            "搜索截止时间之后仍可能发布更正、投诉处理或终止公告，使用前应重新执行逆向复查。",
        ]
        if not claim.project_code:
            reasons.append("未提供项目/采购编号，同名项目误合并风险较高。")
        if claim.procurement_regime == "unknown":
            reasons.append(
                "未区分政府采购与依法必须招标制度线，来源优先级和公告字段须人工确认。"
            )
        if not claim.package_code:
            reasons.append("未提供标包编号，无法排除同一项目不同标包串证据。")
        if not claim.supplier_legal_name:
            reasons.append("未提供供应商法定全称，无法可靠绑定中标/成交主体。")
        if not claim.unified_social_credit_code:
            reasons.append("未提供统一社会信用代码，无法排除同名、母子公司或历史名称混淆。")
        if claim.notice_type == "unknown":
            reasons.append("公告类型未知；招标、候选、中标、合同和验收阶段不能互相替代。")
        if claim.claimed_role == "unknown":
            reasons.append("声称的主体角色未知，不能由名称在页面中出现推断其为中标人。")
        if (
            claim.claimed_role == "completed_performance"
            and claim.notice_type != "acceptance_record"
        ):
            reasons.append("声称已履约但未声明验收记录；中标或合同公告不能证明履约完成。")
        failed_query_ids = [
            run.query_id for run in [*forward_runs, *reverse_runs] if not run.response.ok
        ]
        if failed_query_ids:
            reasons.append(f"查询失败或超时：{', '.join(failed_query_ids)}。")
        empty_reverse = [
            run.query_id
            for run in reverse_runs
            if run.response.ok
            and run.response.data is not None
            and not run.response.data.results
        ]
        if empty_reverse:
            reasons.append(
                f"逆向查询无候选：{', '.join(empty_reverse)}；零结果不代表风险不存在。"
            )
        failed_pages = [page.evidence_id for page in pages if not page.fetch.ok]
        if failed_pages:
            reasons.append(
                f"候选页面读取失败：{', '.join(failed_pages)}；robots/403/超时均按证据缺口处理。"
            )
        degraded_pages = [
            page.evidence_id
            for page in pages
            if page.fetch.data is not None
            and page.fetch.data.retrieval_status != "retrieved"
        ]
        if degraded_pages:
            reasons.append(
                f"页面正文未完整取得：{', '.join(degraded_pages)}；动态页或空正文不能形成否定结论。"
            )
        truncated_pages = [
            page.evidence_id
            for page in pages
            if page.fetch.data is not None and page.fetch.data.content_truncated
        ]
        if truncated_pages:
            reasons.append(
                f"页面正文因返回上限被截断：{', '.join(truncated_pages)}；"
                "未展示部分可能含更正或终止信息，只能人工补证。"
            )
        unobserved = [
            item.field
            for item in observations
            if item.status != "observed_in_retrieved_excerpt"
        ]
        if unobserved:
            reasons.append(
                f"申报值未在已取片段中观察到或无法评估：{', '.join(unobserved)}。"
            )
        if risk_signals:
            reasons.append("发现更正/终止/投诉等文本风险信号，必须核对公告关联和时间线。")
        if coverage.uncovered_forward_query_ids:
            reasons.append(
                "正向查询没有取得同页绑定项目名称与强身份锚点的完整可读正文："
                + ", ".join(coverage.uncovered_forward_query_ids)
                + "。"
            )
        if coverage.uncovered_reverse_query_ids:
            reasons.append(
                "逆向查询没有取得同页绑定项目名称与强身份锚点的完整可读正文："
                + ", ".join(coverage.uncovered_reverse_query_ids)
                + "；搜索候选存在也不能视为反查完成。"
            )
        if coverage.search_result_limit_hit_query_ids:
            reasons.append(
                "搜索候选命中展示/上游返回上限："
                + ", ".join(coverage.search_result_limit_hit_query_ids)
                + "；溢出候选已留痕，但不能宣称召回完整。"
            )
        if coverage.key_binding_page_count == 0:
            reasons.append(
                "没有完整可读页面在同页绑定项目名称、项目编号、申报标包和"
                "供应商法定名称+信用代码；禁止跨标包、分公司或母子公司拼接主体证据。"
            )
        if coverage.candidate_count == 0:
            reasons.append("所有查询均未观察到合规候选；这不等于项目或风险记录不存在。")
        if coverage.duplicate_content_hash_groups:
            reasons.append("存在相同内容哈希的多个 URL，已按重复证据展示，不能累计多数票。")
        if coverage.budget_exhausted:
            reasons.append("候选数超过抓取预算，未读取候选仍是明确覆盖缺口。")
        if not coverage.different_reverse_backend_configured:
            reasons.append("正向与逆向使用同一搜索后端，索引遗漏风险不具备后端独立性。")
        else:
            reasons.append(
                "正反向配置了不同后端名称，但这只增加发现路径，不等于独立官方证据或独立索引。"
            )
        if not coverage.live_search_performed:
            reasons.append("至少部分查询不是实时联网结果或没有成功结果，不可作生产结论。")
        return reasons

    @staticmethod
    def _tender_system_status(
        claim,
        forward_runs,
        reverse_runs,
        observations,
        risk_signals,
        coverage,
    ) -> str:
        if coverage.distinct_search_backends and not coverage.live_search_performed:
            return "offline_fixture_only"
        if risk_signals:
            return "risk_signals_found"
        if not all(
            (
                claim.project_code,
                claim.package_code,
                claim.supplier_legal_name,
                claim.unified_social_credit_code,
            )
        ):
            return "insufficient_identifiers"
        if coverage.candidate_count == 0:
            return (
                "no_candidate_observed"
                if all(run.response.ok for run in [*forward_runs, *reverse_runs])
                else "inconclusive"
            )
        reverse_has_results = all(
            run.response.ok
            and run.response.data is not None
            and bool(run.response.data.results)
            for run in reverse_runs
        )
        all_fields_observed = all(
            item.status == "observed_in_retrieved_excerpt" for item in observations
        )
        complete = (
            all(run.response.ok for run in [*forward_runs, *reverse_runs])
            and reverse_has_results
            and coverage.fetched_success_count > 0
            and coverage.fetched_failure_count == 0
            and coverage.degraded_or_no_text_page_count == 0
            and coverage.key_binding_page_count > 0
            and not coverage.search_result_limit_hit_query_ids
            and not coverage.uncovered_forward_query_ids
            and not coverage.uncovered_reverse_query_ids
            and not coverage.budget_exhausted
            and all_fields_observed
        )
        return "two_pass_completed_pending_human" if complete else "inconclusive"

    def safe_config_summary(self) -> dict[str, Any]:
        summary = self.settings.public_summary()
        summary["resolved_backend"] = self.backend.name
        summary["live_backend"] = self.backend.is_live
        summary["resolved_reverse_backend"] = self.reverse_backend.name
        summary["live_reverse_backend"] = self.reverse_backend.is_live
        return summary
