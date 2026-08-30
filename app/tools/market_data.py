from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import DATA_DIR


MARKET_CATEGORY_ALIASES = {
    "耳机": "无线耳机",
    "蓝牙耳机": "无线耳机",
    "游戏耳机": "无线耳机",
    "头戴式耳机": "无线耳机",
    "游戏无线耳机": "无线耳机",
    "键盘": "机械键盘",
    "游戏键盘": "机械键盘",
    "无线键盘": "机械键盘",
    "电竞键盘": "机械键盘",
}

MARKET_DATASETS = {
    "无线耳机": {
        "products": "wireless_earbuds_competitors.json",
        "reviews": "wireless_earbuds_reviews.json",
    },
    "机械键盘": {
        "products": "mechanical_keyboards_competitors.json",
        "reviews": "mechanical_keyboards_reviews.json",
    },
}

TEST_ONLY_PRODUCT_FIELDS = frozenset(
    {
        "test_case",
        "expected_statistical_flag",
        "expected_excluded",
        "expected_market_layer",
    }
)


def normalize_market_category(category: str) -> str:
    normalized = category.strip()
    return MARKET_CATEGORY_ALIASES.get(normalized, normalized)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_products(category: str) -> list[dict[str, Any]]:
    dataset = MARKET_DATASETS.get(normalize_market_category(category))
    if not dataset:
        return []
    products = load_json(DATA_DIR / "products" / dataset["products"])
    return [
        {key: value for key, value in product.items() if key not in TEST_ONLY_PRODUCT_FIELDS}
        for product in products
    ]


def load_reviews(category: str) -> list[dict[str, Any]]:
    dataset = MARKET_DATASETS.get(normalize_market_category(category))
    return load_json(DATA_DIR / "reviews" / dataset["reviews"]) if dataset else []


def load_keyword_rules() -> dict[str, list[str]]:
    return load_json(DATA_DIR / "products" / "keyword_rules.json")
