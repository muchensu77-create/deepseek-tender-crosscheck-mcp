from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_OFFICIAL_SUFFIXES = (
    "gov.cn",
    "gov.hk",
    "gov.mo",
    "gov.tw",
    "gov",
    "gov.uk",
    "go.jp",
    "go.kr",
    "gc.ca",
    "gouv.fr",
    "bund.de",
    "europa.eu",
)


def _csv(value: str | None, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    value = default if raw is None else int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _number(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    value = default if raw is None else float(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True, slots=True)
class Settings:
    """Operator-controlled settings. Secrets are excluded from repr output."""

    backend: str = "auto"
    reverse_backend: str | None = None
    ddgs_backends: str = "yahoo,duckduckgo,yandex"
    searxng_url: str | None = None
    brave_api_key: str | None = field(default=None, repr=False)
    allowed_domains: tuple[str, ...] = ()
    default_domains: tuple[str, ...] = ("gov.cn", "gov.hk", "gov.mo")
    official_suffixes: tuple[str, ...] = DEFAULT_OFFICIAL_SUFFIXES
    allow_direct_fetch: bool = False
    max_results: int = 10
    search_timeout_seconds: float = 12.0
    tender_workflow_timeout_seconds: float = 150.0
    fetch_timeout_seconds: float = 15.0
    max_response_bytes: int = 2 * 1024 * 1024
    max_content_chars: int = 8_000
    max_redirects: int = 3
    max_concurrency: int = 3
    requests_per_minute: int = 30
    min_domain_interval_seconds: float = 1.0
    robots_cache_seconds: int = 3_600
    fixture_path: Path | None = None
    user_agent: str = "OfficialSearchMCP/0.2 (public-official-source-retrieval)"

    @classmethod
    def from_env(cls) -> Settings:
        fixture = os.getenv("OFFICIAL_SEARCH_FIXTURE_PATH")
        return cls(
            backend=os.getenv("OFFICIAL_SEARCH_BACKEND", "auto").strip().lower(),
            reverse_backend=(
                os.getenv("OFFICIAL_SEARCH_REVERSE_BACKEND", "").strip().lower() or None
            ),
            ddgs_backends=os.getenv(
                "OFFICIAL_SEARCH_DDGS_BACKENDS", "yahoo,duckduckgo,yandex"
            ).strip(),
            searxng_url=os.getenv("OFFICIAL_SEARCH_SEARXNG_URL") or None,
            brave_api_key=os.getenv("OFFICIAL_SEARCH_BRAVE_API_KEY") or None,
            allowed_domains=_csv(os.getenv("OFFICIAL_SEARCH_ALLOWED_DOMAINS")),
            default_domains=_csv(
                os.getenv("OFFICIAL_SEARCH_DEFAULT_DOMAINS"),
                ("gov.cn", "gov.hk", "gov.mo"),
            ),
            allow_direct_fetch=_boolean("OFFICIAL_SEARCH_ALLOW_DIRECT_FETCH", False),
            max_results=_integer("OFFICIAL_SEARCH_MAX_RESULTS", 10, 1, 20),
            search_timeout_seconds=_number(
                "OFFICIAL_SEARCH_SEARCH_TIMEOUT_SECONDS", 12.0, 1.0, 60.0
            ),
            tender_workflow_timeout_seconds=_number(
                "OFFICIAL_SEARCH_TENDER_TIMEOUT_SECONDS", 150.0, 30.0, 300.0
            ),
            fetch_timeout_seconds=_number("OFFICIAL_SEARCH_FETCH_TIMEOUT_SECONDS", 15.0, 1.0, 60.0),
            max_response_bytes=_integer(
                "OFFICIAL_SEARCH_MAX_RESPONSE_BYTES", 2 * 1024 * 1024, 32_768, 10 * 1024 * 1024
            ),
            max_content_chars=_integer("OFFICIAL_SEARCH_MAX_CONTENT_CHARS", 8_000, 500, 50_000),
            max_redirects=_integer("OFFICIAL_SEARCH_MAX_REDIRECTS", 3, 0, 5),
            max_concurrency=_integer("OFFICIAL_SEARCH_MAX_CONCURRENCY", 3, 1, 8),
            requests_per_minute=_integer("OFFICIAL_SEARCH_REQUESTS_PER_MINUTE", 30, 1, 120),
            min_domain_interval_seconds=_number(
                "OFFICIAL_SEARCH_MIN_DOMAIN_INTERVAL_SECONDS", 1.0, 0.0, 10.0
            ),
            fixture_path=Path(fixture).expanduser().resolve() if fixture else None,
        )

    def public_summary(self) -> dict[str, object]:
        """Return settings safe to display; never includes credentials."""
        return {
            "backend": self.backend,
            "reverse_backend": self.reverse_backend,
            "ddgs_backends": self.ddgs_backends,
            "searxng_configured": bool(self.searxng_url),
            "brave_api_key_configured": bool(self.brave_api_key),
            "allowed_domains": list(self.allowed_domains),
            "default_domains": list(self.default_domains),
            "allow_direct_fetch": self.allow_direct_fetch,
            "max_results": self.max_results,
            "tender_workflow_timeout_seconds": self.tender_workflow_timeout_seconds,
            "fetch_timeout_seconds": self.fetch_timeout_seconds,
            "max_response_bytes": self.max_response_bytes,
            "max_redirects": self.max_redirects,
            "max_concurrency": self.max_concurrency,
            "requests_per_minute": self.requests_per_minute,
        }
