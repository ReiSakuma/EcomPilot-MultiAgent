from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any

from app.config import DATA_DIR
from app.sql.policy import SqlPolicyDecision
from app.sandbox.runner import SandboxExecutionError, SqlSandboxRunner


SQL_DATASET_VERSION = "tenant-market-v4"

DEFAULT_PRODUCT_PATHS = (
    DATA_DIR / "products" / "wireless_earbuds_competitors.json",
    DATA_DIR / "products" / "mechanical_keyboards_competitors.json",
)
DEFAULT_REVIEW_PATHS = (
    DATA_DIR / "reviews" / "wireless_earbuds_reviews.json",
    DATA_DIR / "reviews" / "mechanical_keyboards_reviews.json",
)


class SqlExecutionError(RuntimeError):
    def __init__(self, message: str, receipt: Any = None) -> None:
        super().__init__(message)
        self.receipt = receipt


class MarketDatabase:
    """Builds a frozen SQLite dataset and executes only pre-authorized SELECTs."""

    _bootstrap_lock = RLock()

    def __init__(
        self,
        database_path: Path,
        *,
        products_path: Path | None = None,
        reviews_path: Path | None = None,
        timeout_seconds: float = 0.2,
        sandbox_runner: SqlSandboxRunner | None = None,
    ) -> None:
        self.database_path = database_path
        self.products_paths = (products_path,) if products_path else DEFAULT_PRODUCT_PATHS
        self.reviews_paths = (reviews_path,) if reviews_path else DEFAULT_REVIEW_PATHS
        self.timeout_seconds = max(0.05, min(timeout_seconds, 2.0))
        self.sandbox_runner = sandbox_runner or SqlSandboxRunner(
            query_timeout_seconds=self.timeout_seconds
        )

    def ensure_initialized(self) -> None:
        if self._is_current_dataset():
            return
        with self._bootstrap_lock:
            if self._is_current_dataset():
                return
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.database_path.with_suffix(".tmp")
            if temporary.exists():
                temporary.unlink()
            connection = sqlite3.connect(temporary)
            try:
                self._create_schema(connection)
                self._load_seed_data(connection)
                connection.commit()
            finally:
                connection.close()
            temporary.replace(self.database_path)

    def _is_current_dataset(self) -> bool:
        if not self.database_path.exists():
            return False
        try:
            connection = sqlite3.connect(
                f"file:{self.database_path}?mode=ro",
                uri=True,
                timeout=self.timeout_seconds,
            )
            try:
                row = connection.execute(
                    "SELECT dataset_version FROM dataset_metadata WHERE id = 1"
                ).fetchone()
            finally:
                connection.close()
        except sqlite3.Error:
            return False
        return bool(row and row[0] == SQL_DATASET_VERSION)

    def execute(self, decision: SqlPolicyDecision) -> dict[str, Any]:
        if decision.status != "allowed" or not decision.normalized_sql:
            raise SqlExecutionError("SQL execution requires an allowed policy decision")
        self.ensure_initialized()
        try:
            result = self.sandbox_runner.execute(
                decision,
                database_path=self.database_path,
                dataset_version=SQL_DATASET_VERSION,
            )
        except SandboxExecutionError as exc:
            raise SqlExecutionError(
                f"SQL sandbox execution failed: {exc}",
                receipt=exc.receipt.model_dump(mode="json") if exc.receipt else None,
            ) from exc
        return result.model_dump(mode="json")

    def sandbox_status(self) -> dict[str, Any]:
        return self.sandbox_runner.status()

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE dataset_metadata (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                dataset_version TEXT NOT NULL
            );
            CREATE TABLE products (
                tenant_id TEXT NOT NULL,
                id TEXT NOT NULL,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                price REAL NOT NULL,
                monthly_sales INTEGER NOT NULL,
                PRIMARY KEY (tenant_id, id)
            );
            CREATE TABLE reviews (
                tenant_id TEXT NOT NULL,
                review_id INTEGER NOT NULL,
                product_id TEXT NOT NULL,
                rating INTEGER NOT NULL,
                text TEXT NOT NULL,
                PRIMARY KEY (tenant_id, review_id)
            );
            CREATE TABLE product_features (
                tenant_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                feature TEXT NOT NULL,
                PRIMARY KEY (tenant_id, product_id, feature)
            );
            CREATE TABLE product_audiences (
                tenant_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                audience TEXT NOT NULL,
                PRIMARY KEY (tenant_id, product_id, audience)
            );
            CREATE INDEX idx_products_tenant_category ON products(tenant_id, category);
            CREATE INDEX idx_reviews_tenant_product ON reviews(tenant_id, product_id);
            CREATE INDEX idx_features_tenant_feature ON product_features(tenant_id, feature);
            CREATE INDEX idx_audiences_tenant_audience ON product_audiences(tenant_id, audience);
            """
        )
        connection.execute(
            "INSERT INTO dataset_metadata(id,dataset_version) VALUES(1,?)",
            (SQL_DATASET_VERSION,),
        )

    def _load_seed_data(self, connection: sqlite3.Connection) -> None:
        products = [
            product
            for path in self.products_paths
            for product in json.loads(path.read_text(encoding="utf-8"))
        ]
        reviews = [
            review
            for path in self.reviews_paths
            for review in json.loads(path.read_text(encoding="utf-8"))
        ]
        for product in products:
            connection.execute(
                "INSERT INTO products(tenant_id,id,name,category,price,monthly_sales) "
                "VALUES(?,?,?,?,?,?)",
                (
                    "tenant_demo",
                    product["id"],
                    product["name"],
                    product["category"],
                    product["price"],
                    product["monthly_sales"],
                ),
            )
            connection.executemany(
                "INSERT INTO product_features(tenant_id,product_id,feature) VALUES(?,?,?)",
                [("tenant_demo", product["id"], feature) for feature in product["features"]],
            )
            connection.executemany(
                "INSERT INTO product_audiences(tenant_id,product_id,audience) VALUES(?,?,?)",
                [
                    ("tenant_demo", product["id"], audience)
                    for audience in product["target_audience"]
                ],
            )
        connection.executemany(
            "INSERT INTO reviews(tenant_id,review_id,product_id,rating,text) VALUES(?,?,?,?,?)",
            [
                ("tenant_demo", index, review["product_id"], review["rating"], review["text"])
                for index, review in enumerate(reviews, start=1)
            ],
        )
        connection.executemany(
            "INSERT INTO products(tenant_id,id,name,category,price,monthly_sales) "
            "VALUES(?,?,?,?,?,?)",
            [
                ("tenant_beta", "beta_earbud_1", "Beta 入耳式耳机", "无线耳机", 79, 320),
                ("tenant_beta", "beta_earbud_2", "Beta 头戴式耳机", "无线耳机", 99, 180),
            ],
        )
        connection.executemany(
            "INSERT INTO reviews(tenant_id,review_id,product_id,rating,text) VALUES(?,?,?,?,?)",
            [
                ("tenant_beta", 1, "beta_earbud_1", 4, "价格实惠，连接稳定"),
                ("tenant_beta", 2, "beta_earbud_2", 3, "佩戴舒适，降噪一般"),
            ],
        )
