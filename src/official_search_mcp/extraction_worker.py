from __future__ import annotations

import json
import sys
from dataclasses import asdict
from typing import Any

from .extraction import extract_document
from .security import DomainPolicy

_MAX_HEADER_BYTES = 64 * 1024
_MAX_BODY_BYTES = 10 * 1024 * 1024


def _string_list(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"invalid {name}")
    return tuple(value)


def _validate(header: Any) -> tuple[str, str, str | None, DomainPolicy, int]:
    if not isinstance(header, dict):
        raise ValueError("invalid header")
    final_url = header.get("final_url")
    content_type = header.get("content_type")
    declared_charset = header.get("declared_charset")
    max_chars = header.get("max_chars")
    if not isinstance(final_url, str) or not 8 <= len(final_url) <= 2_048:
        raise ValueError("invalid final_url")
    if not isinstance(content_type, str) or len(content_type) > 500:
        raise ValueError("invalid content_type")
    if declared_charset is not None and (
        not isinstance(declared_charset, str) or len(declared_charset) > 100
    ):
        raise ValueError("invalid declared_charset")
    if not isinstance(max_chars, int) or not 500 <= max_chars <= 50_000:
        raise ValueError("invalid max_chars")
    policy = DomainPolicy(
        official_suffixes=_string_list(header.get("official_suffixes"), "official_suffixes"),
        allowed_domains=_string_list(header.get("allowed_domains"), "allowed_domains"),
        default_domains=_string_list(header.get("default_domains"), "default_domains"),
    )
    return final_url, content_type, declared_charset, policy, max_chars


def main() -> int:
    try:
        payload = sys.stdin.buffer.read(_MAX_HEADER_BYTES + _MAX_BODY_BYTES + 2)
        header_bytes, raw = payload.split(b"\n", 1)
        if len(header_bytes) > _MAX_HEADER_BYTES or len(raw) > _MAX_BODY_BYTES:
            raise ValueError("extraction input too large")
        final_url, content_type, declared_charset, policy, max_chars = _validate(
            json.loads(header_bytes)
        )
        document = extract_document(
            raw,
            final_url=final_url,
            content_type=content_type,
            declared_charset=declared_charset,
            policy=policy,
            max_chars=max_chars,
        )
        json.dump(
            {"ok": True, "document": asdict(document)},
            sys.stdout,
            ensure_ascii=False,
        )
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
