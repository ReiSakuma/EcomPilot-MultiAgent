from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any

from app.tools.market_data import (
    load_keyword_rules,
    load_products,
    load_reviews,
    normalize_market_category,
)
from app.tools.market_statistics import clean_market_price_samples
from app.tools.market_layers import ComparableMarketInput, classify_market_layers


PAIN_POINT_RULES = {
    "佩戴舒适度": ["戴久", "胀", "偏大", "尺寸", "压耳", "佩戴舒适度"],
    "连接稳定性": ["断连", "连接", "卡", "恢复", "连接有时慢"],
    "续航真实性": ["续航缩水", "续航宣传", "比预期短"],
    "降噪一般": ["降噪一般", "噪音"],
    "价格敏感": ["价格偏高", "学生党有点犹豫"],
    "便携性": ["携带不太方便", "耳机仓有点大"],
    "键盘噪音": ["声音偏响", "噪音明显"],
    "键帽耐用性": ["键帽", "油印"],
    "键盘连接稳定性": ["无线模式", "唤醒较慢"],
    "配置易用性": ["软件设置", "不够直观"],
}


def search_products(category: str, target_audience: str | None = None) -> list[dict[str, object]]:
    products = load_products(category)
    if target_audience:
        normalized_target = _normalize_audience(target_audience)
        audience_matches = [
            product
            for product in products
            if any(
                normalized_target in _normalize_audience(str(audience))
                or _normalize_audience(str(audience)) in normalized_target
                for audience in product.get("target_audience", [])
            )
        ]
        # Audience is a ranking hint. Missing a slice must not masquerade as missing
        # category data, so retain the category corpus when no audience label matches.
        if audience_matches:
            products = audience_matches
    return products


def _normalize_audience(value: str) -> str:
    normalized = value.strip().replace("大学生", "学生")
    for suffix in ("用户", "人群", "群体", "消费者", "爱好者"):
        normalized = normalized.replace(suffix, "")
    return normalized


def search_keywords(category: str, audience: str | None = None) -> list[str]:
    rules = load_keyword_rules()
    keywords = list(rules.get(normalize_market_category(category), []))
    if audience and ("学生" in audience or "大学生" in audience):
        keywords.extend(rules.get("学生", []))
    return list(dict.fromkeys(keywords))


def get_reviews(category: str, product_ids: list[str] | None = None) -> list[dict[str, Any]]:
    reviews = load_reviews(category)
    if product_ids is not None:
        allowed = set(product_ids)
        reviews = [review for review in reviews if review.get("product_id") in allowed]
    return reviews


def analyze_review_pain_points(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    evidence: dict[str, list[str]] = {}
    for review in reviews:
        text = str(review.get("text", ""))
        for pain_point, patterns in PAIN_POINT_RULES.items():
            if any(pattern in text for pattern in patterns):
                counts[pain_point] += 1
                evidence.setdefault(pain_point, []).append(str(review.get("product_id")))
    return {
        "pain_points": [item for item, _ in counts.most_common(6)],
        "pain_point_counts": dict(counts.most_common()),
        "pain_point_evidence": evidence,
    }


def analyze_feature_frequency(products: list[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for product in products:
        counts.update(product.get("features", []))
    return {
        "top_features": [item for item, _ in counts.most_common(8)],
        "feature_counts": dict(counts.most_common()),
    }


def build_market_report(
    category: str,
    target_audience: str | None = None,
    confirmed_features: list[str] | None = None,
    confirmed_product_form: str | None = None,
    channel: str = "general_ecommerce",
    condition: str = "new",
    brand_tier: str = "mass_market",
) -> dict[str, Any]:
    raw_products = search_products(category)
    raw_product_ids = [str(product["id"]) for product in raw_products]
    raw_reviews = get_reviews(category, product_ids=raw_product_ids)
    statistics = clean_market_price_samples(raw_products)
    layers = classify_market_layers(
        ComparableMarketInput(
            category=normalize_market_category(category),
            confirmed_product_form=confirmed_product_form,
            confirmed_features=tuple(confirmed_features or ()),
            target_audience=target_audience,
            channel=channel,
            condition=condition,
            brand_tier=brand_tier,
        ),
        raw_products,
        raw_reviews,
        statistics,
    )
    retained_prices = {
        decision.sample_id: decision.normalized_price
        for decision in statistics.decisions
        if not decision.excluded and decision.normalized_price is not None
    }
    products = [
        {
            **product,
            "price": retained_prices[str(product["id"])],
            "normalized_market_price": retained_prices[str(product["id"])],
        }
        for product in raw_products
        if str(product["id"]) in retained_prices
    ]
    valid_product_ids = [str(product["id"]) for product in products]
    valid_reviews = get_reviews(category, product_ids=valid_product_ids)
    core_ids = set(layers.core_comparable.product_ids)
    core_products = [product for product in products if str(product["id"]) in core_ids]
    core_reviews = get_reviews(category, product_ids=list(core_ids))
    evidence_products = core_products or products
    evidence_reviews = core_reviews or valid_reviews
    core_distribution = layers.core_comparable.price_distribution
    feature_report = analyze_feature_frequency(evidence_products)
    pain_report = analyze_review_pain_points(evidence_reviews)
    keywords = search_keywords(category, target_audience)
    sorted_products = sorted(
        products,
        key=lambda item: int(item.get("monthly_sales", 0)),
        reverse=True,
    )
    return {
        "sample_size": {
            "competitors": len(core_products),
            "reviews": len(core_reviews),
            "raw_competitors": len(raw_products),
            "raw_reviews": len(raw_reviews),
            "valid_competitors": len(products),
            "valid_reviews": len(valid_reviews),
            "adjacent_competitors": layers.adjacent_tier.sample_count,
            "adjacent_reviews": layers.adjacent_tier.review_count,
            "excluded_competitors": statistics.excluded_count,
        },
        "price_band": [
            core_distribution.minimum or 0,
            core_distribution.maximum or 0,
        ],
        "median_price": core_distribution.median or 0,
        "mean_price": core_distribution.mean or 0,
        "core_reference_price": layers.core_reference_price,
        "reference_method": layers.reference_method,
        "full_market_band": [
            layers.full_valid_market.price_distribution.minimum or 0,
            layers.full_valid_market.price_distribution.maximum or 0,
        ],
        "raw_price_band": [
            statistics.raw_distribution.minimum or 0,
            statistics.raw_distribution.maximum or 0,
        ],
        "market_statistics": statistics.model_dump(mode="json"),
        "market_layers": layers.model_dump(mode="json"),
        "top_features": feature_report["top_features"],
        "feature_counts": feature_report["feature_counts"],
        "pain_points": pain_report["pain_points"],
        "pain_point_counts": pain_report["pain_point_counts"],
        "pain_point_evidence": pain_report["pain_point_evidence"],
        "competitors": [
            product for product in sorted_products if str(product["id"]) in core_ids
        ][:8],
        "keywords": keywords,
        "evidence_refs": raw_product_ids,
    }
