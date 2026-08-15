from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import aiohttp
from protego import Protego

from .config import Settings
from .errors import OfficialSearchError, PolicyError
from .extraction import ExtractedDocument
from .limits import RequestGate
from .models import FetchData
from .security import DomainPolicy, SafePublicResolver, normalize_public_url

_PAGE_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}
_ROBOTS_MAX_BYTES = 256 * 1024
_EXTRACTION_MAX_OUTPUT_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class DownloadedResponse:
    status: int
    final_url: str
    redirect_chain: tuple[str, ...]
    content_type: str
    charset: str | None
    body: bytes


@dataclass(frozen=True, slots=True)
class RobotsDecision:
    allowed: bool
    crawl_delay: float


@dataclass(frozen=True, slots=True)
class RobotsRecord:
    rules: Protego | None
    crawl_delay: float
    expires_at: float


async def _read_limited_body(response: aiohttp.ClientResponse, max_bytes: int) -> bytes:
    if response.content_length and response.content_length > max_bytes:
        raise OfficialSearchError("response_too_large", "目标响应超过允许的字节上限。")
    body = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        body.extend(chunk)
        if len(body) > max_bytes:
            raise OfficialSearchError("response_too_large", "目标响应超过允许的字节上限。")
    return bytes(body)


class ControlledFetcher:
    def __init__(
        self,
        settings: Settings,
        policy: DomainPolicy,
        gate: RequestGate,
    ) -> None:
        self.settings = settings
        self.policy = policy
        self.gate = gate
        self._robots_cache: dict[str, RobotsRecord] = {}
        self._robots_lock = asyncio.Lock()
        self._operation_semaphore = asyncio.Semaphore(settings.max_concurrency)

    def _next_redirect(
        self,
        current: str,
        location: str,
        restrict_domains: tuple[str, ...] | None = None,
    ) -> str:
        next_url = normalize_public_url(
            urljoin(current, location),
            self.policy,
            restrict_domains=restrict_domains,
        )
        if current.startswith("https://") and next_url.startswith("http://"):
            raise PolicyError(
                "https_downgrade_rejected",
                "拒绝从 HTTPS 重定向降级到明文 HTTP。",
            )
        return next_url

    async def _download(
        self,
        url: str,
        *,
        max_bytes: int,
        crawl_delay: float = 0.0,
        enforce_robots: bool = False,
        restrict_domains: tuple[str, ...] | None = None,
    ) -> DownloadedResponse:
        current = normalize_public_url(url, self.policy, restrict_domains=restrict_domains)
        chain = [current]
        timeout = aiohttp.ClientTimeout(
            total=self.settings.fetch_timeout_seconds,
            connect=min(5.0, self.settings.fetch_timeout_seconds),
            sock_read=min(10.0, self.settings.fetch_timeout_seconds),
        )
        connector = aiohttp.TCPConnector(
            resolver=SafePublicResolver(),
            use_dns_cache=False,
            limit=1,
            force_close=True,
        )
        headers = {
            "User-Agent": self.settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
            "Accept-Encoding": "identity",
        }
        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                trust_env=False,
                headers=headers,
                auto_decompress=True,
                cookie_jar=aiohttp.DummyCookieJar(),
            ) as session:
                for hop in range(self.settings.max_redirects + 1):
                    request_delay = crawl_delay
                    if enforce_robots:
                        robots = await self._robots_decision(
                            current, restrict_domains=restrict_domains
                        )
                        if not robots.allowed:
                            raise PolicyError(
                                "robots_disallowed",
                                "robots.txt 不允许该自动客户端访问此 URL。",
                            )
                        request_delay = max(request_delay, robots.crawl_delay)
                    async with self.gate.request(current, request_delay):
                        async with session.get(current, allow_redirects=False) as response:
                            actual_url = normalize_public_url(
                                str(response.url),
                                self.policy,
                                restrict_domains=restrict_domains,
                            )
                            if actual_url != current:
                                raise PolicyError(
                                    "outbound_url_mismatch",
                                    "HTTP 客户端实际请求 URL 与安全规范 URL 不一致。",
                                )
                            if 300 <= response.status < 400:
                                location = response.headers.get("Location")
                                if not location:
                                    raise OfficialSearchError(
                                        "redirect_without_location",
                                        "目标返回重定向但没有 Location。",
                                    )
                                if hop >= self.settings.max_redirects:
                                    raise OfficialSearchError(
                                        "too_many_redirects",
                                        "重定向次数超过安全上限。",
                                    )
                                current = self._next_redirect(
                                    current,
                                    location,
                                    restrict_domains=restrict_domains,
                                )
                                chain.append(current)
                                continue

                            body = await _read_limited_body(response, max_bytes)
                            return DownloadedResponse(
                                status=response.status,
                                final_url=current,
                                redirect_chain=tuple(chain),
                                content_type=response.headers.get("Content-Type", ""),
                                charset=response.charset,
                                body=body,
                            )
        except (OfficialSearchError, PolicyError):
            raise
        except TimeoutError as exc:
            raise OfficialSearchError(
                "fetch_timeout", "公开页面读取超时。", retryable=True
            ) from exc
        except aiohttp.ClientConnectorCertificateError as exc:
            raise OfficialSearchError(
                "tls_validation_failed", "目标网站 TLS 证书校验失败；未绕过校验。"
            ) from exc
        except aiohttp.ClientError as exc:
            raise OfficialSearchError(
                "fetch_network_error",
                f"公开页面连接失败（{type(exc).__name__}）。",
                retryable=True,
            ) from exc
        raise OfficialSearchError("fetch_internal_error", "抓取流程未返回响应。")

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlsplit(url)
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

    async def _robots_decision(
        self,
        url: str,
        restrict_domains: tuple[str, ...] | None = None,
    ) -> RobotsDecision:
        origin = self._origin(url)
        now = time.monotonic()
        async with self._robots_lock:
            cached = self._robots_cache.get(origin)
            if cached and cached.expires_at > now:
                return RobotsDecision(
                    allowed=cached.rules is None
                    or cached.rules.can_fetch(url, "OfficialSearchMCP"),
                    crawl_delay=cached.crawl_delay,
                )

        robots_url = f"{origin}/robots.txt"
        try:
            response = await self._download(
                robots_url,
                max_bytes=_ROBOTS_MAX_BYTES,
                restrict_domains=restrict_domains,
            )
        except OfficialSearchError as exc:
            raise PolicyError(
                "robots_unavailable",
                "无法安全读取 robots.txt，按 fail-closed 策略停止。",
                retryable=exc.retryable,
                details={"cause": exc.code},
            ) from exc

        if response.status in {404, 410}:
            record = RobotsRecord(
                rules=None,
                crawl_delay=0.0,
                expires_at=time.monotonic() + self.settings.robots_cache_seconds,
            )
        elif not 200 <= response.status < 300:
            raise PolicyError(
                "robots_access_blocked",
                f"robots.txt 返回 HTTP {response.status}，按 fail-closed 策略停止。",
                retryable=response.status >= 500 or response.status == 429,
            )
        else:
            try:
                text = response.body.decode(response.charset or "utf-8", errors="replace")
                rules = Protego.parse(text)
                delay = rules.crawl_delay("OfficialSearchMCP")
                if delay is None:
                    delay = rules.crawl_delay("*") or 0.0
                request_rate = rules.request_rate("OfficialSearchMCP")
                if request_rate is None:
                    request_rate = rules.request_rate("*")
                if request_rate is not None:
                    if request_rate.requests <= 0 or request_rate.seconds <= 0:
                        raise ValueError("invalid robots request-rate")
                    delay = max(delay, request_rate.seconds / request_rate.requests)
                visit_time = rules.visit_time("OfficialSearchMCP")
                if visit_time is None:
                    visit_time = rules.visit_time("*")
                if visit_time is not None:
                    raise PolicyError(
                        "robots_visit_time_unsupported",
                        (
                            "robots.txt 声明了 Visit-time；本原型无法可靠执行该时段，"
                            "按 fail-closed 停止。"
                        ),
                    )
                record = RobotsRecord(
                    rules=rules,
                    crawl_delay=float(delay),
                    expires_at=time.monotonic() + self.settings.robots_cache_seconds,
                )
            except (LookupError, TypeError, ValueError) as exc:
                raise PolicyError(
                    "robots_parse_failed",
                    "robots.txt 无法可靠解析，按 fail-closed 策略停止。",
                ) from exc
        async with self._robots_lock:
            self._robots_cache[origin] = record
        return RobotsDecision(
            allowed=record.rules is None or record.rules.can_fetch(url, "OfficialSearchMCP"),
            crawl_delay=record.crawl_delay,
        )

    async def _extract_isolated(
        self,
        body: bytes,
        *,
        final_url: str,
        content_type: str,
        declared_charset: str | None,
    ) -> ExtractedDocument:
        header = json.dumps(
            {
                "final_url": final_url,
                "content_type": content_type,
                "declared_charset": declared_charset,
                "max_chars": self.settings.max_content_chars,
                "official_suffixes": list(self.policy.official_suffixes),
                "allowed_domains": list(self.policy.allowed_domains),
                "default_domains": list(self.policy.default_domains),
            },
            ensure_ascii=False,
        ).encode()
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "official_search_mcp.extraction_worker",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            )
            stdout, _stderr = await process.communicate(header + b"\n" + body)
        except BaseException:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            raise
        if len(stdout) > _EXTRACTION_MAX_OUTPUT_BYTES:
            raise OfficialSearchError(
                "extraction_output_too_large",
                "正文抽取子进程返回超过允许上限。",
            )
        try:
            decoded = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OfficialSearchError(
                "extraction_invalid_response",
                "正文抽取子进程未返回合法 JSON。",
            ) from exc
        if not isinstance(decoded, dict) or decoded.get("ok") is not True:
            error_type = (
                decoded.get("error_type", "unknown")
                if isinstance(decoded, dict)
                else "invalid_envelope"
            )
            raise OfficialSearchError(
                "extraction_failed",
                f"正文抽取子进程失败（{error_type}）；没有补写内容。",
            )
        item = decoded.get("document")
        if not isinstance(item, dict):
            raise OfficialSearchError("extraction_invalid_response", "正文抽取结果缺少 document。")
        try:
            title = item["title"]
            canonical_url = item["canonical_url"]
            content = item["content"]
            extracted_char_count = item["extracted_char_count"]
            content_truncated = item["content_truncated"]
            method = item["method"]
            retrieval_status = item["retrieval_status"]
            warnings = item["warnings"]
            if (
                not all(
                    isinstance(value, str)
                    for value in (title, canonical_url, content, method, retrieval_status)
                )
                or not isinstance(warnings, list)
                or any(not isinstance(warning, str) for warning in warnings)
                or not isinstance(extracted_char_count, int)
                or isinstance(extracted_char_count, bool)
                or extracted_char_count < len(content)
                or not isinstance(content_truncated, bool)
            ):
                raise TypeError("invalid extraction field type")
            if retrieval_status not in {
                "retrieved",
                "degraded_dynamic",
                "no_extractable_text",
            }:
                raise ValueError("invalid retrieval status")
            canonical_url = normalize_public_url(canonical_url, self.policy)
        except (KeyError, TypeError, ValueError, PolicyError) as exc:
            raise OfficialSearchError(
                "extraction_invalid_response", "正文抽取结果字段无效。"
            ) from exc
        return ExtractedDocument(
            title=title[:500],
            canonical_url=canonical_url,
            content=content[: self.settings.max_content_chars],
            extracted_char_count=extracted_char_count,
            content_truncated=content_truncated,
            method=method[:100],
            retrieval_status=retrieval_status,
            warnings=tuple(warnings[:20]),
        )

    async def fetch(
        self,
        url: str,
        *,
        restrict_domains: tuple[str, ...] | None = None,
    ) -> tuple[FetchData, list[str]]:
        try:
            async with asyncio.timeout(self.settings.fetch_timeout_seconds):
                async with self._operation_semaphore:
                    return await self._fetch_within_deadline(url, restrict_domains=restrict_domains)
        except TimeoutError as exc:
            raise OfficialSearchError(
                "fetch_timeout",
                "公开页面读取超过整个操作的时间上限。",
                retryable=True,
            ) from exc

    async def _fetch_within_deadline(
        self,
        url: str,
        *,
        restrict_domains: tuple[str, ...] | None,
    ) -> tuple[FetchData, list[str]]:
        requested = normalize_public_url(url, self.policy, restrict_domains=restrict_domains)
        response = await self._download(
            requested,
            max_bytes=self.settings.max_response_bytes,
            enforce_robots=True,
            restrict_domains=restrict_domains,
        )
        if response.status in {401, 403, 451}:
            raise OfficialSearchError(
                "access_blocked",
                f"目标返回 HTTP {response.status}；不会绕过登录、访问控制或法律限制。",
            )
        if response.status == 429:
            raise OfficialSearchError(
                "remote_rate_limited", "目标网站正在限流；未自动规避。", retryable=True
            )
        if response.status >= 400:
            raise OfficialSearchError(
                "fetch_http_error",
                f"目标页面返回 HTTP {response.status}。",
                retryable=response.status >= 500,
            )
        media_type = response.content_type.split(";", 1)[0].strip().lower()
        if media_type not in _PAGE_CONTENT_TYPES:
            raise OfficialSearchError(
                "unsupported_content_type",
                f"原型只读取 HTML/XHTML/纯文本，目标类型为 {media_type or 'unknown'}。",
            )
        document = await self._extract_isolated(
            response.body,
            final_url=response.final_url,
            content_type=response.content_type,
            declared_charset=response.charset,
        )
        fetched_at = datetime.now(UTC)
        data = FetchData(
            requested_url=requested,
            final_url=response.final_url,
            canonical_url=document.canonical_url,
            redirect_chain=list(response.redirect_chain),
            title=document.title,
            content_excerpt=document.content,
            extracted_char_count=document.extracted_char_count,
            content_truncated=document.content_truncated,
            fetched_at=fetched_at,
            content_sha256=hashlib.sha256(response.body).hexdigest(),
            content_type=media_type,
            bytes_read=len(response.body),
            extraction_method=document.method,
            retrieval_status=document.retrieval_status,
            robots_policy="allowed",
            evidence_status="retrieved_not_verified",
        )
        return data, list(document.warnings)
