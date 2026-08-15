from __future__ import annotations

import socket

import pytest

from official_search_mcp.errors import OfficialSearchError, PolicyError
from official_search_mcp.limits import SlidingWindowRateLimiter
from official_search_mcp.security import (
    DomainPolicy,
    SafePublicResolver,
    host_matches_domain,
    normalize_domain,
    normalize_public_url,
    validate_global_ip,
)


@pytest.fixture
def policy() -> DomainPolicy:
    return DomainPolicy(
        official_suffixes=("gov.cn",),
        allowed_domains=("example.com",),
        default_domains=("gov.cn",),
    )


def test_normalize_domain_handles_url_case_trailing_dot_and_idna() -> None:
    assert normalize_domain("HTTPS://WWW.GOV.CN./") == "www.gov.cn"
    assert normalize_domain("例子.中国") == "xn--fsqu00a.xn--fiqs8s"


@pytest.mark.parametrize(
    "value",
    [
        "127.0.0.1",
        "2130706433",
        "http://user:pass@gov.cn",
        "gov.cn:8443",
        "evil OR x.gov.cn",
        "bad_label.gov.cn",
        "-bad.gov.cn",
        "bad-.gov.cn",
        "gov.cn/path",
        "https://gov.cn/?q=1",
        "https://gov.cn/#fragment",
    ],
)
def test_normalize_domain_rejects_ambiguous_or_credentialed_hosts(value: str) -> None:
    with pytest.raises(PolicyError):
        normalize_domain(value)


def test_domain_boundary_matching() -> None:
    assert host_matches_domain("www.gov.cn", "gov.cn")
    assert not host_matches_domain("gov.cn.evil.example", "gov.cn")
    assert not host_matches_domain("evilgov.cn", "gov.cn")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "data:text/plain,hello",
        "gopher://www.gov.cn/",
        "https://user:pass@www.gov.cn/",
        "https://www.gov.cn:444/",
        "https://www.gov.cn:80/",
        "http://www.gov.cn:443/",
        "https://127.0.0.1/",
        "https://www.gov.cn\\@evil.example/",
    ],
)
def test_url_policy_rejects_unsafe_syntax(url: str, policy: DomainPolicy) -> None:
    with pytest.raises(PolicyError):
        normalize_public_url(url, policy)


def test_url_policy_normalizes_and_strips_fragment(policy: DomainPolicy) -> None:
    result = normalize_public_url("HTTPS://WWW.GOV.CN./a?q=1#fragment", policy)
    assert result == "https://www.gov.cn/a?q=1"


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("https://www.gov.cn/public/../private", "https://www.gov.cn/private"),
        ("https://www.gov.cn/public/%2e%2e/private", "https://www.gov.cn/private"),
        ("https://www.gov.cn/a?x=%7e", "https://www.gov.cn/a?x=~"),
        ("https://www.gov.cn/a?x=%2F", "https://www.gov.cn/a?x=/"),
    ],
)
def test_url_policy_matches_aiohttp_canonicalization(
    raw: str, canonical: str, policy: DomainPolicy
) -> None:
    assert normalize_public_url(raw, policy) == canonical


def test_url_canonicalization_is_idempotent_and_rechecks_length(
    policy: DomainPolicy,
) -> None:
    canonical = normalize_public_url("https://www.gov.cn/中文/公开", policy)
    assert normalize_public_url(canonical, policy) == canonical
    with pytest.raises(PolicyError, match="2048"):
        normalize_public_url("https://www.gov.cn/" + "中" * 300, policy)


def test_requested_domains_can_only_narrow_operator_policy(policy: DomainPolicy) -> None:
    assert policy.resolve_requested_domains(["https://www.gov.cn/"]) == ("www.gov.cn",)
    with pytest.raises(PolicyError, match="未由"):
        policy.resolve_requested_domains(["attacker.example"])


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "224.0.0.1",
        "0.0.0.0",  # noqa: S104 - test vector, never used as a bind address
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
    ],
)
def test_non_global_addresses_are_rejected(address: str) -> None:
    with pytest.raises(PolicyError):
        validate_global_ip(address)


def test_public_address_is_accepted() -> None:
    assert validate_global_ip("93.184.216.34") == "93.184.216.34"


@pytest.mark.asyncio
async def test_resolver_rejects_mixed_public_private_dns(monkeypatch) -> None:
    loop = __import__("asyncio").get_running_loop()

    async def fake_getaddrinfo(*args, **kwargs):
        del args, kwargs
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(PolicyError):
        await SafePublicResolver().resolve("www.gov.cn", 443)


@pytest.mark.asyncio
async def test_local_rate_limiter_rejects_excess_without_network() -> None:
    limiter = SlidingWindowRateLimiter(1)
    await limiter.acquire()
    with pytest.raises(OfficialSearchError) as caught:
        await limiter.acquire()
    assert getattr(caught.value, "code", None) == "rate_limit_exceeded"
