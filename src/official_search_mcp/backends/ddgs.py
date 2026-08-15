from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping

from ..errors import BackendError
from .base import RawSearchResult, SearchBackend

_MAX_WORKER_OUTPUT_BYTES = 1_000_000


class DDGSSearchBackend(SearchBackend):
    """No-key fallback; each connector runs in a killable isolated process."""

    is_live = True

    def __init__(self, timeout_seconds: float, backends: str) -> None:
        supported = {
            "brave",
            "duckduckgo",
            "google",
            "mojeek",
            "startpage",
            "yahoo",
            "yandex",
        }
        selected = tuple(
            dict.fromkeys(item.strip() for item in backends.split(",") if item.strip())
        )
        if not selected or any(item not in supported for item in selected):
            raise BackendError(
                "invalid_ddgs_backends",
                "DDGS 后端配置为空或包含未允许的连接器。",
            )
        self.timeout_seconds = timeout_seconds
        self.backends = ",".join(selected)
        self.name = f"ddgs:{self.backends}"

    async def _search_connector(
        self,
        connector: str,
        query: str,
        max_results: int,
        timeout_seconds: float,
    ) -> list[Mapping[str, object]]:
        payload = json.dumps(
            {
                "connector": connector,
                "query": query,
                "max_results": max_results,
                "timeout_seconds": timeout_seconds,
            },
            ensure_ascii=False,
        ).encode()
        process: asyncio.subprocess.Process | None = None
        try:
            async with asyncio.timeout(timeout_seconds):
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "official_search_mcp.backends.ddgs_worker",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env={
                        "PYTHONIOENCODING": "utf-8",
                        "PYTHONUTF8": "1",
                    },
                )
                stdout, _stderr = await process.communicate(payload)
        except TimeoutError:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            raise
        except BaseException:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            raise

        if len(stdout) > _MAX_WORKER_OUTPUT_BYTES:
            raise BackendError(
                "search_backend_invalid_response",
                "DDGS 隔离进程返回超过允许上限。",
            )
        try:
            decoded = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackendError(
                "search_backend_invalid_response",
                "DDGS 隔离进程未返回合法 JSON。",
                retryable=True,
            ) from exc
        if not isinstance(decoded, dict) or decoded.get("ok") is not True:
            error_type = (
                decoded.get("error_type", "unknown")
                if isinstance(decoded, dict)
                else "invalid_envelope"
            )
            raise BackendError(
                "ddgs_connector_failed",
                f"DDGS 连接器返回失败（{error_type}）。",
                retryable=True,
            )
        rows = decoded.get("results")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise BackendError(
                "search_backend_invalid_response",
                "DDGS 隔离进程返回的 results 结构无效。",
            )
        return rows

    async def search(self, query: str, max_results: int) -> list[RawSearchResult]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_seconds
        rows: list[Mapping[str, object]] = []
        used_backend: str | None = None
        last_error: Exception | None = None
        for connector in self.backends.split(","):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                rows = await self._search_connector(
                    connector,
                    query,
                    max_results,
                    remaining,
                )
            except Exception as exc:
                last_error = exc
                continue
            if rows:
                used_backend = connector
                break

        if not rows and isinstance(last_error, TimeoutError):
            raise BackendError(
                "search_timeout",
                "DDGS 实时搜索超时；可重试或切换到自托管 SearXNG。",
                retryable=True,
            ) from last_error
        if not rows:
            raise BackendError(
                "search_backend_unavailable",
                (
                    "DDGS 实时搜索连接器均未返回结果"
                    f"（{type(last_error).__name__ if last_error else 'no_result'}）；"
                    "没有生成替代 URL。"
                ),
                retryable=True,
            ) from last_error

        results: list[RawSearchResult] = []
        for row in rows:
            results.append(
                RawSearchResult(
                    title=str(row.get("title") or "").strip(),
                    url=str(row.get("href") or row.get("url") or "").strip(),
                    snippet=str(row.get("body") or row.get("snippet") or "").strip(),
                    source_backend=f"ddgs:{used_backend}",
                )
            )
        return results
