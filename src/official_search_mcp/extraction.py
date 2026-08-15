from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from charset_normalizer import from_bytes
from trafilatura import extract

from .errors import PolicyError
from .security import DomainPolicy, normalize_public_url


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    title: str
    canonical_url: str
    content: str
    extracted_char_count: int
    content_truncated: bool
    method: str
    retrieval_status: str
    warnings: tuple[str, ...]


class _HeadAndTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.ignored_depth = 0
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.canonical_href: str | None = None
        self.script_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self.in_title = True
        if lowered in {"script", "style", "noscript", "template"}:
            self.ignored_depth += 1
        if lowered == "script":
            self.script_count += 1
        if lowered == "link":
            attributes = {key.lower(): value for key, value in attrs}
            rel = (attributes.get("rel") or "").lower().split()
            if "canonical" in rel and attributes.get("href") and not self.canonical_href:
                self.canonical_href = attributes["href"]

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self.in_title = False
        if lowered in {"script", "style", "noscript", "template"} and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if self.ignored_depth == 0 and data.strip():
            self.text_parts.append(data)


def _decode(raw: bytes, declared_charset: str | None) -> str:
    if declared_charset:
        try:
            return raw.decode(declared_charset, errors="replace")
        except LookupError:
            pass
    match = from_bytes(raw).best()
    return str(match) if match is not None else raw.decode("utf-8", errors="replace")


def _clean_text(value: str) -> str:
    lines = [re.sub(r"[\t \u00a0]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def extract_document(
    raw: bytes,
    *,
    final_url: str,
    content_type: str,
    declared_charset: str | None,
    policy: DomainPolicy,
    max_chars: int,
) -> ExtractedDocument:
    decoded = _decode(raw, declared_charset)
    media_type = content_type.split(";", 1)[0].strip().lower()
    fallback_title = urlsplit(final_url).hostname or final_url
    if media_type == "text/plain":
        full_content = _clean_text(decoded)
        content_truncated = len(full_content) > max_chars
        content = full_content[:max_chars]
        plain_warnings = (
            ("正文超过返回上限，已截断；未展示部分是明确证据缺口。",)
            if content_truncated
            else (() if content else ("响应是空的纯文本。",))
        )
        return ExtractedDocument(
            title=fallback_title,
            canonical_url=final_url,
            content=content,
            extracted_char_count=len(full_content),
            content_truncated=content_truncated,
            method="plain_text",
            retrieval_status="retrieved" if content else "no_extractable_text",
            warnings=plain_warnings,
        )

    warnings: list[str] = []
    parser = _HeadAndTextParser()
    parse_failed = False
    try:
        parser.feed(decoded)
    except Exception:
        parse_failed = True
    title = _clean_text(" ".join(parser.title_parts))[:500] or fallback_title
    canonical = final_url
    if parse_failed:
        warnings.append("HTML 解析器遇到异常标记，正文抽取结果可能降级。")
    if parser.canonical_href:
        candidate = urljoin(final_url, parser.canonical_href)
        try:
            canonical = normalize_public_url(candidate, policy)
        except PolicyError:
            warnings.append(
                "页面声明的 canonical URL 不符合授权域名/URL 策略，已使用最终抓取 URL。"
            )

    method = "trafilatura"
    try:
        extracted = extract(
            raw,
            url=final_url,
            output_format="txt",
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )
    except Exception:
        extracted = None
    content = _clean_text(extracted or "")
    if not content:
        content = _clean_text("\n".join(parser.text_parts))
        method = "html_text_fallback"
        if content:
            warnings.append("Trafilatura 未提取出正文，已降级为可见 DOM 文本。")

    lowered = decoded.lower()
    dynamic_markers = (
        "__next_data__",
        'id="__next"',
        'id="app"',
        'id="root"',
        "enable javascript",
        "请开启javascript",
    )
    likely_dynamic = len(content) < 250 and (
        parser.script_count >= 5 or any(marker in lowered for marker in dynamic_markers)
    )
    if likely_dynamic:
        status = "degraded_dynamic"
        warnings.append("页面疑似依赖 JavaScript；本原型不执行脚本，正文可能不完整。")
    elif content:
        status = "retrieved"
    else:
        status = "no_extractable_text"
        warnings.append("页面已读取，但没有提取出可用正文；没有生成或补写内容。")
    extracted_char_count = len(content)
    content_truncated = extracted_char_count > max_chars
    if content_truncated:
        warnings.append("正文超过返回上限，已截断；未展示部分是明确证据缺口。")
    return ExtractedDocument(
        title=title,
        canonical_url=canonical,
        content=content[:max_chars],
        extracted_char_count=extracted_char_count,
        content_truncated=content_truncated,
        method=method,
        retrieval_status=status,
        warnings=tuple(warnings),
    )
