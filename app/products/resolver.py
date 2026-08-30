from __future__ import annotations

import re

from app.conversations.repository import ConversationNotFoundError, ConversationRepository
from app.products.ledger import ProductLedger, ProductNotFoundError
from app.products.models import EntityResolution, ProductCandidate, ProductRecord


class EntityResolver:
    """Resolve product references without letting an LLM invent identity."""

    def __init__(
        self,
        ledger: ProductLedger | None = None,
        repository: ConversationRepository | None = None,
    ) -> None:
        self.ledger = ledger or ProductLedger()
        self.repository = repository or ConversationRepository(self.ledger.database_path)

    def resolve(
        self,
        tenant_id: str,
        query: str,
        *,
        conversation_id: str | None = None,
    ) -> EntityResolution:
        text = " ".join(query.strip().split())

        product_id = _match(r"\bproduct_[A-Za-z0-9_-]+\b", text)
        if product_id:
            return self._direct(tenant_id, product_id.lower(), text, "product_id")

        sku = _match(r"\bSKU-[A-Za-z0-9_-]+\b", text)
        if sku:
            return self._candidates(text, "sku", self.ledger.find_by_alias(tenant_id, sku.upper()))

        task_id = _match(r"\btask_[A-Za-z0-9_-]+\b", text)
        if task_id:
            product = self.ledger.product_for_task(tenant_id, task_id)
            return self._resolved(text, "task_link", product) if product else self._not_found(text)

        if conversation_id and _is_context_reference(text):
            try:
                conversation = self.repository.get_conversation(tenant_id, conversation_id)
            except ConversationNotFoundError:
                conversation = None
            if conversation and conversation.active_product_id:
                try:
                    return self._resolved(
                        text,
                        "conversation_active",
                        self.ledger.get(tenant_id, conversation.active_product_id),
                    )
                except ProductNotFoundError:
                    pass
            recent = self.ledger.products_for_conversation(tenant_id, conversation_id)
            return self._candidates(text, "recent_reference", recent)

        aliases = _meaningful_aliases(text)
        exact_matches: dict[str, ProductRecord] = {}
        for alias in aliases:
            for product in self.ledger.find_by_exact_alias(tenant_id, alias):
                exact_matches[product.product_id] = product
        if exact_matches:
            return self._candidates(text, "exact_title", list(exact_matches.values()))

        matches: dict[str, ProductRecord] = {}
        for alias in aliases:
            for product in self.ledger.find_by_alias(tenant_id, alias):
                matches[product.product_id] = product
        return self._candidates(text, "exact_title", list(matches.values()))

    def _direct(
        self, tenant_id: str, product_id: str, query: str, strategy: str
    ) -> EntityResolution:
        try:
            return self._resolved(query, strategy, self.ledger.get(tenant_id, product_id))
        except ProductNotFoundError:
            return self._not_found(query)

    @staticmethod
    def _resolved(query: str, strategy: str, product: ProductRecord) -> EntityResolution:
        return EntityResolution(
            status="resolved",
            query=query,
            strategy=strategy,
            product_id=product.product_id,
            candidates=[_candidate(product)],
            confidence=1.0,
            explanation="已通过受租户隔离保护的商品身份索引定位。",
        )

    @staticmethod
    def _candidates(
        query: str, strategy: str, products: list[ProductRecord]
    ) -> EntityResolution:
        unique = {product.product_id: product for product in products}
        values = list(unique.values())
        if len(values) == 1:
            return EntityResolver._resolved(query, strategy, values[0])
        if len(values) > 1:
            return EntityResolution(
                status="ambiguous",
                query=query,
                strategy="fuzzy_candidates",
                candidates=[_candidate(product) for product in values[:8]],
                confidence=0.0,
                explanation="命中了多个商品，必须由用户明确选择。",
            )
        return EntityResolver._not_found(query)

    @staticmethod
    def _not_found(query: str) -> EntityResolution:
        return EntityResolution(
            status="not_found",
            query=query,
            strategy="none",
            confidence=0.0,
            explanation="当前商户的可见商品中没有找到匹配项。",
        )


def _candidate(product: ProductRecord) -> ProductCandidate:
    return ProductCandidate(
        product_id=product.product_id,
        sku=product.sku,
        title=product.title,
        category=product.category,
        status=product.status,
        source_task_id=product.source_task_id,
    )


def _match(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.I)
    return match.group(0) if match else None


def _is_context_reference(text: str) -> bool:
    return bool(re.search(r"这个|那个|上次|刚才|它|该商品|已上架", text))


def _meaningful_aliases(text: str) -> list[str]:
    cleaned = re.sub(
        r"查看|查询|看看|显示|告诉我|商品|产品|详情|信息|目前|一下|的|请|帮我",
        " ",
        text,
    )
    return [part for part in re.split(r"[\s，。！？、,:：]+", cleaned) if len(part) >= 2]
