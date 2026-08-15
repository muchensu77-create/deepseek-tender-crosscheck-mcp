from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Literal

from .errors import PolicyError
from .models import (
    TenderClaim,
    TenderClaimedRole,
    TenderFieldObservation,
    TenderNoticeType,
    TenderPageReview,
    TenderRegime,
    TenderRiskSignal,
)

_CONTROL_OR_FORMAT = re.compile(r"[\x00-\x1f\x7f]")
_SEARCH_OPERATOR = re.compile(
    r"(?i)\b(?:site|inurl|intitle|filetype|ext|cache|related)\s*:|\b(?:AND|OR|NOT)\b"
)
_SEARCH_PUNCTUATION = re.compile(
    r"[\"'“”‘’`(){}\[\]<>|\\:;+*~^!@#$%&=?/\-‐‑‒–—−]+"
)
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class TenderQuerySpec:
    query_id: str
    phase: Literal["forward", "reverse"]
    purpose: str
    query: str


_FIELD_LIMITS: dict[str, tuple[int, int]] = {
    "project_name": (2, 120),
    "project_code": (4, 60),
    "package_code": (1, 40),
    "purchaser": (2, 120),
    "supplier_legal_name": (2, 120),
    "unified_social_credit_code": (18, 18),
    "claimed_amount": (1, 50),
    "claimed_date": (1, 40),
    "region": (1, 60),
}


def normalize_claim_value(field: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PolicyError("invalid_tender_claim", f"{field} 必须是字符串。")
    normalized = unicodedata.normalize("NFKC", value).strip()
    minimum, maximum = _FIELD_LIMITS[field]
    if not minimum <= len(normalized) <= maximum:
        raise PolicyError(
            "invalid_tender_claim",
            f"{field} 长度必须在 {minimum} 到 {maximum} 个字符之间。",
        )
    if _CONTROL_OR_FORMAT.search(normalized) or any(
        unicodedata.category(character) == "Cf" for character in normalized
    ):
        raise PolicyError("invalid_tender_claim", f"{field} 包含控制字符或隐藏格式字符。")
    if field == "unified_social_credit_code":
        code = normalized.upper()
        if not re.fullmatch(r"[0-9A-HJ-NPQRTUWXY]{18}", code):
            raise PolicyError(
                "invalid_tender_claim",
                "统一社会信用代码必须是 18 位合法大写字母/数字；不会模糊改写。",
            )
        return code
    if field == "project_code":
        identifier_characters = "".join(
            character for character in normalized if character.isalnum()
        )
        if len(identifier_characters) < 4:
            raise PolicyError(
                "invalid_tender_claim",
                "项目/采购编号至少需要 4 个字母或数字，短码不能作为强身份锚点。",
            )
        if identifier_characters.isdecimal() and len(identifier_characters) < 8:
            raise PolicyError(
                "invalid_tender_claim",
                "纯数字项目/采购编号至少需要 8 位；四位年份或过短数字不能作为强身份锚点。",
            )
        if identifier_characters.isdecimal() and len(identifier_characters) == 8:
            try:
                date(
                    int(identifier_characters[:4]),
                    int(identifier_characters[4:6]),
                    int(identifier_characters[6:]),
                )
            except ValueError:
                pass
            else:
                raise PolicyError(
                    "invalid_tender_claim",
                    "8 位纯数字值可解析为 YYYYMMDD 日期，不能作为项目编号强锚点。",
                )
    return _WHITESPACE.sub(" ", normalized)


def prepare_tender_claim(
    *,
    project_name: str,
    procurement_regime: TenderRegime,
    project_code: str | None,
    package_code: str | None,
    purchaser: str | None,
    supplier_legal_name: str | None,
    unified_social_credit_code: str | None,
    claimed_amount: str | None,
    claimed_date: str | None,
    region: str | None,
    notice_type: TenderNoticeType,
    claimed_role: TenderClaimedRole,
) -> TenderClaim:
    if procurement_regime not in {
        "government_procurement",
        "mandatory_tender",
        "unknown",
    }:
        raise PolicyError("invalid_tender_claim", "procurement_regime 不在允许的制度类型中。")
    if notice_type not in {
        "tender_notice",
        "candidate_notice",
        "award_result",
        "contract_notice",
        "acceptance_record",
        "unknown",
    }:
        raise PolicyError("invalid_tender_claim", "notice_type 不在允许的公告类型中。")
    if claimed_role not in {
        "bidder",
        "candidate",
        "winner",
        "contractor",
        "completed_performance",
        "unknown",
    }:
        raise PolicyError("invalid_tender_claim", "claimed_role 不在允许的主体角色中。")
    return TenderClaim(
        project_name=normalize_claim_value("project_name", project_name) or "",
        procurement_regime=procurement_regime,
        project_code=normalize_claim_value("project_code", project_code),
        package_code=normalize_claim_value("package_code", package_code),
        purchaser=normalize_claim_value("purchaser", purchaser),
        supplier_legal_name=normalize_claim_value(
            "supplier_legal_name", supplier_legal_name
        ),
        unified_social_credit_code=normalize_claim_value(
            "unified_social_credit_code", unified_social_credit_code
        ),
        claimed_amount=normalize_claim_value("claimed_amount", claimed_amount),
        claimed_date=normalize_claim_value("claimed_date", claimed_date),
        region=normalize_claim_value("region", region),
        notice_type=notice_type,
        claimed_role=claimed_role,
    )


def _literal(value: str) -> str:
    # Search syntax only comes from fixed templates below. Operator-controlled claim
    # values are converted to inert phrases so they cannot inject site:/OR clauses.
    sanitized = _SEARCH_OPERATOR.sub(" ", value)
    sanitized = _SEARCH_PUNCTUATION.sub(" ", sanitized)
    sanitized = _WHITESPACE.sub(" ", sanitized).strip()
    if not sanitized:
        raise PolicyError(
            "invalid_tender_claim",
            "招投标字段清理搜索运算符后为空，无法安全生成查询。",
        )
    return sanitized


def _compose(*parts: str) -> str:
    query = " ".join(part for part in parts if part)
    if len(query) > 300:
        raise PolicyError(
            "tender_query_too_long",
            "结构化字段生成的固定查询超过 300 字符，请缩短项目/主体名称。",
        )
    return query


def build_tender_query_plan(
    claim: TenderClaim,
) -> tuple[list[TenderQuerySpec], list[TenderQuerySpec]]:
    project = _literal(claim.project_name)
    code = _literal(claim.project_code) if claim.project_code else project
    supplier = _literal(claim.supplier_legal_name) if claim.supplier_legal_name else ""
    purchaser = _literal(claim.purchaser) if claim.purchaser else ""
    package = _literal(claim.package_code) if claim.package_code else ""
    credit_code = (
        _literal(claim.unified_social_credit_code)
        if claim.unified_social_credit_code
        else ""
    )
    party_or_fallback = supplier or purchaser or project
    secondary_party = party_or_fallback if party_or_fallback != code else ""
    reverse_party = credit_code or supplier or purchaser or project
    reverse_secondary_party = reverse_party if reverse_party != code else ""
    party_terms = "" if supplier else "(中标人 OR 成交供应商 OR 中标候选人)"

    forward = [
        TenderQuerySpec(
            "F01",
            "forward",
            "按项目身份发现中标/成交结果公告",
            _compose(
                code,
                project if claim.project_code else "",
                _literal(claim.region) if claim.region else "",
                "(中标 OR 成交 OR 结果公告)",
            ),
        ),
        TenderQuerySpec(
            "F02",
            "forward",
            "按项目、供应商/采购人和标包交叉发现结果",
            _compose(
                code,
                secondary_party,
                package,
                party_terms,
            ),
        ),
        TenderQuerySpec(
            "F03",
            "forward",
            "发现合同与履约验收阶段公开记录",
            _compose(
                code,
                purchaser or supplier,
                "(合同公告 OR 履约验收 OR 验收公告)",
            ),
        ),
    ]
    reverse = [
        TenderQuerySpec(
            "R01",
            "reverse",
            "反查更正、变更与澄清公告",
            _compose(code, "(更正公告 OR 变更公告 OR 澄清公告)"),
        ),
        TenderQuerySpec(
            "R02",
            "reverse",
            "反查终止、废标、流标、取消与撤销",
            _compose(
                code,
                "(终止公告 OR 中止 OR 暂停 OR 废标 OR 流标 OR 取消 OR 撤销 "
                "OR 撤回 OR 作废 OR 废止 OR 停止采购 OR 采购失败)",
            ),
        ),
        TenderQuerySpec(
            "R03",
            "reverse",
            "反查质疑、投诉、监督检查与中标无效",
            _compose(
                code,
                "(质疑 OR 投诉处理 OR 投诉决定 OR 监督检查 OR 中标无效 "
                "OR 结果公告无效)",
            ),
        ),
        TenderQuerySpec(
            "R04",
            "reverse",
            "按法定主体/信用代码逆向绑定同一项目",
            _compose(reverse_secondary_party, code, package),
        ),
        TenderQuerySpec(
            "R05",
            "reverse",
            "反查后续合同、验收、重新采购或重新招标",
            _compose(
                code,
                _literal(claim.claimed_amount) if claim.claimed_amount else "",
                _literal(claim.claimed_date) if claim.claimed_date else "",
                "(合同公告 OR 验收公告 OR 验收不合格 OR 验收不通过 "
                "OR 未通过验收 OR 合同解除 OR 解除采购合同 OR 合同终止 "
                "OR 重新采购 OR 重新招标 OR 重新组织采购 OR 重新实施采购)",
            ),
        ),
    ]
    return forward, reverse


def normalized_exact_text(value: str) -> str:
    return "".join(
        character.casefold()
        for character in unicodedata.normalize("NFKC", value)
        if character.isalnum()
    )


def _identifier_matches(expected: str, text: str) -> bool:
    normalized_expected = unicodedata.normalize("NFKC", expected).casefold()
    groups: list[str] = []
    current: list[str] = []
    for character in normalized_expected:
        if character.isalnum():
            current.append(character)
        elif current:
            groups.append("".join(current))
            current = []
    if current:
        groups.append("".join(current))
    if not groups or sum(len(group) for group in groups) < 4:
        return False
    ascii_only = all(group.isascii() for group in groups)
    left_boundary = r"(?<![0-9a-z])" if ascii_only else r"(?<!\w)"
    right_boundary = r"(?![0-9a-z])" if ascii_only else r"(?!\w)"
    # Separators may be punctuation/whitespace/underscore only. A broad
    # `[^0-9a-z]*` would incorrectly swallow Chinese words between code groups.
    separator = r"[\W_]*"
    core_pattern = separator.join(re.escape(group) for group in groups)
    pattern = left_boundary + core_pattern + right_boundary
    normalized_text = unicodedata.normalize("NFKC", text).casefold()
    if re.search(pattern, normalized_text) is not None:
        return True
    if not ascii_only:
        # Chinese procurement identifiers commonly follow a word character with
        # no colon, e.g. `项目编号川政采招〔2026〕1号`. Permit that explicit
        # field label without weakening the general identifier boundary.
        labelled_pattern = (
            r"(?:项目|采购|招标)?编号\s*[:：]?\s*" + core_pattern + right_boundary
        )
        return re.search(labelled_pattern, normalized_text) is not None
    return False


def _project_code_matches(expected: str, text: str) -> bool:
    identifier_characters = "".join(
        character
        for character in unicodedata.normalize("NFKC", expected)
        if character.isalnum()
    )
    if not identifier_characters.isdecimal():
        return _identifier_matches(expected, text)
    normalized_text = unicodedata.normalize("NFKC", text).casefold()
    labelled_pattern = (
        r"(?:项目|采购|招标)(?:编号|编码|代码|计划号|计划编号)\s*[:：]?\s*"
        + r"(?<!\d)"
        + re.escape(identifier_characters)
        + r"(?!\d)"
    )
    return re.search(labelled_pattern, normalized_text) is not None


def _legal_name_matches(expected: str, text: str) -> bool:
    normalized_expected = unicodedata.normalize("NFKC", expected).casefold()
    normalized_text = unicodedata.normalize("NFKC", text).casefold()
    disallowed_extensions = (
        "分公司",
        "子公司",
        "支公司",
        "集团",
        "项目部",
        "办事处",
    )
    start = 0
    while True:
        index = normalized_text.find(normalized_expected, start)
        if index < 0:
            return False
        suffix = normalized_text[index + len(normalized_expected) :]
        if not suffix.startswith(disallowed_extensions):
            return True
        start = index + 1


def _bounded_literal_matches(expected: str, text: str) -> bool:
    normalized_expected = unicodedata.normalize("NFKC", expected).casefold().strip()
    normalized_text = unicodedata.normalize("NFKC", text).casefold()
    if not normalized_expected:
        return False
    left = (
        r"(?<![0-9a-z])"
        if normalized_expected[0].isascii() and normalized_expected[0].isalnum()
        else ""
    )
    right = (
        r"(?![0-9a-z])"
        if normalized_expected[-1].isascii() and normalized_expected[-1].isalnum()
        else ""
    )
    return re.search(left + re.escape(normalized_expected) + right, normalized_text) is not None


def matched_claim_anchor_fields(claim: TenderClaim, text: str) -> list[str]:
    matched: list[str] = []
    if claim.project_code and _project_code_matches(claim.project_code, text):
        matched.append("project_code")
    project_name = normalized_exact_text(claim.project_name)
    if project_name and project_name in normalized_exact_text(text):
        matched.append("project_name")
    return matched


def is_strongly_related_to_claim(claim: TenderClaim, text: str) -> bool:
    matched = matched_claim_anchor_fields(claim, text)
    if claim.project_code:
        # Completion coverage requires the project name and code to co-occur in
        # the same retrieved page. Cross-page stitching is only an observation.
        return "project_code" in matched and "project_name" in matched
    if "project_name" not in matched:
        return False
    if claim.unified_social_credit_code:
        return _identifier_matches(claim.unified_social_credit_code, text)
    # Without a project code or a legal-entity code, name-only relevance is too
    # weak to drive a completed government verification phase.
    return False


def is_key_binding_page(claim: TenderClaim, text: str) -> bool:
    if not is_strongly_related_to_claim(claim, text):
        return False
    if not claim.package_code or not _bounded_literal_matches(claim.package_code, text):
        return False
    supplier_bound = bool(
        claim.supplier_legal_name
        and _legal_name_matches(claim.supplier_legal_name, text)
    )
    credit_bound = bool(
        claim.unified_social_credit_code
        and _identifier_matches(claim.unified_social_credit_code, text)
    )
    return supplier_bound and credit_bound


def observe_claim_fields(
    claim: TenderClaim, pages: list[TenderPageReview]
) -> list[TenderFieldObservation]:
    usable = [
        page
        for page in pages
        if page.fetch.ok
        and page.fetch.data is not None
        and page.fetch.data.retrieval_status == "retrieved"
    ]
    searchable = {
        page.evidence_id: normalized_exact_text(
            f"{page.fetch.data.title} {page.fetch.data.content_excerpt}"
        )
        for page in usable
        if page.fetch.data is not None
    }
    raw_searchable = {
        page.evidence_id: f"{page.fetch.data.title} {page.fetch.data.content_excerpt}"
        for page in usable
        if page.fetch.data is not None
    }
    observations: list[TenderFieldObservation] = []
    for field in (
        "project_name",
        "project_code",
        "package_code",
        "purchaser",
        "supplier_legal_name",
        "unified_social_credit_code",
        "claimed_amount",
        "claimed_date",
        "region",
    ):
        expected = getattr(claim, field)
        if not expected:
            continue
        if field in {"project_code", "unified_social_credit_code", "claimed_date"}:
            matches = [
                page
                for page in usable
                if page.fetch.data is not None
                and (
                    _project_code_matches(
                        expected, raw_searchable.get(page.evidence_id, "")
                    )
                    if field == "project_code"
                    else _identifier_matches(
                        expected, raw_searchable.get(page.evidence_id, "")
                    )
                )
            ]
            match_rule = (
                "date_identifier_boundary_v1"
                if field == "claimed_date"
                else "identifier_token_boundary_v1"
            )
        elif field in {"package_code", "claimed_amount"}:
            matches = [
                page
                for page in usable
                if _bounded_literal_matches(
                    expected, raw_searchable.get(page.evidence_id, "")
                )
            ]
            match_rule = "bounded_literal_v1"
        elif field == "supplier_legal_name":
            matches = [
                page
                for page in usable
                if _legal_name_matches(
                    expected, raw_searchable.get(page.evidence_id, "")
                )
            ]
            match_rule = "legal_name_exact_without_extension_v1"
        else:
            needle = normalized_exact_text(expected)
            matches = [
                page
                for page in usable
                if needle and needle in searchable.get(page.evidence_id, "")
            ]
            match_rule = "nfkc_alphanumeric_exact_substring_v1"
        if matches:
            status = "observed_in_retrieved_excerpt"
        elif usable:
            status = "not_observed_in_retrieved_excerpts"
        else:
            status = "not_evaluated_no_retrieved_pages"
        observations.append(
            TenderFieldObservation(
                field=field,
                expected_value=expected,
                status=status,
                supporting_evidence_ids=[page.evidence_id for page in matches],
                supporting_urls=[page.url for page in matches],
                match_rule=match_rule,
            )
        )
    return observations


_RISK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "correction_or_change": ("更正", "变更", "澄清", "修正"),
    "failed_cancelled_or_terminated": (
        "终止",
        "中止",
        "暂停",
        "废标",
        "流标",
        "取消",
        "撤销",
        "撤回",
        "作废",
        "废止",
        "采购失败",
        "采购活动失败",
        "重新采购",
        "重新招标",
        "重新开展采购",
        "重新开展招标",
    ),
    "challenge_complaint_or_supervision": (
        "质疑",
        "投诉处理",
        "监督检查",
        "中标无效",
        "中标结果无效",
        "成交结果无效",
        "采购结果无效",
    ),
    "contract_or_acceptance_adverse": (
        "验收不合格",
        "验收不通过",
        "解除合同",
        "合同解除",
    ),
}

_RISK_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "challenge_complaint_or_supervision",
        "中标/成交/采购结果无效",
        re.compile(r"(?:中标|成交|采购)结果.{0,6}无效"),
    ),
    (
        "challenge_complaint_or_supervision",
        "投诉处理/决定",
        re.compile(r"投诉.{0,6}(?:处理|决定)"),
    ),
    (
        "failed_cancelled_or_terminated",
        "重新开展/组织采购或招标",
        re.compile(r"重新(?:开展|组织).{0,4}(?:采购|招标)"),
    ),
    (
        "failed_cancelled_or_terminated",
        "重新实施/启动/进行采购或招标",
        re.compile(r"重新(?:实施|启动|进行).{0,4}(?:采购|招标)"),
    ),
    (
        "contract_or_acceptance_adverse",
        "验收不合格/不通过",
        re.compile(r"验收.{0,6}(?:不合格|不通过)"),
    ),
    (
        "contract_or_acceptance_adverse",
        "解除/终止采购合同",
        re.compile(r"(?:解除|终止).{0,4}(?:采购)?合同"),
    ),
    (
        "contract_or_acceptance_adverse",
        "未通过/不予通过验收",
        re.compile(r"(?:未通过|不予通过).{0,4}验收"),
    ),
    (
        "contract_or_acceptance_adverse",
        "合同解除/终止",
        re.compile(r"合同.{0,4}(?:被)?(?:解除|终止)"),
    ),
    (
        "failed_cancelled_or_terminated",
        "停止采购",
        re.compile(r"停止.{0,4}采购"),
    ),
)


def detect_risk_signals(
    claim: TenderClaim, pages: list[TenderPageReview]
) -> list[TenderRiskSignal]:
    signals: list[TenderRiskSignal] = []
    seen: set[tuple[str, str, str]] = set()
    for page in pages:
        data = page.fetch.data
        if not page.fetch.ok or data is None or data.retrieval_status != "retrieved":
            continue
        text = f"{data.title}\n{data.content_excerpt}"
        matched_anchor_fields = matched_claim_anchor_fields(claim, text)
        if not matched_anchor_fields:
            # A generic correction/termination page for another project is not a
            # signal about this claim. It remains in the candidate ledger instead.
            continue
        compact_text = "".join(
            character
            for character in unicodedata.normalize("NFKC", text)
            if not character.isspace()
            and unicodedata.category(character) not in {"Cf", "Cc"}
        )
        for category, keywords in _RISK_KEYWORDS.items():
            for keyword in keywords:
                if re.sub(r"\s+", "", unicodedata.normalize("NFKC", keyword)) in compact_text:
                    signal_key = (page.evidence_id, category, keyword)
                    if signal_key in seen:
                        continue
                    seen.add(signal_key)
                    signals.append(
                        TenderRiskSignal(
                            category=category,
                            keyword=keyword,
                            matched_anchor_fields=matched_anchor_fields,
                            evidence_id=page.evidence_id,
                            url=page.url,
                            content_sha256=data.content_sha256,
                        )
                    )
        for category, label, pattern in _RISK_PATTERNS:
            if not pattern.search(compact_text):
                continue
            signal_key = (page.evidence_id, category, label)
            if signal_key in seen:
                continue
            seen.add(signal_key)
            signals.append(
                TenderRiskSignal(
                    category=category,
                    keyword=label,
                    matched_anchor_fields=matched_anchor_fields,
                    evidence_id=page.evidence_id,
                    url=page.url,
                    content_sha256=data.content_sha256,
                )
            )
    return signals[:100]
