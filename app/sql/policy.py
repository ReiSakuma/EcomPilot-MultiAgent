from __future__ import annotations

import hashlib
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp, parse
from sqlglot.errors import ParseError


DEFAULT_ALLOWED_SCHEMA: dict[str, frozenset[str]] = {
    "products": frozenset({"id", "name", "category", "price", "monthly_sales"}),
    "reviews": frozenset({"review_id", "product_id", "rating", "text"}),
    "product_features": frozenset({"product_id", "feature"}),
    "product_audiences": frozenset({"product_id", "audience"}),
}
ALLOWED_FUNCTIONS = frozenset(
    {
        "ABS",
        "AVG",
        "COALESCE",
        "COUNT",
        "LENGTH",
        "LOWER",
        "MAX",
        "MIN",
        "NULLIF",
        "ROUND",
        "SUM",
        "UPPER",
    }
)
# SQLGlot represents CASE expressions and their internal branches as Func
# subclasses. They are control-flow syntax, not callable database functions.
SAFE_FUNCTION_LIKE_EXPRESSIONS = (exp.Case, exp.If)
FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.Command,
    exp.Pragma,
    exp.Transaction,
    exp.Attach,
    exp.Detach,
    exp.With,
    exp.Subquery,
    exp.Union,
    exp.Intersect,
    exp.Except,
)


class SqlPolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(default_factory=lambda: f"sqlpolicy_{uuid4().hex[:12]}")
    status: Literal["allowed", "denied"]
    query_hash: str
    tenant_id: str = "tenant_demo"
    normalized_sql: str | None = None
    tables: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    functions: tuple[str, ...] = ()
    enforced_limit: int
    limit_applied: bool = False
    row_filter_applied: bool = False
    reason_codes: tuple[str, ...] = ()


class SqlPolicyDeniedError(PermissionError):
    safe_to_retry = False

    def __init__(self, decision: SqlPolicyDecision) -> None:
        self.decision = decision
        reasons = ", ".join(decision.reason_codes) or "sql_policy_denied"
        super().__init__(f"SQL policy denied query: {reasons}")


class SqlPolicyGateway:
    """Validates model-authored SQLite with an allowlist AST policy."""

    def __init__(
        self,
        *,
        allowed_schema: dict[str, frozenset[str]] | None = None,
        max_rows: int = 50,
    ) -> None:
        self.allowed_schema = allowed_schema or DEFAULT_ALLOWED_SCHEMA
        self.max_rows = max(1, min(max_rows, 100))
        self.decisions: list[SqlPolicyDecision] = []

    def authorize(self, sql: str, *, tenant_id: str = "tenant_demo") -> SqlPolicyDecision:
        query_hash = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        try:
            statements = [statement for statement in parse(sql, read="sqlite") if statement]
        except ParseError:
            self._deny(query_hash, ("parse_error",))
        if len(statements) != 1:
            self._deny(query_hash, ("exactly_one_statement_required",))
        tree = statements[0]
        if not isinstance(tree, exp.Select):
            self._deny(query_hash, ("select_only",))
        if any(tree.find(node_type) is not None for node_type in FORBIDDEN_NODES):
            self._deny(query_hash, ("forbidden_ast_node",))

        table_nodes = list(tree.find_all(exp.Table))
        tables = tuple(dict.fromkeys(node.name for node in table_nodes))
        if not tables:
            self._deny(query_hash, ("table_required",))
        unknown_tables = sorted(set(tables) - set(self.allowed_schema))
        if unknown_tables:
            self._deny(query_hash, ("table_not_allowed",))

        aliases = {node.alias_or_name: node.name for node in table_nodes}
        select_aliases = {
            expression.alias
            for expression in tree.expressions
            if expression.alias
        }
        observed_columns: list[str] = []
        for column in tree.find_all(exp.Column):
            column_name = column.name
            qualifier = column.table
            if not qualifier and column_name in select_aliases:
                continue
            if qualifier:
                table_name = aliases.get(qualifier)
                if table_name is None or column_name not in self.allowed_schema[table_name]:
                    self._deny(query_hash, ("column_not_allowed",))
                observed_columns.append(f"{table_name}.{column_name}")
                continue
            matching_tables = [
                table_name
                for table_name in tables
                if column_name in self.allowed_schema[table_name]
            ]
            if len(matching_tables) != 1:
                self._deny(
                    query_hash,
                    ("ambiguous_column" if matching_tables else "column_not_allowed",),
                )
            observed_columns.append(f"{matching_tables[0]}.{column_name}")

        for star in tree.find_all(exp.Star):
            if not isinstance(star.parent, exp.Count):
                self._deny(query_hash, ("wildcard_not_allowed",))

        functions: list[str] = []
        for function in tree.find_all(exp.Func):
            # SQLGlot models boolean AND/OR connectors as Func subclasses, even
            # though they are operators rather than callable SQL functions. CASE
            # and its internal IF node are likewise expressions, so they are
            # validated through their child columns rather than the function list.
            if isinstance(function, (exp.Connector, *SAFE_FUNCTION_LIKE_EXPRESSIONS)):
                continue
            function_name = (
                function.name.upper()
                if isinstance(function, exp.Anonymous)
                else function.sql_name().upper()
            )
            if function_name not in ALLOWED_FUNCTIONS:
                self._deny(query_hash, ("function_not_allowed",))
            functions.append(function_name)

        tenant_predicate: exp.Expression | None = None
        for table in table_nodes:
            scoped_column = exp.column("tenant_id", table=table.alias_or_name)
            condition = exp.EQ(
                this=scoped_column,
                expression=exp.Literal.string(tenant_id),
            )
            tenant_predicate = (
                condition
                if tenant_predicate is None
                else exp.and_(tenant_predicate, condition)
            )
        assert tenant_predicate is not None
        existing_where = tree.args.get("where")
        if existing_where is None:
            tree.set("where", exp.Where(this=tenant_predicate))
        else:
            tree.set(
                "where",
                exp.Where(this=exp.and_(existing_where.this, tenant_predicate)),
            )

        limit = tree.args.get("limit")
        requested_limit: int | None = None
        if limit is not None:
            expression = limit.expression
            if not isinstance(expression, exp.Literal) or not expression.is_int:
                self._deny(query_hash, ("literal_limit_required",))
            requested_limit = int(expression.this)
            if requested_limit < 1:
                self._deny(query_hash, ("positive_limit_required",))
        enforced_limit = min(requested_limit or self.max_rows, self.max_rows)
        limit_applied = requested_limit != enforced_limit
        tree.limit(enforced_limit, copy=False)
        decision = SqlPolicyDecision(
            status="allowed",
            query_hash=query_hash,
            tenant_id=tenant_id,
            normalized_sql=tree.sql(dialect="sqlite"),
            tables=tuple(sorted(set(tables))),
            columns=tuple(sorted(set(observed_columns))),
            functions=tuple(sorted(set(functions))),
            enforced_limit=enforced_limit,
            limit_applied=limit_applied,
            row_filter_applied=True,
            reason_codes=(
                "select_only",
                "schema_allowlist_passed",
                "tenant_row_filter_enforced",
                "row_limit_enforced",
            ),
        )
        self.decisions.append(decision)
        return decision

    def schema_catalog(self) -> dict[str, list[str]]:
        return {
            table: sorted(columns) for table, columns in sorted(self.allowed_schema.items())
        }

    def _deny(self, query_hash: str, reasons: tuple[str, ...]) -> None:
        decision = SqlPolicyDecision(
            status="denied",
            query_hash=query_hash,
            enforced_limit=self.max_rows,
            reason_codes=reasons,
        )
        self.decisions.append(decision)
        raise SqlPolicyDeniedError(decision)
