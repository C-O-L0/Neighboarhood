"""Building page from PostgreSQL: live joins, no materialised view."""

from __future__ import annotations

from psycopg import Connection

from app.repos.dto import BuildingDetail, BuildingStats, FlatTypeStats, RecentSale, SupplyRow
from etl.clean import LAND_USE

_BUILDING = """
SELECT b.building_id, b.block, b.street, tw.name, tw.code, b.in_property_dataset,
       b.year_completed, b.lease_commence_year, b.max_floor,
       b.residential, b.commercial, b.market_hawker, b.miscellaneous, b.multistorey_carpark, b.precinct_pavilion,
       b.latitude, b.longitude, u.total_units
FROM building b
JOIN town tw ON tw.town_id = b.town_id
JOIN v_building_units u ON u.building_id = b.building_id
WHERE b.building_id = %s
"""

_SUPPLY = """
SELECT ft.name, s.sold_units, s.rental_units
FROM building_flat_supply s
JOIN flat_type ft ON ft.flat_type_id = s.flat_type_id
WHERE s.building_id = %s
ORDER BY ft.name COLLATE "C"
"""

_STATS = """
SELECT count(*), percentile_disc(0.5) WITHIN GROUP (ORDER BY resale_price),
       round(avg(resale_price / floor_area_sqm), 2), max(txn_month)
FROM resale_transaction
WHERE building_id = %s
"""

_BY_TYPE = """
SELECT ft.name, count(*), percentile_disc(0.5) WITHIN GROUP (ORDER BY t.resale_price),
       round(avg(t.resale_price / t.floor_area_sqm), 2)
FROM resale_transaction t
JOIN flat_type ft ON ft.flat_type_id = t.flat_type_id
WHERE t.building_id = %s
GROUP BY ft.name
ORDER BY ft.name COLLATE "C"
"""

_RECENT = """
SELECT txn_id, txn_month, flat_type, flat_model, storey_min, storey_max, floor_area_sqm, resale_price
FROM v_transaction_detail
WHERE building_id = %s
ORDER BY txn_month DESC, txn_id DESC
LIMIT 10
"""


class PgBuildingRepo:
    """Built around one idle autocommit connection (docs/conventions.md, section 3)."""

    def __init__(self, conn: Connection):
        self.conn = conn

    def get(self, building_id: int) -> BuildingDetail | None:
        # One snapshot for the whole page, as a single Mongo document read gives.
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            b = self.conn.execute(_BUILDING, (building_id,)).fetchone()
            if b is None:
                return None
            in_property = b[5]
            supply = None
            if in_property:
                supply = [SupplyRow(*r) for r in self.conn.execute(_SUPPLY, (building_id,)).fetchall()]
            count, median, avg_psm, last_month = self.conn.execute(_STATS, (building_id,)).fetchone()
            by_type = [FlatTypeStats(*r) for r in self.conn.execute(_BY_TYPE, (building_id,)).fetchall()]
            recent = [RecentSale(*r) for r in self.conn.execute(_RECENT, (building_id,)).fetchall()]
        return BuildingDetail(
            building_id=b[0],
            block=b[1],
            street=b[2],
            town=b[3],
            town_code=b[4],
            in_property_dataset=in_property,
            year_completed=b[6],
            lease_commence_year=b[7],
            max_floor=b[8],
            land_use=dict(zip(LAND_USE, b[9:15], strict=True)) if in_property else None,
            latitude=b[15],
            longitude=b[16],
            supply=supply,
            total_units=b[17],
            stats=BuildingStats(
                txn_count=count, median_price=median, avg_psm=avg_psm, last_txn_month=last_month, by_flat_type=by_type
            ),
            recent_sales=recent,
        )

    def find_id(self, block: str, street: str) -> int | None:
        row = self.conn.execute(
            "SELECT building_id FROM building WHERE block = %s AND street = %s", (block, street)
        ).fetchone()
        return row[0] if row else None
