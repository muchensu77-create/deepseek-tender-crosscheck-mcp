from __future__ import annotations

from urllib.parse import urljoin, urlsplit

from ..errors import BackendError
from .base import RawSearchResult, SearchBackend
from .http_json import get_json


class SearXNGSearchBackend(SearchBackend):
    name = "searxng"
    is_live = True

    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        normalized = base_url.strip()
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise BackendError("invalid_searxng_url", "SearXNG 配置必须是有效的 http(s) URL。")
        if parsed.username or parsed.password:
            raise BackendError("invalid_searxng_url", "SearXNG URL 不得内嵌用户名或密码。")
        if parsed.query or parsed.fragment:
            raise BackendError("invalid_searxng_url", "SearXNG URL 不得包含 query 或 fragment。")
        self.endpoint = urljoin(normalized.rstrip("/") + "/", "search")
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, max_results: int) -> list[RawSearchResult]:
        payload = await get_json(
            self.endpoint,
            params={
                "q": query,
                "format": "json",
                "language": "zh-CN",
                "safesearch": 1,
            },
            headers={"Accept": "application/json"},
            timeout_seconds=self.timeout_seconds,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise BackendError("search_backend_schema_error", "SearXNG JSON 缺少 results 数组。")
        results: list[RawSearchResult] = []
        for row in payload["results"][:max_results]:
            if not isinstance(row, dict):
                continue
            results.append(
                RawSearchResult(
                    title=str(row.get("title") or "").strip(),
                    url=str(row.get("url") or "").strip(),
                    snippet=str(row.get("content") or "").strip(),
                )
            )
        return results
