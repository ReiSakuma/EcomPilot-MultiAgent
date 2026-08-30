from __future__ import annotations

from typing import Any

from app.model.contracts import ListingModelOutput, ReviewModelOutput, StrategyModelOutput


LISTING_JSON_SCHEMA: dict[str, Any] = ListingModelOutput.model_json_schema()


STRATEGY_JSON_SCHEMA: dict[str, Any] = StrategyModelOutput.model_json_schema()


REVIEW_JSON_SCHEMA: dict[str, Any] = ReviewModelOutput.model_json_schema()
