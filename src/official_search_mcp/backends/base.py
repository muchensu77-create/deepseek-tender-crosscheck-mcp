from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RawSearchResult:
    title: str
    url: str
    snippet: str
    source_backend: str | None = None


class SearchBackend(ABC):
    name: str
    is_live: bool

    @abstractmethod
    async def search(self, query: str, max_results: int) -> list[RawSearchResult]:
        """Return untrusted raw search results. The service applies policy afterward."""
