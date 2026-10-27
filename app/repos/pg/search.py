"""Search from PostgreSQL: parameterised filters on v_transaction_detail, LIMIT/OFFSET paging."""

from __future__ import annotations

import math

from psycopg import Connection, sql

from app.repos.dto import SaleRow, SearchResult
from app.services.search import PAGE_SIZE, SORTS, SearchQuery
from etl.clean import FLAT_MODEL_IDS, FLAT_TYPE_IDS, TOWN_IDS

_COLUMNS = sql.SQL(
    "txn_id, txn_month, town, block, street, building_id, flat_type, flat_model, storey_min, storey_max,"
    " floor_area_sqm, lease_commence_year, remaining_lease_months, resale_price"
)


def like_escape(text: str) -> str:
    """Escape LIKE metacharacters (backslash is PostgreSQL's default escape character)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _where(q: SearchQuery) -> tuple[sql.Composable, list]:
    """WHERE clause with %s placeholders for the filters that are set. Columns are fixed names, values are bound."""
    conds: list[sql.Composable] = []
    params: list = []

    def add(condition: str, value) -> None:
        conds.append(sql.SQL(condition))
        params.append(value)

    if q.town is not None:
        add("town_id = %s", TOWN_IDS[q.town])
    if q.flat_type is not None:
        add("flat_type_id = %s", FLAT_TYPE_IDS[q.flat_type])
    if q.flat_model is not None:
        add("flat_model_id = %s", FLAT_MODEL_IDS[q.flat_model])
    if q.month_from is not None:
        add("txn_month >= %s", q.month_from)
    if q.month_to is not None:
        add("txn_month <= %s", q.month_to)
    if q.price_min is not None:
        add("resale_price >= %s", q.price_min)
    if q.price_max is not None:
        add("resale_price <= %s", q.price_max)
    if q.area_min is not None:
        add("floor_area_sqm >= %s", q.area_min)
    if q.area_max is not None:
        add("floor_area_sqm <= %s", q.area_max)
    if q.street is not None:
        add("street LIKE %s", f"%{like_escape(q.street)}%")
    if q.block is not None:
        add("block = %s", q.block)
    if not conds:
        return sql.SQL(""), params
    return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conds), params


class PgSearchRepo:
    """Built around one idle autocommit connection (docs/conventions.md, section 3)."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def search(self, query: SearchQuery) -> SearchResult:
        where, params = _where(query)
        direction = sql.SQL("DESC" if query.order == "desc" else "ASC")
        page_sql = (
            sql.SQL("SELECT ")
            + _COLUMNS
            + sql.SQL(" FROM v_transaction_detail")
            + where
            + sql.SQL(" ORDER BY {col} {d}, txn_id {d} LIMIT %s OFFSET %s").format(
                col=sql.Identifier(SORTS[query.sort]), d=direction
            )
        )
        count_sql = sql.SQL("SELECT count(*) FROM v_transaction_detail") + where
        # Count and page from one snapshot.
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            total = self.conn.execute(count_sql, params).fetchone()[0]
            rows = self.conn.execute(page_sql, [*params, PAGE_SIZE, query.offset]).fetchall()
        return SearchResult(
            rows=[SaleRow(*r) for r in rows],
            total=total,
            page=query.page,
            pages=max(1, math.ceil(total / PAGE_SIZE)),
        )
