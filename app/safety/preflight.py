from __future__ import annotations

import re
from typing import Any

from app.copilot.intents import PreflightIssue
from app.safety.content_revision import PRODUCT_FORM_TERMS, UNCONFIRMED_PRODUCT_FACTS


PROMPT_INJECTION_PATTERN = re.compile(
    r"忽略(?:之前|以上|系统).{0,12}(?:指令|规则|要求)"
    r"|绕过.{0,12}(?:审核|审批|安全|权限|规则)"
    r"|(?:输出|泄露|展示).{0,12}(?:系统提示|隐藏指令|密钥|API\s*key)"
    r"|(?:假装|扮演).{0,12}(?:管理员|系统|开发者).{0,12}(?:绕过|取消|关闭)",
    re.I,
)

_CLAIM_REQUEST_PATTERN = re.compile(
    r"(?:但是|但|然而)?\s*(?:请|要求|务必).{0,16}"
    r"(?:标题|文案|商品页|详情页|宣传).{0,10}"
    r"(?:写|加入|添加|声称|标注|宣传|突出)\s*[：:]?\s*([^。；;]+)",
    re.I,
)

_PROHIBITED_MARKETING_TERMS = (
    "电竞专用",
    "医用级",
    "专业级",
    "全网最低",
    "销量第一",
    "行业第一",
    "绝对降噪",
    "零延迟",
)


def evaluate_listing_preflight(
    text: str,
    payload: dict[str, Any],
    *,
    semantic_claims: list[str] | None = None,
    semantic_prompt_injection: bool = False,
) -> tuple[list[PreflightIssue], list[str]]:
    """Evaluate cheap write-admission rules before any specialist workflow starts."""

    issues: list[PreflightIssue] = []
    confirmed = [str(item).strip() for item in payload.get("confirmed_features") or [] if str(item).strip()]
    rejected_claims = _requested_unverified_claims(text, confirmed)
    for claim in semantic_claims or []:
        normalized = str(claim).strip()
        if (
            normalized
            and normalized in text
            and not _is_confirmed(normalized, confirmed)
            and normalized not in rejected_claims
        ):
            rejected_claims.append(normalized)

    if rejected_claims:
        issues.append(
            PreflightIssue(
                code="unverified_product_claim",
                category="content_safety",
                field_path="request.requested_marketing_claims",
                message=(
                    "请求要求把未确认的产品能力写入商品宣传；这些内容不能进入可执行方案。"
                ),
                evidence=rejected_claims,
            )
        )

    if PROMPT_INJECTION_PATTERN.search(text) or semantic_prompt_injection:
        issues.append(
            PreflightIssue(
                code="prompt_injection",
                category="security",
                field_path="request.message",
                message="检测到试图覆盖安全规则、权限或隐藏指令的内容，已拒绝启动业务工作流。",
                evidence=["prompt_injection_pattern"],
            )
        )

    conflicts = _conflicting_numeric_fields(text)
    capacity_conflict = _inventory_plan_conflict(text, payload)
    if capacity_conflict:
        conflicts.append(capacity_conflict)
    if conflicts:
        message = "同一请求中存在互相矛盾的关键业务数值，需要先确认唯一值。"
        if capacity_conflict:
            message += "计划投入量超过可用库存，也需要调整。"
        issues.append(
            PreflightIssue(
                code="conflicting_business_fields",
                category="input_contract",
                field_path="request.business_fields",
                message=message,
                evidence=conflicts,
            )
        )

    cost = payload.get("cost")
    price = payload.get("target_price")
    minimum = payload.get("min_margin_rate")
    if cost is not None and price is not None:
        actual = (float(price) - float(cost)) / float(price) if float(price) > 0 else -1.0
        required = float(minimum) if minimum is not None else 0.0
        if float(cost) >= float(price) or actual + 1e-9 < required:
            issues.append(
                PreflightIssue(
                    code="margin_infeasible",
                    category="business_rule",
                    field_path="request.target_price",
                    message=(
                        f"按成本{float(cost):g}元和售价{float(price):g}元计算，"
                        f"最高基础毛利率为{actual:.2%}，低于要求的{required:.2%}。"
                    ),
                    evidence=[
                        f"cost={float(cost):g}",
                        f"target_price={float(price):g}",
                        f"min_margin_rate={required:g}",
                    ],
                )
            )
    return issues, rejected_claims


def sanitize_listing_fields(
    fields: dict[str, Any],
) -> tuple[dict[str, Any], list[PreflightIssue], set[str]]:
    """Remove invalid primitive values before Pydantic builds the typed request."""

    sanitized = dict(fields)
    issues: list[PreflightIssue] = []
    invalid_fields: set[str] = set()
    checks = (
        ("cost", lambda value: float(value) >= 0, "单件成本不能是负数。"),
        ("target_price", lambda value: float(value) > 0, "目标售价必须大于0元。"),
        ("inventory", lambda value: int(value) >= 0, "可用库存不能是负数。"),
        (
            "min_margin_rate",
            lambda value: 0 <= float(value) < 1,
            "最低毛利率必须大于等于0%且小于100%。",
        ),
    )
    for field_name, predicate, message in checks:
        value = fields.get(field_name)
        if value is None or predicate(value):
            continue
        invalid_fields.add(field_name)
        sanitized[field_name] = None
        issues.append(
            PreflightIssue(
                code="invalid_field_value",
                category="input_contract",
                field_path=f"request.{field_name}",
                message=message,
                evidence=[f"{field_name}={value}"],
            )
        )
    return sanitized, issues, invalid_fields


def prompt_injection_detected(text: str) -> bool:
    return bool(PROMPT_INJECTION_PATTERN.search(text))


def _requested_unverified_claims(text: str, confirmed: list[str]) -> list[str]:
    match = _CLAIM_REQUEST_PATTERN.search(text)
    if not match:
        return []
    requested = match.group(1).strip(" ，,、")
    candidates: list[str] = []
    known_terms = (*UNCONFIRMED_PRODUCT_FACTS, *PRODUCT_FORM_TERMS, *_PROHIBITED_MARKETING_TERMS)
    for term in known_terms:
        if term in requested and not _is_confirmed(term, confirmed):
            candidates.append(term)
    for match in re.finditer(
        r"\d+(?:\.\d+)?\s*(?:小时|天|分钟|毫秒|ms|米|级|W|w)\s*[\u4e00-\u9fffA-Za-z0-9.]*",
        requested,
        re.I,
    ):
        claim = match.group(0).strip()
        if claim and not _is_confirmed(claim, confirmed):
            candidates.append(claim)
    return list(dict.fromkeys(candidates))


def _is_confirmed(value: str, confirmed: list[str]) -> bool:
    normalized = re.sub(r"\s+", "", value).lower()
    return any(
        normalized == re.sub(r"\s+", "", item).lower()
        for item in confirmed
        if item.strip()
    )


def _conflicting_numeric_fields(text: str) -> list[str]:
    patterns = {
        "cost": r"成本\s*(-?\d+(?:\.\d+)?)",
        "target_price": r"(?:目标售价|计划售价|售价|定价)\s*(?:为|是)?\s*(-?\d+(?:\.\d+)?)",
        "inventory": r"库存\s*(-?\d+)",
        "min_margin_rate": r"(?:最低毛利率|毛利率(?:不能低于|不低于|至少))\s*(?:要求|为)?\s*(-?\d+(?:\.\d+)?)\s*%",
    }
    conflicts: list[str] = []
    for field, pattern in patterns.items():
        values = list(dict.fromkeys(re.findall(pattern, text)))
        if len(values) > 1:
            conflicts.append(f"{field}={','.join(values)}")
    return conflicts


def _inventory_plan_conflict(text: str, payload: dict[str, Any]) -> str | None:
    planned = re.search(
        r"(?:计划投入|首批投入|投入|只能投入)\s*(\d+)\s*(?:件|个)", text
    )
    inventory = payload.get("inventory")
    if not planned or inventory is None:
        return None
    planned_units = int(planned.group(1))
    available = int(inventory)
    if planned_units <= available:
        return None
    return f"planned_units={planned_units}>inventory={available}"
