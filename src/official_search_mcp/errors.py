from __future__ import annotations

from typing import Any


class OfficialSearchError(Exception):
    """A safe, structured error that may be returned to an MCP caller."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


class PolicyError(OfficialSearchError):
    """The request was rejected by a domain, URL, or robots policy."""


class BackendError(OfficialSearchError):
    """The selected search backend could not complete the request."""
