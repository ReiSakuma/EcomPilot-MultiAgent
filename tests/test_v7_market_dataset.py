from app.eval.metrics import market_sample_score
from app.orchestration.workflow import run_workflow
from app.tools.product_tools import build_market_report, get_reviews, search_products


def test_v7_market_report_uses_local_dataset():
    report = build_market_report("无线耳机", "大学生")

    assert report["sample_size"]["competitors"] >= 10
    assert report["sample_size"]["reviews"] >= 20
    assert report["price_band"] == [129.0, 239.0]
    assert report["full_market_band"] == [49.0, 899.0]
    assert report["median_price"] == 189.0
    assert "低延迟" in report["top_features"]
    assert "连接稳定性" in report["pain_points"]
    assert len(report["evidence_refs"]) >= 10


def test_v7_tools_load_products_and_reviews():
    products = search_products("无线耳机", "大学生")
    reviews = get_reviews("无线耳机", [product["id"] for product in products])

    assert len(products) == 96
    assert len(reviews) == 192
    assert all(product["source_type"] == "synthetic_seed" for product in products)


def test_market_dataset_includes_one_hundred_mechanical_keyboards():
    report = build_market_report("键盘")

    assert report["sample_size"] == {
        "competitors": 94,
        "reviews": 188,
        "raw_competitors": 100,
        "raw_reviews": 200,
        "valid_competitors": 98,
        "valid_reviews": 196,
        "adjacent_competitors": 4,
        "adjacent_reviews": 8,
        "excluded_competitors": 2,
    }
    assert report["raw_price_band"] == [8.8, 12999.0]
    assert report["price_band"] == [159.0, 499.0]
    assert report["full_market_band"] == [39.0, 1299.0]
    assert report["median_price"] == 329.0
    assert "热插拔" in report["top_features"]
    assert "键盘噪音" in report["pain_points"]
    assert "机械键盘" in report["keywords"]
    assert len(report["evidence_refs"]) == 100


def test_market_reviews_reference_existing_products_in_both_categories():
    for category in ("蓝牙耳机", "机械键盘"):
        products = search_products(category)
        product_ids = {product["id"] for product in products}
        reviews = get_reviews(category)

        assert len(products) == 100
        assert len(reviews) == 200
        assert all(review["product_id"] in product_ids for review in reviews)
        assert all(review["source_type"] == "synthetic_seed" for review in reviews)


def test_v7_workflow_market_output_has_evidence_and_sample_score():
    state = run_workflow(
        "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，库存 800 件，毛利率不能低于 25%。",
        approved=True,
    )

    market = state.agent_outputs["market_agent"]
    assert state.status == "completed"
    assert market["sample_size"]["competitors"] == 94
    assert market["sample_size"]["reviews"] == 188
    assert market_sample_score(state) == 1.0
    assert any(record["tool_name"] == "build_market_report" for record in state.tool_records)
