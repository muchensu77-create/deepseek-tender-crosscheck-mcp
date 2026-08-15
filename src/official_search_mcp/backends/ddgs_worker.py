from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any

from ddgs import DDGS

_SUPPORTED_CONNECTORS = {
    "brave",
    "duckduckgo",
    "google",
    "mojeek",
    "startpage",
    "yahoo",
    "yandex",
}


def _validate(payload: Any) -> tuple[str, str, int, float]:
    if not isinstance(payload, dict):
        raise ValueError("invalid payload")
    connector = payload.get("connector")
    query = payload.get("query")
    max_results = payload.get("max_results")
    timeout_seconds = payload.get("timeout_seconds")
    if connector not in _SUPPORTED_CONNECTORS:
        raise ValueError("invalid connector")
    if not isinstance(query, str) or not 1 <= len(query) <= 1_000:
        raise ValueError("invalid query")
    if not isinstance(max_results, int) or not 1 <= max_results <= 40:
        raise ValueError("invalid max_results")
    if not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 60:
        raise ValueError("invalid timeout")
    return connector, query, max_results, float(timeout_seconds)


def _bounded_row(row: Mapping[str, object]) -> dict[str, str]:
    return {
        "title": str(row.get("title") or "")[:2_000],
        "href": str(row.get("href") or row.get("url") or "")[:4_096],
        "body": str(row.get("body") or row.get("snippet") or "")[:10_000],
    }


def main() -> int:
    try:
        connector, query, max_results, timeout_seconds = _validate(json.load(sys.stdin))
        client = DDGS(timeout=max(1, int(timeout_seconds)))
        raw_rows = client.text(
            query,
            region="cn-zh",
            safesearch="moderate",
            max_results=max_results,
            backend=connector,
        )
        results = [_bounded_row(row) for row in (raw_rows or [])[:40] if isinstance(row, Mapping)]
        json.dump({"ok": True, "results": results}, sys.stdout, ensure_ascii=False)
        return 0
    except Exception as exc:
        json.dump(
            {"ok": False, "error_type": type(exc).__name__},
            sys.stdout,
            ensure_ascii=False,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
