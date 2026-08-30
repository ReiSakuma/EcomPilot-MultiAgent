from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import PROJECT_ROOT, PROJECT_VERSION
from app.tools.market_data import load_products
from app.tools.market_statistics import clean_market_price_samples


EXPECTED = {
    "无线耳机": {
        "dirty": {"earbud_099", "earbud_100"},
        "explainable": {f"earbud_{index:03d}" for index in range(95, 99)},
    },
    "机械键盘": {
        "dirty": {"keyboard_099", "keyboard_100"},
        "explainable": {f"keyboard_{index:03d}" for index in range(95, 99)},
    },
}


def main() -> int:
    categories: dict[str, dict] = {}
    checks: dict[str, bool] = {}
    for category, expected in EXPECTED.items():
        products = load_products(category)
        result = clean_market_price_samples(products)
        reversed_result = clean_market_price_samples(list(reversed(products)))
        dirty = {item.sample_id for item in result.decisions if item.excluded}
        explainable = {
            item.sample_id
            for item in result.decisions
            if item.status == "explainable_extreme"
        }
        prefix = "earbuds" if category == "无线耳机" else "keyboards"
        checks[f"{prefix}_has_100_raw_samples"] = result.input_count == 100
        checks[f"{prefix}_excludes_only_known_dirty_rows"] = dirty == expected["dirty"]
        checks[f"{prefix}_retains_explainable_extremes"] = (
            explainable == expected["explainable"]
        )
        checks[f"{prefix}_is_order_deterministic"] = result == reversed_result
        categories[category] = {
            "input_count": result.input_count,
            "retained_count": result.retained_count,
            "excluded_count": result.excluded_count,
            "mode": result.mode,
            "warnings": list(result.warnings),
            "raw_distribution": result.raw_distribution.model_dump(mode="json"),
            "cleaned_distribution": result.cleaned_distribution.model_dump(mode="json"),
            "excluded_ids": sorted(dirty),
            "explainable_extreme_ids": sorted(explainable),
            "content_hash": result.content_hash,
        }

    report = {
        "version": "v52",
        "project_version": PROJECT_VERSION,
        "passed": all(checks.values()),
        "checks": checks,
        "categories": categories,
        "boundary": (
            "v52 performs deterministic whole-category cleaning only. Comparable "
            "market tiers and target-price gating begin in v53 and v54."
        ),
    }
    report_path = PROJECT_ROOT / "reports" / "v52" / "v52_acceptance.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
