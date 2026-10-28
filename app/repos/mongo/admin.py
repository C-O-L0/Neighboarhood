"""Admin sale writes in MongoDB (app_admin client only): record, delete, list, and the bulk stats rebuild.

A record or delete is one `with_transaction`: the sale write and the recomputed `stats` and `recent_sales` of its
block commit together or not at all. The completion-year rule of PostgreSQL trigger (a) cannot be a collection
validator (it needs the building's `year_completed`), so the record transaction enforces it, with the same message.
`rebuild_stats` is the separate `$merge` bulk rebuild; it runs outside a transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from bson.decimal128 import Decimal128
from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import WriteError

from app.repos.dto import BuildingRef, Figures, NewSale, SaleOutcome, SaleResult, SaleRow
from db.mongo.stats import rebuild_all_stats, stats_for_building

DOCUMENT_VALIDATION_FAILURE = 121
PRICE_SCALE = Decimal("0.01")
AREA_SCALE = Decimal("0.1")


class _Stop(Exception):
    """Raised inside the transaction callback to abort it with a typed result (or the demo's rollback)."""

    def __init__(self, result: SaleResult):
        self.result = result


def predates_completion(txn_month, year_completed: int | None) -> str | None:
    """The trigger (a) message when the sale's year is before the block's completion year, else None."""
    if year_completed is not None and txn_month.year < year_completed:
        return f"sale in {txn_month:%Y-%m} predates block completion in {year_completed}"
    return None


class MongoAdminRepo:
    def __init__(self, db: Database):
        self.db = db

    def find_building(self, block: str, street: str) -> BuildingRef | None:
        doc = self.db.building_profiles.find_one({"block": block, "street": street}, {"block": 1, "street": 1})
        return None if doc is None else BuildingRef(doc["_id"], doc["block"], doc["street"])

    def figures(self, building_id: int) -> Figures:
        wanted = {"stats.txn_count": 1, "stats.median_price": 1}
        profile = self.db.building_profiles.find_one({"_id": building_id}, wanted)
        stats = (profile or {}).get("stats", {})
        median = stats.get("median_price")
        return Figures(
            self.db.resale_transactions.count_documents({}),
            stats.get("txn_count", 0),
            None if median is None else median.to_decimal(),
        )

    def _run(self, work) -> SaleResult:
        """Run `work(session)` in a transaction; a _Stop aborts it and its result is returned."""
        try:
            with self.db.client.start_session() as session:
                return session.with_transaction(work)
        except _Stop as stop:
            return stop.result

    def record(self, sale: NewSale, simulate_failure: bool = False) -> SaleResult:
        before = self.figures(sale.building_id) if simulate_failure else None

        def work(session) -> SaleResult:
            # Inside the transaction, so an aborted attempt does not consume an id.
            counter = self.db.counters.find_one_and_update(
                {"_id": "txn"}, {"$inc": {"seq": 1}}, return_document=ReturnDocument.AFTER, session=session
            )
            if counter is None:
                raise LookupError("the txn counter is missing; reload the data")
            txn_id = counter["seq"]
            profile = self.db.building_profiles.find_one(
                {"_id": sale.building_id},
                {"block": 1, "street": 1, "town.name": 1, "year_completed": 1, "lease_commence_year": 1},
                session=session,
            )
            if profile is None:
                raise _Stop(SaleResult(SaleOutcome.NO_SUCH_BUILDING))
            reason = predates_completion(sale.txn_month, profile.get("year_completed"))
            if reason is not None:
                raise _Stop(SaleResult(SaleOutcome.REJECTED, reason=reason))
            self.db.resale_transactions.insert_one(
                {
                    "_id": txn_id,
                    "building_id": sale.building_id,
                    "town": profile["town"]["name"],
                    "block": profile["block"],
                    "street": profile["street"],
                    "flat_type": sale.flat_type,
                    "flat_model": sale.flat_model,
                    "txn_month": datetime(sale.txn_month.year, sale.txn_month.month, 1, tzinfo=UTC),
                    "storey_min": sale.storey_min,
                    "storey_max": sale.storey_max,
                    "floor_area_sqm": Decimal128(sale.floor_area_sqm.quantize(AREA_SCALE)),
                    "remaining_lease_months": sale.remaining_lease_months,
                    "resale_price": Decimal128(sale.resale_price.quantize(PRICE_SCALE)),
                    "lease_commence_year": profile.get("lease_commence_year"),
                    "source": "admin",
                },
                session=session,
            )
            self._update_profile(sale.building_id, session)
            if simulate_failure:
                raise _Stop(SaleResult(SaleOutcome.OK, txn_id, before=before))
            return SaleResult(SaleOutcome.OK, txn_id)

        try:
            result = self._run(work)
        except WriteError as exc:  # the validator (for example the flat_type enum)
            if exc.code == DOCUMENT_VALIDATION_FAILURE:
                return SaleResult(SaleOutcome.REJECTED, reason="the sale failed the collection validator")
            raise
        if result.rolled_back:
            # The transaction has aborted; read the figures again to show they are unchanged.
            return SaleResult(result.outcome, result.txn_id, before=before, after=self.figures(sale.building_id))
        return result

    def delete(self, txn_id: int, simulate_failure: bool = False) -> SaleResult:
        before = None
        if simulate_failure:
            doc = self.db.resale_transactions.find_one({"_id": txn_id}, {"building_id": 1})
            if doc is None:
                return SaleResult(SaleOutcome.NOT_FOUND)
            before = self.figures(doc["building_id"])
        building_id = None

        def work(session) -> SaleResult:
            nonlocal building_id
            doc = self.db.resale_transactions.find_one({"_id": txn_id}, {"building_id": 1}, session=session)
            if doc is None:
                raise _Stop(SaleResult(SaleOutcome.NOT_FOUND))
            building_id = doc["building_id"]
            self.db.resale_transactions.delete_one({"_id": txn_id}, session=session)
            self._update_profile(building_id, session)
            if simulate_failure:
                raise _Stop(SaleResult(SaleOutcome.OK, txn_id, before=before))
            return SaleResult(SaleOutcome.OK, txn_id)

        result = self._run(work)
        if result.rolled_back:
            return SaleResult(result.outcome, result.txn_id, before=before, after=self.figures(building_id))
        return result

    def _update_profile(self, building_id: int, session) -> None:
        self.db.building_profiles.update_one(
            {"_id": building_id}, {"$set": stats_for_building(self.db, building_id, session)}, session=session
        )

    def rebuild_stats(self) -> int:
        """The $merge bulk rebuild of every block's stats; returns the number of blocks with sales."""
        return rebuild_all_stats(self.db)

    def admin_sales(self, limit: int) -> list[SaleRow]:
        cursor = self.db.resale_transactions.find({"source": "admin"}).sort("_id", -1).limit(limit)
        return [
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
