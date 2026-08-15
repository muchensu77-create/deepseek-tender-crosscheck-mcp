from __future__ import annotations

import asyncio
import time
from collections import deque
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from .errors import OfficialSearchError


class SlidingWindowRateLimiter:
    def __init__(self, requests_per_minute: int) -> None:
        self.limit = requests_per_minute
        self._events: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        now = time.monotonic()
        async with self._lock:
            while self._events and now - self._events[0] >= 60:
                self._events.popleft()
            if len(self._events) >= self.limit:
                retry_after = max(1, int(60 - (now - self._events[0])))
                raise OfficialSearchError(
                    "rate_limit_exceeded",
                    "本地 MCP 请求频率已达到上限。",
                    retryable=True,
                    details={"retry_after_seconds": retry_after},
                )
            self._events.append(now)


class DomainPacer:
    def __init__(self, minimum_interval_seconds: float) -> None:
        self.minimum_interval_seconds = minimum_interval_seconds
        self._last_request: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._map_lock = asyncio.Lock()

    async def wait(self, host: str, requested_delay: float = 0.0) -> None:
        async with self._map_lock:
            lock = self._locks.setdefault(host, asyncio.Lock())
        delay = max(self.minimum_interval_seconds, requested_delay)
        if delay > 10:
            raise OfficialSearchError(
                "robots_crawl_delay_too_long",
                "网站 robots.txt 要求的 Crawl-delay 超过本工具单次等待上限；已停止而非绕过。",
                details={"crawl_delay_seconds": delay},
            )
        async with lock:
            now = time.monotonic()
            remaining = delay - (now - self._last_request.get(host, 0.0))
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._last_request[host] = time.monotonic()


class RequestGate:
    def __init__(
        self,
        *,
        max_concurrency: int,
        requests_per_minute: int,
        minimum_domain_interval_seconds: float,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._rate = SlidingWindowRateLimiter(requests_per_minute)
        self._pacer = DomainPacer(minimum_domain_interval_seconds)

    @asynccontextmanager
    async def request(self, url: str, requested_delay: float = 0.0):
        await self._rate.acquire()
        host = urlsplit(url).hostname or ""
        async with self._semaphore:
            await self._pacer.wait(host, requested_delay)
            yield

    @asynccontextmanager
    async def operation(self):
        """Bound a backend operation whose destination is operator-controlled."""
        await self._rate.acquire()
        async with self._semaphore:
            yield
