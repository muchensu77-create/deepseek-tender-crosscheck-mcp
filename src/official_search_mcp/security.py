from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Sequence
from urllib.parse import SplitResult, urlsplit, urlunsplit

from aiohttp.abc import AbstractResolver
from yarl import URL

from .errors import PolicyError

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_NUMERIC_HOST = re.compile(r"^[0-9.]+$")
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ALLOWED_SCHEMES = {"http", "https"}


def normalize_domain(value: str) -> str:
    """Normalize an operator/request domain to lower-case ASCII IDNA form."""
    raw = value.strip()
    if not raw:
        raise PolicyError("invalid_domain", "域名不能为空。")
    if _CONTROL_CHARS.search(raw) or "\\" in raw:
        raise PolicyError("invalid_domain", "域名包含控制字符或反斜杠。")
    if raw.startswith("*."):
        raw = raw[2:]

    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise PolicyError(
            "invalid_domain",
            "域名参数不得包含路径、查询字符串或片段。",
        )
    if parsed.username or parsed.password:
        raise PolicyError("invalid_domain", "域名配置不得包含用户名或密码。")
    try:
        if parsed.port is not None:
            raise PolicyError("invalid_domain", "域名配置不得包含端口。")
    except ValueError as exc:
        raise PolicyError("invalid_domain", "域名端口格式无效。") from exc

    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise PolicyError("invalid_domain", "无法从输入中识别域名。")
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise PolicyError("invalid_domain", "域名 IDNA 编码无效。") from exc
    labels = ascii_host.split(".")
    if len(ascii_host) > 253 or any(
        not label or len(label) > 63 or not _DNS_LABEL.fullmatch(label)
        for label in labels
    ):
        raise PolicyError("invalid_domain", "域名长度或标签格式无效。")
    try:
        ipaddress.ip_address(ascii_host)
    except ValueError:
        pass
    else:
        raise PolicyError("invalid_domain", "必须使用域名，不能把 IP 地址列入允许清单。")
    if _NUMERIC_HOST.fullmatch(ascii_host):
        raise PolicyError("invalid_domain", "不接受非标准数字形式的主机名。")
    return ascii_host


def host_matches_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


class DomainPolicy:
    def __init__(
        self,
        *,
        official_suffixes: Sequence[str],
        allowed_domains: Sequence[str],
        default_domains: Sequence[str],
    ) -> None:
        self.official_suffixes = tuple(normalize_domain(item) for item in official_suffixes)
        self.allowed_domains = tuple(normalize_domain(item) for item in allowed_domains)
        self.default_domains = tuple(normalize_domain(item) for item in default_domains)
        for domain in self.default_domains:
            if not self.is_authorized(domain):
                raise PolicyError(
                    "unauthorized_default_domain",
                    f"默认搜索域名未获授权：{domain}",
                )

    @property
    def authorized_domains(self) -> tuple[str, ...]:
        return self.official_suffixes + self.allowed_domains

    def is_authorized(self, host: str) -> bool:
        normalized = normalize_domain(host)
        return any(host_matches_domain(normalized, domain) for domain in self.authorized_domains)

    def resolve_requested_domains(self, domains: Sequence[str] | None) -> tuple[str, ...]:
        selected = (
            self.default_domains
            if not domains
            else tuple(normalize_domain(item) for item in domains)
        )
        if not selected:
            raise PolicyError("domains_required", "至少需要一个获授权的官网域名。")
        if len(selected) > 8:
            raise PolicyError("too_many_domains", "单次最多允许 8 个域名。")
        deduplicated = tuple(dict.fromkeys(selected))
        unauthorized = [domain for domain in deduplicated if not self.is_authorized(domain)]
        if unauthorized:
            raise PolicyError(
                "domain_not_authorized",
                "请求包含未由内置政府规则或操作者允许清单授权的域名。",
                details={"domains": unauthorized},
            )
        return deduplicated

    def url_host_is_allowed(self, host: str, restrict_domains: Sequence[str] | None = None) -> bool:
        normalized = normalize_domain(host)
        domains = tuple(restrict_domains) if restrict_domains else self.authorized_domains
        return any(host_matches_domain(normalized, domain) for domain in domains)


def _normalized_netloc(host: str, port: int | None) -> str:
    rendered = f"[{host}]" if ":" in host else host
    return f"{rendered}:{port}" if port is not None else rendered


def normalize_public_url(
    value: str,
    policy: DomainPolicy,
    *,
    restrict_domains: Sequence[str] | None = None,
) -> str:
    """Validate URL syntax/domain policy. DNS is validated at connect time."""
    raw = value.strip()
    if not raw or len(raw) > 2_048:
        raise PolicyError("invalid_url", "URL 为空或长度超过 2048 字符。")
    if _CONTROL_CHARS.search(raw) or "\\" in raw:
        raise PolicyError("invalid_url", "URL 包含控制字符或反斜杠。")
    try:
        parsed: SplitResult = urlsplit(raw)
    except ValueError as exc:
        raise PolicyError("invalid_url", "URL 解析失败。") from exc
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise PolicyError("scheme_not_allowed", "只允许 http:// 或 https:// 公网 URL。")
    if parsed.username or parsed.password:
        raise PolicyError("userinfo_not_allowed", "URL 不得包含用户名或密码。")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise PolicyError("invalid_url", "URL 缺少主机名。")
    try:
        ascii_host = host.encode("idna").decode("ascii")
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise PolicyError("invalid_url", "URL 主机名或端口无效。") from exc
    try:
        ipaddress.ip_address(ascii_host)
    except ValueError:
        pass
    else:
        raise PolicyError("ip_literal_not_allowed", "URL 不得直接使用 IP 地址。")
    if _NUMERIC_HOST.fullmatch(ascii_host):
        raise PolicyError("invalid_url", "URL 不接受非标准数字形式的主机名。")
    expected_port = 80 if scheme == "http" else 443
    if port is not None and port != expected_port:
        raise PolicyError(
            "port_not_allowed",
            "HTTP 只允许端口 80，HTTPS 只允许端口 443。",
        )
    if not policy.url_host_is_allowed(ascii_host, restrict_domains):
        raise PolicyError(
            "domain_not_authorized",
            "URL 不属于内置政府域名或操作者显式允许的域名。",
            details={"host": ascii_host},
        )
    path = parsed.path or "/"
    rendered = urlunsplit((scheme, _normalized_netloc(ascii_host, port), path, parsed.query, ""))
    try:
        # aiohttp uses yarl for the actual outbound request. Canonicalizing here makes
        # candidate registration, robots matching, redirect evidence, and wire semantics
        # agree for dot segments and percent-encoded path/query characters.
        canonical = str(URL(rendered))
    except ValueError as exc:
        raise PolicyError("invalid_url", "URL 无法按出站客户端语义规范化。") from exc
    if len(canonical) > 2_048:
        raise PolicyError("invalid_url", "URL 规范化后长度超过 2048 字符。")
    return canonical


def validate_global_ip(address: str) -> str:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as exc:
        raise PolicyError("dns_invalid_address", "DNS 返回了无效 IP 地址。") from exc
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if (
        not ip.is_global
        or ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise PolicyError(
            "ssrf_blocked_address",
            "DNS 解析指向非公网地址，已按 SSRF 策略拒绝。",
        )
    return str(ip)


class SafePublicResolver(AbstractResolver):
    """Resolve and pin only global addresses at the actual connection boundary."""

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_UNSPEC
    ) -> list[dict[str, object]]:
        loop = __import__("asyncio").get_running_loop()
        try:
            infos = await loop.getaddrinfo(
                host,
                port,
                type=socket.SOCK_STREAM,
                family=family,
            )
        except socket.gaierror as exc:
            raise OSError("public DNS resolution failed") from exc
        resolved: list[dict[str, object]] = []
        seen: set[tuple[int, str]] = set()
        for result_family, _socktype, proto, _canonname, sockaddr in infos:
            address = validate_global_ip(sockaddr[0])
            key = (result_family, address)
            if key in seen:
                continue
            seen.add(key)
            resolved.append(
                {
                    "hostname": host,
                    "host": address,
                    "port": port,
                    "family": result_family,
                    "proto": proto,
                    "flags": socket.AI_NUMERICHOST,
                }
            )
        if not resolved:
            raise OSError("public DNS resolution returned no usable address")
        return resolved

    async def close(self) -> None:
        return None
