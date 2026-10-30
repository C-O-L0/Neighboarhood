"""Places (task T4): both databases against answers worked out in Python from the building table."""

import math
from decimal import Decimal

import pytest

from app.repos.mongo.places import MongoPlaceRepo
from app.repos.pg.places import PgPlaceRepo
from app.services.food import EARTH_RADIUS_M
from app.services.places import SUGGESTIONS, parse_place

pytestmark = pytest.mark.integration


def repos(pg_conn, mongo_db):
    return PgPlaceRepo(pg_conn), MongoPlaceRepo(mongo_db)


@pytest.fixture(scope="module")
def buildings(pg_conn):
    """(id, block, street, town, lat, lon, residential) for every building."""
    return pg_conn.execute(
        """
        SELECT b.building_id, b.block, b.street, tw.name, b.latitude, b.longitude, b.residential
        FROM building b JOIN town tw USING (town_id)
        """
    ).fetchall()


def haversine(a_lat, a_lon, b_lat, b_lon) -> float:
    la, lb = math.radians(float(a_lat)), math.radians(float(b_lat))
    dlat, dlon = lb - la, math.radians(float(b_lon)) - math.radians(float(a_lon))
    h = math.sin(dlat / 2) ** 2 + math.cos(la) * math.cos(lb) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def origins(buildings) -> list[tuple]:
    located = sorted((b for b in buildings if b[4] is not None), key=lambda b: b[0])
    return [located[0], located[len(located) // 2], located[-1]]


@pytest.mark.parametrize("radius", [1000, 2000, 5000])
def test_near_matches_haversine_in_python(pg_conn, mongo_db, buildings, radius):
    pg, mongo = repos(pg_conn, mongo_db)
    for o in origins(buildings):
        expected = sorted(
            (
                (haversine(o[4], o[5], b[4], b[5]), b[0])
                for b in buildings
                if b[4] is not None and b[0] != o[0] and b[6] is not False
            ),
        )
        expected = [(i, d) for d, i in expected if d <= radius]
        for repo in (pg, mongo):
            got = repo.near(o[0], radius)
            assert [n.building_id for n in got] == [i for i, _ in expected], (repo.__class__.__name__, o[0])
            for n, (_, d) in zip(got, expected, strict=True):
                assert abs(n.distance_m - Decimal(repr(d))) <= 1


def test_near_is_none_for_an_unlocated_or_unknown_block(pg_conn, mongo_db, buildings):
    unlocated = next(b[0] for b in buildings if b[4] is None)
    for repo in repos(pg_conn, mongo_db):
        assert repo.near(unlocated, 5000) is None
        assert repo.near(2_000_000_000, 5000) is None


def test_near_leaves_out_the_place_itself_and_non_residential_blocks(pg_conn, mongo_db, buildings):
    non_residential = {b[0] for b in buildings if b[6] is False}
    for repo in repos(pg_conn, mongo_db):
        for o in origins(buildings):
            ids = {n.building_id for n in repo.near(o[0], 5000)}
            assert o[0] not in ids and not ids & non_residential


@pytest.mark.parametrize(
    "text", ["marine terrace", "17 marine ter", "Blk 186 Bishan Street 13", "bishan street", "ROAD", "10", "a%b"]
)
def test_find_matches_a_filter_in_python(pg_conn, mongo_db, buildings, text):
    parsed = parse_place(text)
    expected = sorted(
        (
            (b[2], len(b[1]), b[1], b[0], b[3], b[4] is not None)
            for b in buildings
            if parsed.street in b[2] and (parsed.block is None or b[1] == parsed.block)
        ),
    )[:SUGGESTIONS]
    for repo in repos(pg_conn, mongo_db):
        got = [
            (p.street, len(p.block), p.block, p.building_id, p.town, p.located)
            for p in repo.find(parsed.block, parsed.street, SUGGESTIONS)
        ]
        assert got == expected, (repo.__class__.__name__, text)


def test_full_words_find_the_abbreviated_street(pg_conn, mongo_db):
    for repo in repos(pg_conn, mongo_db):
        full = parse_place("17 Marine Terrace")
        short = parse_place("17 marine ter")
        found = repo.find(full.block, full.street, SUGGESTIONS)
        assert found and found == repo.find(short.block, short.street, SUGGESTIONS)
        assert [(p.block, p.street) for p in found] == [("17", "MARINE TER")]
