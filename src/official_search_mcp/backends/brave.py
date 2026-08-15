from __future__ import annotations

from ..errors import BackendError
from .base import RawSearchResult, SearchBackend
from .http_json import get_json


class BraveSearchBackend(SearchBackend):
    name = "brave_search_api"
    is_live = True
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, timeout_seconds: float) -> None:
        if not api_key.strip():
            raise BackendError("missing_brave_api_key", "Brave 后端需要 API Key。")
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, max_results: int) -> list[RawSearchResult]:
        if len(query) > 400 or len(query.split()) > 50:
            raise BackendError(
                "brave_query_too_long",
                "Brave Search 查询超过后端的 400 字符或 50 词上限。",
            )
        request_count = min(max_results, 20)
        payload = await get_json(
            self.endpoint,
            params={"q": query, "count": request_count, "safesearch": "moderate"},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self._api_key,
            },
            timeout_seconds=self.timeout_seconds,
        )
        web = payload.get("web") if isinstance(payload, dict) else None
        rows = web.get("results") if isinstance(web, dict) else None
        if not isinstance(rows, list):
            raise BackendError(
                "search_backend_schema_error", "Brave Search JSON 缺少 web.results 数组。"
            )
        results: list[RawSearchResult] = []
        for row in rows[:request_count]:
            if not isinstance(row, dict):
                continue
            results.append(
                RawSearchResult(
                    title=str(row.get("title") or "").strip(),
                    url=str(row.get("url") or "").strip(),
                    snippet=str(row.get("description") or "").strip(),
                )
            )
        return results
