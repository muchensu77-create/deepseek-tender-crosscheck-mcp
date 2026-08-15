from __future__ import annotations

import json
from typing import Any

import aiohttp

from ..errors import BackendError


async def get_json(
    url: str,
    *,
    params: dict[str, str | int],
    headers: dict[str, str] | None,
    timeout_seconds: float,
    max_bytes: int = 2 * 1024 * 1024,
) -> Any:
    timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=min(5, timeout_seconds))
    try:
        async with aiohttp.ClientSession(
            timeout=timeout,
            trust_env=False,
            cookie_jar=aiohttp.DummyCookieJar(),
        ) as session:
            async with session.get(
                url,
                params=params,
                headers=headers,
                allow_redirects=False,
            ) as response:
                if 300 <= response.status < 400:
                    raise BackendError(
                        "search_backend_redirect_rejected",
                        "搜索后端意外返回重定向；为避免凭据或请求被转发，已拒绝。",
                    )
                if response.status in {401, 403}:
                    raise BackendError(
                        "search_backend_auth_failed",
                        "搜索后端拒绝认证或禁用了所需输出格式。",
                    )
                if response.status == 429:
                    raise BackendError(
                        "search_backend_rate_limited",
                        "搜索后端正在限流。",
                        retryable=True,
                    )
                if response.status >= 400:
                    raise BackendError(
                        "search_backend_http_error",
                        f"搜索后端返回 HTTP {response.status}。",
                        retryable=response.status >= 500,
                    )
                if response.content_length and response.content_length > max_bytes:
                    raise BackendError(
                        "search_backend_response_too_large", "搜索后端响应超过大小上限。"
                    )
                body = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise BackendError(
                            "search_backend_response_too_large", "搜索后端响应超过大小上限。"
                        )
    except BackendError:
        raise
    except TimeoutError as exc:
        raise BackendError("search_timeout", "搜索后端请求超时。", retryable=True) from exc
    except aiohttp.ClientError as exc:
        raise BackendError(
            "search_backend_unavailable",
            f"搜索后端连接失败（{type(exc).__name__}）。",
            retryable=True,
        ) from exc
    try:
        return json.loads(body)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise BackendError("search_backend_invalid_json", "搜索后端没有返回有效 JSON。") from exc
