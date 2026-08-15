from __future__ import annotations

from official_search_mcp.extraction import extract_document
from official_search_mcp.security import DomainPolicy


def _policy() -> DomainPolicy:
    return DomainPolicy(
        official_suffixes=("gov.cn",),
        allowed_domains=(),
        default_domains=("gov.cn",),
    )


def test_extracts_chinese_title_text_and_canonical() -> None:
    raw = """
    <html><head><title>公开采购公告</title>
    <link rel="canonical" href="/notice/42"></head>
    <body><nav>导航</nav><article><h1>项目公告</h1>
    <p>这是用于公开离线测试的虚构公告正文，不包含任何私密投标材料。</p>
    </article></body></html>
    """.encode()
    result = extract_document(
        raw,
        final_url="https://www.gov.cn/notice/42?from=search",
        content_type="text/html; charset=utf-8",
        declared_charset="utf-8",
        policy=_policy(),
        max_chars=8_000,
    )
    assert result.title == "公开采购公告"
    assert "虚构公告正文" in result.content
    assert result.canonical_url == "https://www.gov.cn/notice/42"
    assert result.retrieval_status == "retrieved"


def test_dynamic_shell_is_reported_honestly() -> None:
    raw = b'<html><body><div id="app"></div><script></script><script></script>' + (
        b"<script></script>" * 5
    )
    result = extract_document(
        raw,
        final_url="https://www.gov.cn/app",
        content_type="text/html",
        declared_charset="utf-8",
        policy=_policy(),
        max_chars=8_000,
    )
    assert result.retrieval_status == "degraded_dynamic"
    assert any("JavaScript" in warning for warning in result.warnings)


def test_external_canonical_is_not_trusted() -> None:
    raw = b'<html><head><link rel="canonical" href="https://evil.example/x"></head><body>ok</body>'
    result = extract_document(
        raw,
        final_url="https://www.gov.cn/a",
        content_type="text/html",
        declared_charset="utf-8",
        policy=_policy(),
        max_chars=8_000,
    )
    assert result.canonical_url == "https://www.gov.cn/a"
    assert any("canonical" in warning for warning in result.warnings)


def test_long_html_is_explicitly_marked_truncated() -> None:
    body = "公开招投标正文。" * 2_000
    raw = f"<html><body><article><p>{body}</p></article></body></html>".encode()
    result = extract_document(
        raw,
        final_url="https://www.gov.cn/notice/long",
        content_type="text/html; charset=utf-8",
        declared_charset="utf-8",
        policy=_policy(),
        max_chars=8_000,
    )
    assert result.retrieval_status == "retrieved"
    assert result.content_truncated is True
    assert result.extracted_char_count > len(result.content) == 8_000
    assert any("截断" in warning for warning in result.warnings)
