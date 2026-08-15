from __future__ import annotations

from dataclasses import replace

import pytest

from official_search_mcp.config import Settings


@pytest.fixture
def fixture_settings() -> Settings:
    return Settings(
        backend="fixture",
        default_domains=("gov.cn",),
        official_suffixes=("gov.cn",),
        allowed_domains=(),
        min_domain_interval_seconds=0.0,
        requests_per_minute=120,
    )


@pytest.fixture
def settings_factory(fixture_settings: Settings):
    def factory(**changes):
        return replace(fixture_settings, **changes)

    return factory
