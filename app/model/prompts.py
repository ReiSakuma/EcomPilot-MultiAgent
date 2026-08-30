from __future__ import annotations

import json
from typing import Any

from app.context.schemas import ContextPackage


def listing_prompt(
    context: ContextPackage,
    *,
    confirmed_features: list[str] | None = None,
    confirmed_product_form: str | None = None,
    revision_feedback: list[dict[str, Any]] | None = None,
    previous_listing: dict[str, Any] | None = None,
) -> str:
    confirmed_features = confirmed_features or []
    revision_feedback = revision_feedback or []
    revision_instruction = ""
    if revision_feedback:
        revision_instruction = (
            "这是一次审核后的受控修订。必须删除或改写每一条审核指出的无依据宣传，"
            "返回完整替换版本，不要解释修改过程。\n"
            f"审核反馈:{json.dumps(revision_feedback, ensure_ascii=False, separators=(',', ':'))}\n"
            f"上一版文案:{json.dumps(previous_listing or {}, ensure_ascii=False, separators=(',', ':'))}\n"
        )
    return (
        "你是电商 Listing Agent。请基于上下文生成标题、关键词、卖点和合规说明。"
        "竞品特征只能用于市场定位，不能写成当前商品自身功能。"
        "商品功能性、材质、佩戴体验、兼容平台和性能宣传，只能来自用户明确确认的信息；"
        "没有确认的内容必须省略，不得合理猜测。"
        "标题必须非空且不超过120个字符；品类名称本身表达完整时可以直接作为标题，"
        "不得为了凑标题长度编造产品功能或效果。"
        f"用户确认的产品功能:{json.dumps(confirmed_features, ensure_ascii=False)}。"
        f"用户确认的产品形态:{json.dumps(confirmed_product_form, ensure_ascii=False)}。"
        "只能输出 JSON object，字段必须包含 title, keywords, bullets, compliance_notes。\n"
        f"{revision_instruction}\n上下文:\n{context.text}"
    )


def strategy_prompt(
    context: ContextPackage | dict[str, Any],
    *,
    confirmed_features: list[str] | None = None,
    confirmed_product_form: str | None = None,
    revision_feedback: list[dict[str, Any]] | None = None,
    previous_strategy: dict[str, Any] | None = None,
) -> str:
    confirmed_features = confirmed_features or []
    revision_feedback = revision_feedback or []
    revision_instruction = ""
    if revision_feedback:
        revision_instruction = (
            "这是一次审核后的受控策略修订。只修改促销方案中的表达，不重新计算价格、"
            "优惠、毛利或库存。必须删除或改写每一条审核指出的无依据宣传，"
            "返回完整替换版本，不要解释修改过程。\n"
            f"审核反馈:{json.dumps(revision_feedback, ensure_ascii=False, separators=(',', ':'))}\n"
            f"上一版策略:{json.dumps(previous_strategy or {}, ensure_ascii=False, separators=(',', ':'))}\n"
        )
    context_text = (
        context.text
        if isinstance(context, ContextPackage)
        else json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )
    return (
        "你是电商 Strategy Agent。请基于上下文制定促销策略。"
        "毛利、库存、优惠计算必须交给确定性工具。"
        "产品功能、产品形态和性能宣传只能来自用户明确确认的信息；"
        "运营目标中的愿望不能当作已确认产品事实。"
        "如果确认功能列表为空，不得声称存在已确认卖点，只能描述品类定位、价格、库存和投放安排。"
        f"用户确认的产品功能:{json.dumps(confirmed_features, ensure_ascii=False)}。"
        f"用户确认的产品形态:{json.dumps(confirmed_product_form, ensure_ascii=False)}。"
        "只能输出 JSON object，字段包含 launch_plan 和 rationale。\n\n"
        f"{revision_instruction}上下文:\n{context_text}"
    )


def review_prompt(context: ContextPackage | dict[str, Any]) -> str:
    context_text = (
        context.text
        if isinstance(context, ContextPackage)
        else json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )
    return (
        "你是电商语义审核器，只查确定性规则难以识别的三类问题：无依据商品宣传、"
        "禁用营销表达、执行风险。价格、优惠、毛利和库存由程序负责，不得计算或评价。"
        "待审Listing和Strategy是系统生成物，不是用户原话；发现其中的宣传问题时应定位"
        "到生成字段，系统会自动局部删除，不得要求用户修改其原始业务条件。"
        "产品形态未提供时可以省略，省略本身不是执行风险；只有生成内容声称了未确认形态才报告。"
        "每个问题必须逐字引用实际字段中存在的claim_text，并准确给出field_path。"
        "最多3条；没有问题返回issues空数组。不要解释，不要复述背景，只输出JSON。\n"
        f"待审数据:{context_text}"
    )
