from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from ..errors import BackendError
from .base import RawSearchResult, SearchBackend


class FixtureSearchBackend(SearchBackend):
    name = "offline_fixture"
    is_live = False

    def __init__(self, fixture_path: Path | None = None) -> None:
        self.fixture_path = fixture_path

    def _load(self) -> list[dict[str, object]]:
        try:
            if self.fixture_path:
                raw = self.fixture_path.read_text(encoding="utf-8")
            else:
                raw = (
                    files("official_search_mcp")
                    .joinpath("fixtures/offline_search_results.json")
                    .read_text(encoding="utf-8")
                )
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise BackendError(
                "fixture_load_failed", "离线 fixture 无法读取；没有伪装成实时结果。"
            ) from exc
        if not isinstance(payload, list):
            raise BackendError("fixture_schema_error", "离线 fixture 顶层必须是数组。")
        return [row for row in payload if isinstance(row, dict)]

    async def search(self, query: str, max_results: int) -> list[RawSearchResult]:
        del query
        return [
            RawSearchResult(
                title=str(row.get("title") or "").strip(),
                url=str(row.get("url") or "").strip(),
                snippet=str(row.get("snippet") or "").strip(),
            )
            for row in self._load()[:max_results]
        ]
