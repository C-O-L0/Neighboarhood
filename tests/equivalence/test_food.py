"""Food access: PostgreSQL Haversine and MongoDB $geoNear agree on ids, order, counts and distances (task 3.4)."""

import math
from decimal import Decimal

import pytest

from app.repos.dto import DISTANCE_TOL, BlockFoodAccess, HawkerDistance
from app.repos.mongo import food as mongo_food
from app.repos.mongo.food import MongoFoodRepo
from app.repos.pg import food as pg_food
from app.repos.pg.food import PgFoodRepo
from app.services.food import EXCLUDED_STATUS, NEAREST_COUNT
from tests.equivalence.cases import food_block_params, food_town_params, is_sample
from tests.equivalence.compare import assert_equivalent

pytestmark = pytest.mark.integration

BUKIT_TIMAH_MARKET = 119463  # Under Construction


def both_block(pg_conn, mongo_db, building_id):
    return PgFoodRepo(pg_conn).block(building_id), MongoFoodRepo(mongo_db).block(building_id)


def both_town(pg_conn, mongo_db, town):
    return PgFoodRepo(pg_conn).town(town), MongoFoodRepo(mongo_db).town(town)


def block_id(pg_conn, block, street):
    row = pg_conn.execute("SELECT building_id FROM building WHERE block = %s AND street = %s", (block, street))
    return row.fetchone()[0]


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Independent check: Haversine with MongoDB's radius, in plain floats."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6378100 * math.asin(math.sqrt(a))


def open_centres(pg_conn):
    return pg_conn.execute(
        "SELECT hawker_id, latitude::float8, longitude::float8 FROM hawker_centre WHERE status <> %s",
        (EXCLUDED_STATUS,),
    ).fetchall()


def test_every_block_and_every_town_is_equivalent(pg_conn, mongo_db):
    blocks = food_block_params(pg_conn)
    towns = food_town_params(pg_conn)
    if is_sample(pg_conn):
        count = pg_conn.execute("SELECT count(*) FROM building WHERE latitude IS NOT NULL").fetchone()[0]
        assert len(blocks) == count and len(towns) == 5
    else:
        assert len(towns) == 3
    for p in blocks:
        pg, mongo = both_block(pg_conn, mongo_db, p["building_id"])
        assert pg is not None and len(pg.nearest) == NEAREST_COUNT
        assert_equivalent(pg, mongo, path=f"food_block{p}")
    for p in towns:
        pg, mongo = both_town(pg_conn, mongo_db, p["town"])
        assert pg
        assert_equivalent(pg, mongo, path=f"food_town{p}")


def test_stub_is_geocoded_and_joins_food_access(pg_conn, mongo_db):
    stub = pg_conn.execute("SELECT building_id FROM building WHERE NOT in_property_dataset").fetchone()[0]
    if is_sample(pg_conn):
        assert stub == block_id(pg_conn, "82", "MACPHERSON LANE") == 251
    assert stub in [p["building_id"] for p in food_block_params(pg_conn)]
    pg, mongo = both_block(pg_conn, mongo_db, stub)
    assert pg.nearest and pg.block == "82" and pg.street == "MACPHERSON LANE"
    assert_equivalent(pg, mongo)


def test_distances_are_tolerant_within_one_metre_and_checked_independently(pg_conn, mongo_db):
    tol = {f.name: f.metadata.get("tol") for f in HawkerDistance.__dataclass_fields__.values()}
    assert tol["distance_m"] == DISTANCE_TOL == Decimal("1")
    assert all(v is None for k, v in tol.items() if k != "distance_m")
    assert BlockFoodAccess.__dataclass_fields__["within_1km"].metadata.get("tol") is None
    centres = {h: (lat, lon) for h, lat, lon in open_centres(pg_conn)}
    worst = Decimal(0)
    for p in food_block_params(pg_conn):
        pg, mongo = both_block(pg_conn, mongo_db, p["building_id"])
        lat, lon = pg_conn.execute(
            "SELECT latitude::float8, longitude::float8 FROM building WHERE building_id = %s", (p["building_id"],)
        ).fetchone()
        expected = sorted((haversine_m(lat, lon, *c), h) for h, c in centres.items())
        assert [h.hawker_id for h in pg.nearest] == [h for _, h in expected[:NEAREST_COUNT]]
        assert pg.within_1km == sum(d <= 1000 for d, _ in expected)
        for a, b in zip(pg.nearest, mongo.nearest, strict=True):
            worst = max(worst, abs(a.distance_m - b.distance_m))
            assert abs(float(a.distance_m) - haversine_m(lat, lon, *centres[a.hawker_id])) < 0.06
    assert worst <= 1  # the report quotes the unrounded maximum, measured separately


def test_a_block_with_no_open_centre_within_1km_counts_zero_in_both(pg_conn, mongo_db):
    zero = [
        p["building_id"]
        for p in food_block_params(pg_conn)
        if PgFoodRepo(pg_conn).block(p["building_id"]).within_1km == 0
    ]
    assert zero, "the data has no block without a centre within 1 km"
    for i in zero[:10]:
        pg, mongo = both_block(pg_conn, mongo_db, i)
        assert pg.within_1km == mongo.within_1km == 0 and pg.nearest[0].distance_m > 1000


def test_block_214_counts_the_centre_at_999_88_m(pg_conn, mongo_db):
    if not is_sample(pg_conn):
        pytest.skip("building ids are specific to the sample")
    pg, mongo = both_block(pg_conn, mongo_db, 214)
    assert (pg.block, pg.street) == ("265", "BISHAN ST 24")
    assert pg.within_1km == mongo.within_1km == 3
    edge = next(h for h in pg.nearest if h.hawker_id == 119393)
    assert Decimal("999.8") <= edge.distance_m <= Decimal("999.9")


def test_block_207_orders_the_4th_and_5th_centres_alike(pg_conn, mongo_db):
    if not is_sample(pg_conn):
        pytest.skip("building ids are specific to the sample")
    pg, mongo = both_block(pg_conn, mongo_db, 207)
    assert [h.hawker_id for h in pg.nearest] == [h.hawker_id for h in mongo.nearest]
    fourth, fifth = pg.nearest[3], pg.nearest[4]
    assert fifth.distance_m - fourth.distance_m <= Decimal("0.2")
    assert (fourth.hawker_id, fifth.hawker_id) == (119393, 119377)


def test_no_centre_under_construction_appears_in_either_database(pg_conn, mongo_db):
    for p in food_block_params(pg_conn):
        pg, mongo = both_block(pg_conn, mongo_db, p["building_id"])
        assert all(h.status != EXCLUDED_STATUS for h in pg.nearest + mongo.nearest)
    for p in food_town_params(pg_conn):
        pg, mongo = both_town(pg_conn, mongo_db, p["town"])
        assert all(h.status != EXCLUDED_STATUS for r in pg + mongo for h in r.nearest)


def test_bukit_timah_block_nearer_to_the_closed_market_than_the_fifth_open_centre(pg_conn, mongo_db):
    market = pg_conn.execute(
        "SELECT status, latitude::float8, longitude::float8 FROM hawker_centre WHERE hawker_id = %s",
        (BUKIT_TIMAH_MARKET,),
    ).fetchone()
    assert market[0] == EXCLUDED_STATUS
    rows = pg_conn.execute(
        "SELECT building_id, latitude::float8, longitude::float8 FROM building b JOIN town t USING (town_id)"
        " WHERE t.name = 'BUKIT TIMAH' AND latitude IS NOT NULL ORDER BY building_id"
    ).fetchall()
    chosen = None
    for building_id, lat, lon in rows:
        fifth = PgFoodRepo(pg_conn).block(building_id).nearest[-1]
        if haversine_m(lat, lon, market[1], market[2]) < float(fifth.distance_m):
            chosen = building_id
            break
    assert chosen is not None, "no BUKIT TIMAH block has Bukit Timah Market nearer than its fifth open centre"
    pg, mongo = both_block(pg_conn, mongo_db, chosen)
    assert BUKIT_TIMAH_MARKET not in [h.hawker_id for h in pg.nearest + mongo.nearest]
    assert_equivalent(pg, mongo)


def test_ungeocoded_block_is_none_in_both_without_error(pg_conn, mongo_db):
    ids = [r[0] for r in pg_conn.execute("SELECT building_id FROM building WHERE latitude IS NULL").fetchall()]
    assert ids
    for i in ids[:20]:
        assert both_block(pg_conn, mongo_db, i) == (None, None)
    assert both_block(pg_conn, mongo_db, 2_000_000_000) == (None, None)


def test_ordering_key_is_unrounded_distance_then_id():
    # The data has no exact ties, so the key is checked in the statements and the pipelines.
    for statement, keys in (
        (pg_food._BLOCK.as_string(), ["ORDER BY dist, h.hawker_id", "ORDER BY n.dist, n.hawker_id"]),
        (pg_food._TOWN.as_string(), ["ORDER BY dist, h.hawker_id", "ORDER BY n.dist, b.building_id"]),
    ):
        for key in keys:
            assert key in statement
        assert "round(" not in statement.lower()
        assert "<= 1000" in statement
    assert "LIMIT 5" in pg_food._BLOCK.as_string() and "LIMIT 1" in pg_food._TOWN.as_string()
    block = mongo_food.block_pipeline({"type": "Point", "coordinates": [103.8, 1.3]})
    facet = block[1]["$facet"]
    assert facet["nearest"][0] == {"$sort": {"d": 1, "_id": 1}}
    assert facet["within"][0] == {"$match": {"d": {"$lte": 1000}}}
    assert "maxDistance" not in block[0]["$geoNear"]
    assert block[0]["$geoNear"]["query"] == {"properties.STATUS": {"$ne": EXCLUDED_STATUS}}
    town = mongo_food.town_pipeline("BISHAN")
    nested = town[1]["$lookup"]["pipeline"]
    assert nested[0]["$geoNear"]["near"] == "$$loc" and nested[1] == {"$sort": {"d": 1, "_id": 1}}
    assert town[2]["$lookup"]["pipeline"][0]["$geoNear"]["maxDistance"] == 1000
    assert town[-1] == {"$sort": {"dist": 1, "_id": 1}}


def test_town_view_is_ordered_by_distance_then_building_id(pg_conn, mongo_db):
    for p in food_town_params(pg_conn):
        for rows in both_town(pg_conn, mongo_db, p["town"]):
            # the sort key is the unrounded distance, so the rounded values are only non-decreasing
            distances = [r.nearest[0].distance_m for r in rows]
            assert distances == sorted(distances)
            assert all(len(r.nearest) == 1 for r in rows)
