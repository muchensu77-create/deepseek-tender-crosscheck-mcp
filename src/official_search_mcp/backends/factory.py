from __future__ import annotations

from ..config import Settings
from ..errors import BackendError
from .base import SearchBackend
from .brave import BraveSearchBackend
from .ddgs import DDGSSearchBackend
from .fixture import FixtureSearchBackend
from .searxng import SearXNGSearchBackend


def create_search_backend(settings: Settings) -> SearchBackend:
    selected = settings.backend
    if selected == "auto":
        if settings.searxng_url:
            selected = "searxng"
        elif settings.brave_api_key:
            selected = "brave"
        else:
            selected = "ddgs"
    if selected == "ddgs":
        return DDGSSearchBackend(settings.search_timeout_seconds, settings.ddgs_backends)
    if selected == "searxng":
        if not settings.searxng_url:
            raise BackendError("missing_searxng_url", "选择 searxng 后端时必须配置 URL。")
        return SearXNGSearchBackend(settings.searxng_url, settings.search_timeout_seconds)
    if selected == "brave":
        if not settings.brave_api_key:
            raise BackendError("missing_brave_api_key", "选择 brave 后端时必须配置 API Key。")
        return BraveSearchBackend(settings.brave_api_key, settings.search_timeout_seconds)
    if selected == "fixture":
        return FixtureSearchBackend(settings.fixture_path)
    raise BackendError(
        "unknown_search_backend",
        "搜索后端必须是 auto、ddgs、searxng、brave 或 fixture。",
    )
