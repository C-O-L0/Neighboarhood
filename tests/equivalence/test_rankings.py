"""Rankings: PostgreSQL and MongoDB return the same ranked towns, ties and island medians."""

from collections import defaultdict
from decimal import Decimal
from itertools import groupby

import pytest

from app.repos.dto import TownRank
from app.repos.mongo.rankings import MongoRankingRepo
from app.repos.pg.rankings import PgRankingRepo
from app.services.rankings import RankingQuery
from app.services.search import RESALE_TYPES
from tests.equivalence.cases import is_sample, ranking_params, ranking_years
from tests.equivalence.compare import assert_equivalent

pytestmark = pytest.mark.integration


def both(pg_conn, mongo_db, **params):
    q = RankingQuery.model_validate(params)
    return PgRankingRepo(pg_conn).rank(q), MongoRankingRepo(mongo_db).rank(q)


def lower_median(prices: list[Decimal]) -> Decimal:
    ordered = sorted(prices)
    return ordered[(len(ordered) - 1) // 2]


def test_params_cover_every_year_and_type(pg_conn):
    params = ranking_params(pg_conn)
    years = ranking_years(pg_conn)
    assert len(params) == len(years) * (1 + len(RESALE_TYPES))
    assert {(p["year"], p.get("flat_type")) for p in params} == {
        (str(y), t) for y in years for t in (None, *RESALE_TYPES)
    }


def test_every_year_with_and_without_flat_type_is_equivalent(pg_conn, mongo_db):
    nonempty = 0
    for p in ranking_params(pg_conn):
        pg, mongo = both(pg_conn, mongo_db, **p)
        assert_equivalent(pg, mongo, path=f"rankings{p}")
        nonempty += bool(pg)
    assert nonempty


def test_flat_types_without_sales_are_empty_in_both(pg_conn, mongo_db):
    if not is_sample(pg_conn):
        pytest.skip("sample-specific facts")
    for year in ranking_years(pg_conn):
        for pg, mongo in [both(pg_conn, mongo_db, year=year, flat_type="1 ROOM")]:
            assert pg == mongo == []
    pg, mongo = both(pg_conn, mongo_db, year=2018, flat_type="MULTI-GENERATION")
    assert pg == mongo == []
    for rows in both(pg_conn, mongo_db, year=2018):
        assert "MULTI-GENERATION" not in {r.flat_type for r in rows}


def test_rows_are_typed_and_ordered(pg_conn, mongo_db):
    for rows in both(pg_conn, mongo_db, year=2022):
        assert rows
        keys = [(r.flat_type, r.rank, r.town) for r in rows]
        assert keys == sorted(keys)
        assert len(set((r.flat_type, r.town) for r in rows)) == len(rows)
        for r in rows:
            assert isinstance(r, TownRank) and r.n >= 1 and isinstance(r.beats_island, bool)
            for value in (r.median_price, r.avg_psm, r.island_median):
                assert isinstance(value, Decimal)
            assert r.beats_island == (r.median_price > r.island_median)


def assert_generic_ranks(rows):
    """Ranks non-decreasing, equal medians share a rank, and a rank after k ties is the old rank plus k."""
    for _, part in groupby(rows, key=lambda r: r.flat_type):
        part = list(part)
        medians = [r.median_price for r in part]
        assert medians == sorted(medians, reverse=True)
        expected = []
        for i, m in enumerate(medians):
            expected.append(expected[-1] if i and m == medians[i - 1] else i + 1)
        assert [r.rank for r in part] == expected


def test_generic_rank_properties(pg_conn, mongo_db):
    for year in ranking_years(pg_conn):
        for rows in both(pg_conn, mongo_db, year=year):
            assert_generic_ranks(rows)


def test_ties_on_the_sample(pg_conn, mongo_db):
    if not is_sample(pg_conn):
        pytest.skip("exact tie expectations are facts on the sample")
    for rows in both(pg_conn, mongo_db, year=2017, flat_type="3 ROOM"):
        assert [(r.town, r.rank) for r in rows] == [
            ("BUKIT TIMAH", 1),
            ("MARINE PARADE", 1),
            ("BISHAN", 3),
            ("GEYLANG", 4),
            ("JURONG EAST", 5),
        ]
    for rows in both(pg_conn, mongo_db, year=2022, flat_type="5 ROOM"):
        assert [(r.town, r.rank) for r in rows] == [("BUKIT TIMAH", 1), ("BISHAN", 2), ("MARINE PARADE", 2)]


def raw_sales(pg_conn, year, flat_type) -> dict[str, list[Decimal]]:
    rows = pg_conn.execute(
        """
        SELECT tw.name, t.resale_price
        FROM resale_transaction t
        JOIN building b USING (building_id)
        JOIN town tw ON tw.town_id = b.town_id
        JOIN flat_type ft ON ft.flat_type_id = t.flat_type_id
        WHERE ft.name = %s AND EXTRACT(YEAR FROM t.txn_month) = %s
        """,
        (flat_type, year),
    ).fetchall()
    by_town = defaultdict(list)
    for town, price in rows:
        by_town[town].append(price)
    return by_town


def test_hand_checked_medians(pg_conn, mongo_db):
    year, flat_type = 2022, "4 ROOM"
    sales = raw_sales(pg_conn, year, flat_type)
    island = lower_median([p for prices in sales.values() for p in prices])
    for rows in both(pg_conn, mongo_db, year=year, flat_type=flat_type):
        assert {r.town for r in rows} == set(sales)
        for r in rows:
            assert r.island_median == island
            assert r.n == len(sales[r.town])
            assert r.median_price == lower_median(sales[r.town])
            assert r.beats_island == (lower_median(sales[r.town]) > island)


def test_median_equal_to_island_does_not_beat_it(pg_conn, mongo_db):
    if not is_sample(pg_conn):
        pytest.skip("exact single-town case is a fact on the sample")
    sales = raw_sales(pg_conn, 2017, "2 ROOM")
    assert len(sales) == 1
    for rows in both(pg_conn, mongo_db, year=2017, flat_type="2 ROOM"):
        assert len(rows) == 1
        assert rows[0].median_price == rows[0].island_median
        assert rows[0].beats_island is False
        assert rows[0].rank == 1


def test_beats_island_is_true_for_some_and_false_for_some(pg_conn, mongo_db):
    for rows in both(pg_conn, mongo_db, year=2022):
        assert {r.beats_island for r in rows} == {True, False}


def test_year_boundaries_are_half_open(pg_conn, mongo_db):
    """January of the next year and December of the previous year are excluded."""
    for year in ranking_years(pg_conn):
        total = pg_conn.execute(
            "SELECT count(*) FROM resale_transaction WHERE txn_month >= make_date(%s,1,1)"
            " AND txn_month < make_date(%s,1,1)",
            (year, year + 1),
        ).fetchone()[0]
        for rows in both(pg_conn, mongo_db, year=year):
            assert sum(r.n for r in rows) == total
