from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from pathlib import Path
from statistics import mean, median

import pytest

from app.sql.database import SQL_DATASET_VERSION, MarketDatabase
from app.sql.service import MarketSqlService
from app.orchestration.workflow import run_workflow
from app.tools.market_data import load_products


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_DIR = ROOT / "data" / "products"
REVIEW_DIR = ROOT / "data" / "reviews"
REPORT_PATH = ROOT / "reports" / "v51" / "market_fixture_distribution.json"

DATASETS = {
    "无线耳机": (
        PRODUCT_DIR / "wireless_earbuds_competitors.json",
        REVIEW_DIR / "wireless_earbuds_reviews.json",
    ),
    "机械键盘": (
        PRODUCT_DIR / "mechanical_keyboards_competitors.json",
        REVIEW_DIR / "mechanical_keyboards_reviews.json",
    ),
}

TEST_ONLY_FIELDS = {
    "test_case",
    "expected_statistical_flag",
    "expected_excluded",
    "expected_market_layer",
}


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _statistical_flags(products: list[dict]) -> dict[str, bool]:
    prices = sorted(float(product["price"]) for product in products)
    half = len(prices) // 2
    q1 = median(prices[:half])
    q3 = median(prices[half:])
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr

    log_prices = [math.log(price) for price in prices]
    log_median = median(log_prices)
    mad = median(abs(price - log_median) for price in log_prices)
    flags: dict[str, bool] = {}
    for product in products:
        price = float(product["price"])
        modified_z = (
            abs(0.6745 * (math.log(price) - log_median) / mad) if mad else 0.0
        )
        flags[str(product["id"])] = (
            modified_z > 3.5 or price < lower or price > upper
        )
    return flags


@pytest.mark.parametrize("category", DATASETS)
def test_v51_each_category_has_controlled_94_2_4_fixture(category: str) -> None:
    product_path, review_path = DATASETS[category]
    products = _load(product_path)
    reviews = _load(review_path)

    assert len(products) == 100
    assert len(reviews) == 200
    assert len({product["id"] for product in products}) == 100
    assert Counter(product["test_case"] for product in products) == {
        "regular": 94,
        "dirty_outlier": 2,
        "explainable_extreme": 4,
    }
    product_ids = {product["id"] for product in products}
    assert all(review["product_id"] in product_ids for review in reviews)
    assert all(float(product["price"]) > 0 for product in products)
    assert all(product["currency"] == "CNY" for product in products)


@pytest.mark.parametrize("category", DATASETS)
def test_v51_special_prices_are_real_statistical_edge_cases(category: str) -> None:
    products = _load(DATASETS[category][0])
    flags = _statistical_flags(products)
    special = [product for product in products if product["test_case"] != "regular"]

    assert all(product["expected_statistical_flag"] for product in special)
    assert all(flags[product["id"]] for product in special)
    assert mean(float(product["price"]) for product in products) > median(
        float(product["price"]) for product in products
    ) * 1.3


@pytest.mark.parametrize("category", DATASETS)
def test_v51_dirty_and_explainable_extremes_have_different_expected_actions(
    category: str,
) -> None:
    products = _load(DATASETS[category][0])
    dirty = [product for product in products if product["test_case"] == "dirty_outlier"]
    explainable = [
        product for product in products if product["test_case"] == "explainable_extreme"
    ]

    assert len(dirty) == 2
    assert all(product["expected_excluded"] for product in dirty)
    assert all(product["expected_market_layer"] == "excluded" for product in dirty)
    assert all(product.get("data_quality_flags") for product in dirty)

    assert len(explainable) == 4
    assert all(not product["expected_excluded"] for product in explainable)
    assert all(product["expected_market_layer"] == "adjacent_tier" for product in explainable)
    assert all(product.get("price_explanation") for product in explainable)
    assert {product["sales_context"] for product in explainable} >= {
        "clearance",
        "refurbished_clearance",
        "regular",
    }
    assert {product["brand_tier"] for product in explainable} >= {
        "entry",
        "premium",
    }


@pytest.mark.parametrize("category", DATASETS)
def test_v51_runtime_loader_does_not_leak_offline_answers(category: str) -> None:
    runtime_products = load_products(category)

    assert len(runtime_products) == 100
    assert all(not TEST_ONLY_FIELDS.intersection(product) for product in runtime_products)
    assert any(product.get("sales_context") == "clearance" for product in runtime_products)
    assert any(product.get("brand_tier") == "premium" for product in runtime_products)
    assert any(product.get("data_quality_flags") for product in runtime_products)


def test_v51_distribution_report_matches_generated_files() -> None:
    report = _load(REPORT_PATH)

    assert report["dataset_version"] == SQL_DATASET_VERSION == "tenant-market-v4"
    for category, (product_path, _) in DATASETS.items():
        products = _load(product_path)
        prices = [float(product["price"]) for product in products]
        assert report[category]["product_count"] == 100
        assert report[category]["price_min"] == min(prices)
        assert report[category]["price_max"] == max(prices)
        assert report[category]["price_median"] == median(prices)
        assert report[category]["test_case_counts"] == {
            "regular": 94,
            "explainable_extreme": 4,
            "dirty_outlier": 2,
        }


def test_v51_sql_dataset_rebuilds_v3_and_contains_both_categories(tmp_path: Path) -> None:
    database_path = tmp_path / "market.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE dataset_metadata (id INTEGER PRIMARY KEY, dataset_version TEXT)"
    )
    connection.execute("INSERT INTO dataset_metadata VALUES (1, 'tenant-market-v3')")
    connection.commit()
    connection.close()

    MarketDatabase(database_path).ensure_initialized()
    service = MarketSqlService(database_path)
    result = service.query(
        "SELECT category, COUNT(*) AS product_count FROM products GROUP BY category"
    )

    assert result["dataset_version"] == "tenant-market-v4"
    assert result["rows"] == [
        {"category": "无线耳机", "product_count": 100},
        {"category": "机械键盘", "product_count": 100},
    ]
    assert service.schema_catalog()["dataset"] == "tenant-market-v4"


def test_added_fixture_metadata_does_not_displace_agent_memory() -> None:
    state = run_workflow(
        "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，"
        "库存 800 件，毛利率不能低于 25%。",
        approved=True,
    )

    assert state.status == "completed"
    assert state.memory_refs["listing_agent"]
    listing_context = state.context_usage["listing_agent"]
    assert "confirmed_merchant_memory" in listing_context["included_sections"]
