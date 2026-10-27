"""Search from MongoDB: $match-style filter, sort, skip and limit on resale_transactions."""

from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime

from bson.decimal128 import Decimal128
from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database

from app.repos.dto import SaleRow, SearchResult
from app.services.search import PAGE_SIZE, SORTS, SearchQuery


def _midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def _range(filter_: dict, field: str, low, high, convert) -> None:
    bounds = {}
    if low is not None:
        bounds["$gte"] = convert(low)
    if high is not None:
        bounds["$lte"] = convert(high)
    if bounds:
        filter_[field] = bounds


def build_filter(q: SearchQuery) -> dict:
    """Filter document built only from the validated, typed query values."""
    f: dict = {}
    if q.town is not None:
        f["town"] = q.town
    if q.flat_type is not None:
        f["flat_type"] = q.flat_type
    if q.flat_model is not None:
        f["flat_model"] = q.flat_model
    _range(f, "txn_month", q.month_from, q.month_to, _midnight)
    _range(f, "resale_price", q.price_min, q.price_max, Decimal128)
    _range(f, "floor_area_sqm", q.area_min, q.area_max, Decimal128)
    if q.street is not None:
        f["street"] = {"$regex": re.escape(q.street)}
    if q.block is not None:
        f["block"] = q.block
    return f


class MongoSearchRepo:
    """Built around one Database (docs/conventions.md, section 3)."""

    def __init__(self, db: Database):
        self.db = db

    def search(self, query: SearchQuery) -> SearchResult:
        filter_ = build_filter(query)
        d = ASCENDING if query.order == "asc" else DESCENDING
        coll = self.db.resale_transactions
        total = coll.count_documents(filter_)
        cursor = coll.find(filter_).sort([(SORTS[query.sort], d), ("_id", d)]).skip(query.offset).limit(PAGE_SIZE)
        rows = [
            SaleRow(
                txn_id=doc["_id"],
                txn_month=doc["txn_month"].date(),
                town=doc["town"],
                block=doc["block"],
                street=doc["street"],
                building_id=doc["building_id"],
                flat_type=doc["flat_type"],
                flat_model=doc["flat_model"],
                storey_min=doc["storey_min"],
                storey_max=doc["storey_max"],
                floor_area_sqm=doc["floor_area_sqm"].to_decimal(),
                lease_commence_year=doc.get("lease_commence_year"),
                remaining_lease_months=doc.get("remaining_lease_months"),
                resale_price=doc["resale_price"].to_decimal(),
            )
            for doc in cursor
        ]
        return SearchResult(rows=rows, total=total, page=query.page, pages=max(1, math.ceil(total / PAGE_SIZE)))
