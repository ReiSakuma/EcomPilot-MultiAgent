from __future__ import annotations

import hashlib
import json
import re
from typing import Any


CONTENT_FINDING_CODES = frozenset(
    {"unsupported_product_claim", "prohibited_marketing_claim"}
)
DISCOUNT_FINDING_CODES = frozenset({"discount_representation_mismatch"})
NUMERIC_FINDING_CODES = frozenset(
    {"margin_inconsistency", "inventory_inconsistency"}
)
REPAIRABLE_FINDING_CODES = (
    CONTENT_FINDING_CODES | DISCOUNT_FINDING_CODES | NUMERIC_FINDING_CODES
)
REPAIRABLE_CONTENT_ACTIONS = frozenset(
    {
        "remove_unconfirmed_claim",
        "rewrite_claim",
        "fix_discount_representation",
        "recalculate_strategy",
        "reduce_planned_units",
    }
)
REPAIRABLE_CONTENT_FIELDS = frozenset(
    {
        "listing.title",
        "listing.keywords",
        "listing.bullets",
        "strategy.launch_plan",
        "strategy.strategy_rationale",
        "strategy.price",
        "strategy.coupon",
        "strategy.margin",
        "strategy.inventory_check",
        "strategy.planned_units",
    }
)


# These phrases turn a confirmed feature into an unverified performance result.
# The deterministic guardrail rewrites the whole affected field to a neutral,
# user-confirmed statement before Review sees it.
DERIVED_LISTING_CLAIMS: tuple[tuple[str, str], ...] = (
    ("连接稳定", "蓝牙5.3"),
    ("抗干扰性能好", "蓝牙5.3"),
    ("抗干扰", "蓝牙5.3"),
    ("游戏体验更顺畅", "游戏低延迟"),
    ("提升游戏音画同步体验", "游戏低延迟"),
    ("音画同步提升", "游戏低延迟"),
    ("满足长时间游戏需求", "长续航"),
    ("满足长时间使用需求", "长续航"),
    ("充电片刻即可继续使用", "快充"),
    ("短暂充电即可", "快充"),
    ("游戏语音沟通清晰", "通话降噪"),
    ("通话更清晰", "通话降噪"),
)

# Qualitative conclusions are not product facts. Listing may introduce them even
# when the user only supplied concrete features, so replace them with those
# confirmed features instead of asking the user to change the request.
UNSUPPORTED_QUALITATIVE_CLAIMS: tuple[str, ...] = (
    "性能出色",
    "表现出色",
    "卓越性能",
    "性能卓越",
    "强劲性能",
    "稳定连接",
    "连接稳定",
    "清晰通话",
    "通话清晰",
    "音画同步",
    "声画同步",
    "电量焦虑",
    "功耗更低",
    "畅玩",
)

CONFIRMED_FEATURE_ALIASES: dict[str, tuple[str, ...]] = {
    "游戏低延迟": ("游戏低延迟", "游戏低延时", "低延迟模式", "低延时模式"),
    "蓝牙5.3": ("蓝牙5.3", "5.3蓝牙"),
}

UNCONFIRMED_PRODUCT_FACTS: tuple[str, ...] = (
    "主动降噪",
    "双麦克风",
    "三麦克风",
    "防水",
    "轻量佩戴",
    "佩戴稳固",
    "强低音",
)

PRODUCT_FORM_TERMS: tuple[str, ...] = (
    "头戴式",
    "入耳式",
    "半入耳式",
    "开放式",
    "耳夹式",
)


def _audit_entry(
    *,
    source_agent: str,
    field_path: str,
    issue_code: str,
    before: Any,
    after: Any,
    reason: str,
    evidence_refs: list[str],
) -> dict[str, Any]:
    payload = {
        "source_agent": source_agent,
        "field_path": field_path,
        "issue_code": issue_code,
        "before": before,
        "after": after,
        "reason": reason,
        "evidence_refs": evidence_refs,
        "method": "deterministic_semantic_guardrail",
        "status": "corrected",
        "before_hash": _value_hash(before),
        "after_hash": _value_hash(after),
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return {
        "correction_id": "correction_"
        + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12],
        **payload,
    }


def _value_hash(value: Any) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _append_corrections(
    result: dict[str, Any], corrections: list[dict[str, Any]]
) -> None:
    existing = list(result.get("semantic_corrections") or [])
    known_ids = {item.get("correction_id") for item in existing}
    existing.extend(
        item for item in corrections if item.get("correction_id") not in known_ids
    )
    result["semantic_corrections"] = existing


def _confirmed(value: str, confirmed_values: list[str]) -> bool:
    normalized = value.replace(" ", "").lower()
    return any(
        normalized in str(item).replace(" ", "").lower()
        or str(item).replace(" ", "").lower() in normalized
        for item in confirmed_values
        if str(item).strip()
    )


def _safe_feature_statement(feature: str) -> str:
    if feature == "游戏低延迟":
        return "支持游戏低延迟模式"
    return f"支持{feature}"


def confirmed_feature_statements(features: list[str]) -> list[str]:
    return [
        _safe_feature_statement(feature)
        for feature in list(dict.fromkeys(features))
        if str(feature).strip()
    ]


def _mentioned_confirmed_features(
    text: str, confirmed_features: list[str]
) -> list[str]:
    normalized_text = text.replace(" ", "").lower()
    return list(
        dict.fromkeys(
            feature
            for feature in confirmed_features
            if str(feature).strip()
            and any(
                alias.replace(" ", "").lower() in normalized_text
                for alias in CONFIRMED_FEATURE_ALIASES.get(
                    feature, (str(feature),)
                )
            )
        )
    )


def _listing_item_issue(
    text: str,
    *,
    confirmed_features: list[str],
    confirmed_product_form: str | None,
) -> tuple[str, str, str] | None:
    if any(phrase in text for phrase in UNSUPPORTED_QUALITATIVE_CLAIMS):
        replacement = "，".join(
            _safe_feature_statement(feature)
            for feature in list(dict.fromkeys(confirmed_features))[:3]
        )
        return (
            "derived_performance_claim",
            replacement,
            "模型生成了没有独立证据的概括性性能宣传，已改为用户确认的功能表述",
        )

    derived_features = [
        feature
        for phrase, feature in DERIVED_LISTING_CLAIMS
        if phrase in text
        and feature in text
        and _confirmed(feature, confirmed_features)
    ]
    if derived_features:
        safe_features = list(dict.fromkeys(derived_features))
        replacement = "，".join(
            _safe_feature_statement(feature) for feature in safe_features
        )
        return (
            "derived_performance_claim",
            replacement,
            "文案把已确认功能扩展成了未经测试的效果承诺，已改为中性功能表述",
        )

    confirmed_facts = [*confirmed_features]
    if confirmed_product_form:
        confirmed_facts.append(confirmed_product_form)
    for fact in UNCONFIRMED_PRODUCT_FACTS:
        if fact in text and not _confirmed(fact, confirmed_facts):
            return (
                "unsupported_product_claim",
                "",
                f"文案包含用户未确认的产品事实“{fact}”，已从可执行内容中移除",
            )
    for form in PRODUCT_FORM_TERMS:
        if form in text and not _confirmed(form, confirmed_facts):
            return (
                "unsupported_product_claim",
                "",
                f"文案包含用户未确认的产品形态“{form}”，已从可执行内容中移除",
            )
    return None


def normalize_listing_semantics(
    result: dict[str, Any],
    *,
    category: str,
    confirmed_features: list[str],
    confirmed_product_form: str | None,
) -> list[dict[str, Any]]:
    """Remove unverified implications while preserving confirmed product facts."""

    corrections: list[dict[str, Any]] = []
    for field_name in ("title", "keywords", "bullets"):
        values = (
            [str(result.get(field_name) or "")]
            if field_name == "title"
            else [str(item) for item in result.get(field_name, [])]
        )
        revised_values: list[str] = []
        for value in values:
            policy_value = re.sub(
                r"(?:全网)?第一|100%|最(?:佳|好|强|低|高)", "", value
            )
            if policy_value != value:
                policy_value = re.sub(r"\s+", " ", policy_value).strip(
                    " ，,。；;"
                )
                corrections.append(
                    _audit_entry(
                        source_agent="listing_agent",
                        field_path=f"listing.{field_name}",
                        issue_code="prohibited_marketing_claim",
                        before=value,
                        after=policy_value,
                        reason="已局部删除绝对化营销词，不重新生成整份商品方案",
                        evidence_refs=["policy.marketing_claims"],
                    )
                )
                value = policy_value
            else:
                # Formatting cleanup is not a semantic correction and must not
                # inflate the compliance audit count.
                value = re.sub(r"\s+", " ", value).strip()

            if field_name == "bullets":
                mentioned_features = _mentioned_confirmed_features(
                    value, confirmed_features
                )
                if mentioned_features:
                    projected_value = "，".join(
                        _safe_feature_statement(feature)
                        for feature in mentioned_features
                    )
                    if (
                        projected_value.replace(" ", "").rstrip("。")
                        != value.replace(" ", "").rstrip("。")
                    ):
                        corrections.append(
                            _audit_entry(
                                source_agent="listing_agent",
                                field_path="listing.bullets",
                                issue_code="confirmed_feature_projection",
                                before=value,
                                after=projected_value,
                                reason=(
                                    "卖点包含已确认功能之外的效果延伸，"
                                    "已投影为可追溯的用户确认事实"
                                ),
                                evidence_refs=[
                                    "task.constraints.confirmed_features"
                                ],
                            )
                        )
                        value = projected_value
            issue = _listing_item_issue(
                value,
                confirmed_features=confirmed_features,
                confirmed_product_form=confirmed_product_form,
            )
            if issue is None:
                if (
                    field_name == "bullets"
                    and confirmed_features
                    and not mentioned_features
                ):
                    corrections.append(
                        _audit_entry(
                            source_agent="listing_agent",
                            field_path="listing.bullets",
                            issue_code="ungrounded_generated_bullet",
                            before=value,
                            after="",
                            reason=(
                                "模型卖点未关联任何用户确认功能，"
                                "已从可执行商品文案中移除"
                            ),
                            evidence_refs=[
                                "task.constraints.confirmed_features"
                            ],
                        )
                    )
                    continue
                revised_values.append(value)
                continue
            issue_code, replacement, reason = issue
            if replacement:
                revised_values.append(replacement)
            corrections.append(
                _audit_entry(
                    source_agent="listing_agent",
                    field_path=f"listing.{field_name}",
                    issue_code=issue_code,
                    before=value,
                    after=replacement,
                    reason=reason,
                    evidence_refs=[
                        "task.constraints.confirmed_features",
                        "task.constraints.confirmed_product_form",
                    ],
                )
            )
        if field_name == "title":
            normalized_title = revised_values[0] if revised_values else ""
            if len(normalized_title) < 2:
                replacement = f"{category} 商品上新方案"
                corrections.append(
                    _audit_entry(
                        source_agent="listing_agent",
                        field_path="listing.title",
                        issue_code="listing_title_too_short",
                        before=normalized_title,
                        after=replacement,
                        reason="标题信息不足，已使用已确认品类生成中性标题",
                        evidence_refs=["task.constraints.category"],
                    )
                )
                normalized_title = replacement
            result[field_name] = normalized_title
        else:
            result[field_name] = list(dict.fromkeys(filter(None, revised_values)))

    if not result.get("keywords"):
        result["keywords"] = [category]
    if not result.get("bullets"):
        result["bullets"] = ["商品卖点仅采用商家已确认的信息"]
    _append_corrections(result, corrections)
    return corrections


def unresolved_listing_semantic_findings(
    listing: dict[str, Any],
    *,
    confirmed_features: list[str],
    confirmed_product_form: str | None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for field_name in ("title", "keywords", "bullets"):
        values = (
            [str(listing.get(field_name) or "")]
            if field_name == "title"
            else [str(item) for item in listing.get(field_name, [])]
        )
        for value in values:
            issue = _listing_item_issue(
                value,
                confirmed_features=confirmed_features,
                confirmed_product_form=confirmed_product_form,
            )
            if issue is None:
                continue
            issue_code, _, reason = issue
            findings.append(
                {
                    "code": (
                        "unsupported_product_claim"
                        if issue_code == "derived_performance_claim"
                        else issue_code
                    ),
                    "severity": "high",
                    "blocking": True,
                    "message": reason[:80],
                    "source_agent": "listing_agent",
                    "artifact_type": "listing",
                    "field_path": f"listing.{field_name}",
                    "claim_text": value[:80],
                    "suggested_action": "rewrite_claim",
                    "claim_origin": "agent_generated",
                    "user_action_required": False,
                }
            )
    return findings


def normalize_repairable_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """Map model wording variants onto the small repair protocol."""

    normalized = dict(finding)
    if not normalized.get("blocking") or normalized.get("severity") != "high":
        return normalized
    if normalized.get("code") != "execution_risk":
        return normalized
    if normalized.get("suggested_action") not in REPAIRABLE_CONTENT_ACTIONS:
        return normalized
    if normalized.get("field_path") not in REPAIRABLE_CONTENT_FIELDS:
        return normalized
    if not str(normalized.get("claim_text") or "").strip():
        return normalized
    normalized["code"] = (
        "discount_representation_mismatch"
        if normalized.get("suggested_action") == "fix_discount_representation"
        else "unsupported_product_claim"
    )
    return normalized


def is_repairable_finding(finding: dict[str, Any]) -> bool:
    return bool(
        finding.get("code") in REPAIRABLE_FINDING_CODES
        and finding.get("source_agent") in {"listing_agent", "strategy_agent"}
        and finding.get("field_path") in REPAIRABLE_CONTENT_FIELDS
        and finding.get("suggested_action") in REPAIRABLE_CONTENT_ACTIONS
        and str(finding.get("claim_text") or "").strip()
    )


def coupon_percentage_claim(text: str, coupon: float) -> str | None:
    """Return a phrase that describes an amount coupon as a percentage."""
    value = f"{coupon:g}"
    discount_words = r"(?:折扣|优惠|券|让利)"
    patterns = (
        rf"{re.escape(value)}\s*%[^，。；]{{0,10}}{discount_words}",
        rf"{discount_words}[^，。；]{{0,10}}{re.escape(value)}\s*%",
    )
    for pattern in patterns:
        if match := re.search(pattern, text):
            return match.group(0)
    return None


def mismatched_coupon_amount_claim(text: str, coupon: float) -> str | None:
    """Return a monetary discount phrase whose amount conflicts with tool output."""

    patterns = (
        r"(?:立减|优惠(?:券)?|让利|券)\s*(\d+(?:\.\d+)?)\s*元",
        r"(\d+(?:\.\d+)?)\s*元\s*(?:立减|优惠(?:券)?|让利|券)",
    )
    for pattern in patterns:
        if match := re.search(pattern, text):
            if abs(float(match.group(1)) - coupon) > 0.01:
                return match.group(0)
    return None


def _contains_percentage_discount(text: str) -> bool:
    discount_words = r"(?:折扣|优惠|券|让利)"
    return bool(
        re.search(rf"\d+(?:\.\d+)?\s*%[^，。；]{{0,12}}{discount_words}", text)
        or re.search(rf"{discount_words}[^，。；]{{0,12}}\d+(?:\.\d+)?\s*%", text)
    )


def _safe_launch_plan(result: dict[str, Any], category: str) -> str:
    coupon = float(result.get("coupon") or 0)
    margin = result.get("margin") or {}
    net_price = margin.get("net_price")
    planned_units = result.get("planned_units")
    parts = [f"围绕{category}品类定位执行首月上新方案"]
    if coupon > 0:
        parts.append(f"使用{coupon:g}元优惠券")
    else:
        parts.append("本次不设置优惠券")
    if net_price is not None:
        parts.append(f"预计到手价{float(net_price):g}元")
    if planned_units is not None:
        parts.append(f"首批计划投入{int(planned_units)}件")
    return "，".join(parts) + "。"


def findings_for_agent(
    findings: list[dict[str, Any]], agent_name: str
) -> list[dict[str, Any]]:
    return [
        finding
        for finding in findings
        if finding.get("source_agent") == agent_name
    ]


def scrub_claims(text: str, findings: list[dict[str, Any]], replacement: str) -> str:
    revised = text
    for finding in findings:
        if finding.get("code") not in CONTENT_FINDING_CODES:
            continue
        claim = str(finding.get("claim_text") or "").strip()
        if claim:
            revised = revised.replace(claim, replacement)
    revised = re.sub(r"([\"“'])\s*\1", "", revised)
    revised = re.sub(r"\s+", " ", revised)
    revised = re.sub(r"([，,。；;])\1+", r"\1", revised)
    return revised.strip(" ，,。；;")


def scrub_listing_result(
    result: dict[str, Any],
    findings: list[dict[str, Any]],
    *,
    category: str,
) -> bool:
    before = (
        result.get("title"),
        tuple(result.get("keywords", [])),
        tuple(result.get("bullets", [])),
    )
    result["title"] = scrub_claims(str(result.get("title") or ""), findings, category)
    result["keywords"] = [
        scrubbed
        for value in result.get("keywords", [])
        if (scrubbed := scrub_claims(str(value), findings, category))
    ]
    result["bullets"] = [
        scrubbed
        for value in result.get("bullets", [])
        if (scrubbed := scrub_claims(str(value), findings, category))
    ]
    if len(result["title"]) < 5:
        result["title"] = f"{category} 商品上新方案"
    if not result["keywords"]:
        result["keywords"] = [category]
    if not result["bullets"]:
        result["bullets"] = ["商品卖点仅采用商家已确认的信息"]
    after = (
        result["title"],
        tuple(result["keywords"]),
        tuple(result["bullets"]),
    )
    changed = before != after
    if changed:
        _append_corrections(
            result,
            [
                _audit_entry(
                    source_agent="listing_agent",
                    field_path="listing.content",
                    issue_code="review_directed_revision",
                    before={
                        "title": before[0],
                        "keywords": list(before[1]),
                        "bullets": list(before[2]),
                    },
                    after={
                        "title": after[0],
                        "keywords": list(after[1]),
                        "bullets": list(after[2]),
                    },
                    reason="根据 Review 的结构化反馈移除了未经确认或禁止的宣传内容",
                    evidence_refs=[
                        str(item.get("code")) for item in findings if item.get("code")
                    ],
                )
            ],
        )
    return changed


def scrub_strategy_result(
    result: dict[str, Any],
    findings: list[dict[str, Any]],
    *,
    category: str,
) -> bool:
    before = str(result.get("launch_plan") or "")
    revised = scrub_claims(before, findings, category)
    discount_findings = [
        finding
        for finding in findings
        if finding.get("code") in DISCOUNT_FINDING_CODES
    ]
    if discount_findings:
        coupon = float(result.get("coupon") or 0)
        replacement = f"{coupon:g}元优惠" if coupon > 0 else "不设置优惠"
        for finding in discount_findings:
            claim = str(finding.get("claim_text") or "").strip()
            if claim:
                revised = revised.replace(claim, replacement)
        value = re.escape(f"{coupon:g}")
        revised = re.sub(
            rf"(?:折扣|优惠|券|让利)[^，。；]{{0,10}}{value}\s*%",
            replacement,
            revised,
        )
        revised = re.sub(
            rf"{value}\s*%[^，。；]{{0,10}}(?:折扣|优惠|券|让利)",
            replacement,
            revised,
        )
        if _contains_percentage_discount(revised):
            revised = _safe_launch_plan(result, category)
    result["launch_plan"] = revised
    if len(result["launch_plan"]) < 5:
        result["launch_plan"] = f"围绕{category}品类定位执行本次定价和投放方案。"
    changed = before != result["launch_plan"]
    if changed:
        _append_corrections(
            result,
            [
                _audit_entry(
                    source_agent="strategy_agent",
                    field_path="strategy.launch_plan",
                    issue_code="review_directed_revision",
                    before=before,
                    after=result["launch_plan"],
                    reason="根据 Review 的结构化反馈修正了策略宣传或优惠单位",
                    evidence_refs=[
                        str(item.get("code")) for item in findings if item.get("code")
                    ],
                )
            ],
        )
    return changed


def _replace_verified_number_claims(
    text: str,
    *,
    coupon: float,
    net_price: float | None,
    margin_rate: float | None,
) -> tuple[str, list[str]]:
    revised = text
    reasons: list[str] = []
    while claim := coupon_percentage_claim(revised, coupon):
        replacement = f"{coupon:g}元优惠" if coupon > 0 else "不设置优惠"
        revised = revised.replace(claim, replacement, 1)
        reasons.append("discount_unit")
    while claim := mismatched_coupon_amount_claim(revised, coupon):
        replacement = f"{coupon:g}元优惠" if coupon > 0 else "不设置优惠"
        revised = revised.replace(claim, replacement, 1)
        reasons.append("discount_amount")

    if net_price is not None:
        pattern = re.compile(r"(?:预计)?到手价\s*(\d+(?:\.\d+)?)\s*元")
        for match in list(pattern.finditer(revised)):
            if abs(float(match.group(1)) - net_price) > 0.01:
                revised = revised.replace(
                    match.group(0), f"预计到手价{net_price:g}元", 1
                )
                reasons.append("net_price")

    if margin_rate is not None:
        expected_percent = round(margin_rate * 100, 2)
        pattern = re.compile(r"毛利率(?:约|为)?\s*(\d+(?:\.\d+)?)\s*%")
        for match in list(pattern.finditer(revised)):
            if abs(float(match.group(1)) - expected_percent) > 0.11:
                revised = revised.replace(
                    match.group(0), f"毛利率{expected_percent:g}%", 1
                )
                reasons.append("margin_rate")
    return revised, reasons


def enforce_verified_strategy_numbers(
    result: dict[str, Any], *, category: str
) -> bool:
    """Align model prose with authoritative pricing and inventory tool results."""

    coupon = float(result.get("coupon") or 0)
    margin = result.get("margin") or {}
    net_price = (
        float(margin["net_price"]) if margin.get("net_price") is not None else None
    )
    margin_rate = (
        float(margin["margin_rate"])
        if margin.get("margin_rate") is not None
        else None
    )
    corrections: list[dict[str, Any]] = []
    changed = False
    for field_name in ("launch_plan", "strategy_rationale"):
        before = str(result.get(field_name) or "")
        revised, reasons = _replace_verified_number_claims(
            before,
            coupon=coupon,
            net_price=net_price,
            margin_rate=margin_rate,
        )
        if field_name == "launch_plan" and len(revised) < 5:
            revised = _safe_launch_plan(result, category)
        result[field_name] = revised
        if before == revised:
            continue
        changed = True
        issue_code = (
            "discount_representation_mismatch"
            if {"discount_unit", "discount_amount"} & set(reasons)
            else "margin_inconsistency"
        )
        corrections.append(
            _audit_entry(
                source_agent="strategy_agent",
                field_path=f"strategy.{field_name}",
                issue_code=issue_code,
                before=before,
                after=revised,
                reason=(
                    "模型策略中的优惠单位或价格表述与工具计算结果不一致，"
                    "已以经过校验的金额、到手价和毛利率为准"
                ),
                evidence_refs=[
                    "strategy.coupon",
                    "strategy.margin.net_price",
                    "strategy.margin.margin_rate",
                ],
            )
        )
    _append_corrections(result, corrections)
    return changed


def strategy_consistency_findings(
    strategy: dict[str, Any], constraints: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return only unresolved contradictions; upstream corrections remain non-blocking audit."""

    findings: list[dict[str, Any]] = []
    coupon = float(strategy.get("coupon") or 0)
    for field_name in ("launch_plan", "strategy_rationale"):
        value = str(strategy.get(field_name) or "")
        if claim := coupon_percentage_claim(value, coupon):
            findings.append(
                {
                    "code": "discount_representation_mismatch",
                    "severity": "high",
                    "blocking": True,
                    "message": "促销文案仍把金额优惠写成百分比，需要按工具结果修正",
                    "source_agent": "strategy_agent",
                    "artifact_type": "strategy",
                    "field_path": f"strategy.{field_name}",
                    "claim_text": claim,
                    "suggested_action": "fix_discount_representation",
                    "claim_origin": "agent_generated",
                    "user_action_required": False,
                }
            )
        if claim := mismatched_coupon_amount_claim(value, coupon):
            findings.append(
                {
                    "code": "discount_representation_mismatch",
                    "severity": "high",
                    "blocking": True,
                    "message": "促销文案中的金额优惠与可信工具结果不一致",
                    "source_agent": "strategy_agent",
                    "artifact_type": "strategy",
                    "field_path": f"strategy.{field_name}",
                    "claim_text": claim,
                    "suggested_action": "fix_discount_representation",
                    "claim_origin": "agent_generated",
                    "user_action_required": False,
                }
            )

    price = float(strategy.get("price") or 0)
    margin = strategy.get("margin") or {}
    expected_cost = float(constraints.get("cost") or 0)
    expected_net = price - coupon
    actual_values = {
        "price": float(margin.get("price") or 0),
        "discount": float(margin.get("discount") or 0),
        "net_price": float(margin.get("net_price") or 0),
        "cost": float(margin.get("cost") or 0),
        "margin": float(margin.get("margin") or 0),
        "margin_rate": float(margin.get("margin_rate") or 0),
    }
    expected_margin = expected_net - expected_cost
    expected_rate = round(expected_margin / expected_net, 4) if expected_net > 0 else 0
    expected_values = {
        "price": price,
        "discount": coupon,
        "net_price": expected_net,
        "cost": expected_cost,
        "margin": expected_margin,
        "margin_rate": expected_rate,
    }
    if any(
        abs(actual_values[key] - expected_values[key]) > 0.011
        for key in expected_values
    ):
        findings.append(
            {
                "code": "margin_inconsistency",
                "severity": "high",
                "blocking": True,
                "message": "策略金额与毛利结构不一致，需要用可信输入重新计算",
                "source_agent": "strategy_agent",
                "artifact_type": "strategy",
                "field_path": "strategy.margin",
                "claim_text": "毛利计算结果",
                "suggested_action": "recalculate_strategy",
                "claim_origin": "agent_generated",
                "user_action_required": False,
            }
        )

    planned_units = int(strategy.get("planned_units") or 0)
    inventory = int(constraints.get("inventory") or 0)
    check = strategy.get("inventory_check") or {}
    expected_inventory = {
        "inventory": inventory,
        "planned_units": planned_units,
        "valid": planned_units <= inventory,
        "remaining": inventory - planned_units,
    }
    if any(check.get(key) != value for key, value in expected_inventory.items()):
        findings.append(
            {
                "code": "inventory_inconsistency",
                "severity": "high",
                "blocking": True,
                "message": "策略库存结果与可信库存输入不一致，需要重新校验",
                "source_agent": "strategy_agent",
                "artifact_type": "strategy",
                "field_path": "strategy.inventory_check",
                "claim_text": "库存校验结果",
                "suggested_action": "recalculate_strategy",
                "claim_origin": "agent_generated",
                "user_action_required": False,
            }
        )
    return findings
