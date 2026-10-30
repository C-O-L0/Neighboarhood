"""The single registry of PostgreSQL / MongoDB comparison cases.

Each feature registers a Case. `pg_call(conn, params)` and `mongo_call(db, params)` return DTOs;
`params(conn)` returns the parameter dicts to run, given a PostgreSQL connection so it can look
at the loaded data. The pytest files here iterate over CASES, and Phase 4's bench/verify.py will
iterate over the same registry on the full data.

Sampling rule: with at most SAMPLE_MAX buildings loaded (the sample), every case runs its full
parameter list; otherwise each feature returns the fixed full-data list it declares.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any

from app.repos.dto import NewSale
from app.repos.mongo.accounts import MongoAccountRepo
from app.repos.mongo.building import MongoBuildingRepo
from app.repos.mongo.explore import MongoExploreRepo
from app.repos.mongo.food import MongoFoodRepo
from app.repos.mongo.places import MongoPlaceRepo
from app.repos.mongo.rankings import MongoRankingRepo
from app.repos.mongo.saved import MongoSavedRepo
from app.repos.mongo.search import MongoSearchRepo
from app.repos.mongo.trends import MongoTrendRepo
from app.repos.pg.accounts import PgAccountRepo
from app.repos.pg.building import PgBuildingRepo
from app.repos.pg.explore import PgExploreRepo
from app.repos.pg.food import PgFoodRepo
from app.repos.pg.places import PgPlaceRepo
from app.repos.pg.rankings import PgRankingRepo
from app.repos.pg.saved import PgSavedRepo
from app.repos.pg.search import PgSearchRepo
from app.repos.pg.trends import PgTrendRepo
from app.services.explore import FitQuery, window_ending
from app.services.places import RADII_M, SUGGESTIONS, parse_place
from app.services.rankings import RankingQuery
from app.services.search import RESALE_TYPES, SearchQuery
from app.services.trends import TrendQuery, add_months

SAMPLE_MAX = 1000
FULL_DATA_STEP = 25  # building cases: every 25th building id in id order, plus the named blocks


@dataclass(frozen=True)
class Case:
    name: str
    pg_call: Callable[[Any, dict], Any]
    mongo_call: Callable[[Any, dict], Any]
    params: Callable[[Any], list[dict]]
    writes: bool = False  # True for a case that writes; bench/verify.py never runs those on benchmark data


def is_sample(conn) -> bool:
    return conn.execute("SELECT count(*) FROM building").fetchone()[0] <= SAMPLE_MAX


def building_params(conn) -> list[dict]:
    ids = [r[0] for r in conn.execute("SELECT building_id FROM building ORDER BY building_id").fetchall()]
    if len(ids) > SAMPLE_MAX:
        named = conn.execute(
            """
            SELECT building_id FROM building WHERE NOT in_property_dataset
            UNION
            SELECT building_id FROM building WHERE block = '37' AND street = 'TEBAN GDNS RD'
            UNION
            (SELECT building_id FROM building b
             WHERE in_property_dataset
               AND NOT EXISTS (SELECT 1 FROM building_flat_supply s WHERE s.building_id = b.building_id)
             ORDER BY building_id LIMIT 1)
            """
        ).fetchall()
        ids = sorted({*ids[::FULL_DATA_STEP], *(r[0] for r in named)})
    return [{"building_id": i} for i in ids]


# Search parameter sets are raw query strings, as a browser sends them. Each returns rows on the
# sample (mainly BISHAN, MARINE PARADE and BUKIT TIMAH, plus one building each in GEYLANG and
# JURONG EAST) and on full data.
DUPLICATE_PAIR = {
    "block": "18",
    "street": "MARINE TER",
    "month_from": "2017-02",
    "month_to": "2017-02",
    "flat_type": "4 ROOM",
}
TIED_PRICE = {"price_min": "550000", "price_max": "550000"}  # 79 sales on the sample, 64 of them 4 ROOM
TIED_AREA = {"area_min": "121", "area_max": "121"}  # 550 sales on the sample

SEARCH_PARAMS: list[dict] = [
    {"town": "BISHAN"},
    {"town": "MARINE PARADE", "sort": "price", "order": "asc"},
    {"flat_type": "3 ROOM"},
    {"flat_model": "Maisonette"},
    {"month_from": "2020-01", "month_to": "2020-12"},
    {"price_min": "400000", "price_max": "500000.00"},
    {"area_min": "90", "area_max": "100.0"},
    {"street": "QUEEN'S"},
    {"block": "18"},
    {"town": "BISHAN", "flat_type": "4 ROOM", "price_min": "500000", "month_from": "2022-01"},
    {"town": "MARINE PARADE", "flat_model": "Improved", "area_min": "60", "street": "marine"},
    {"sort": "month", "order": "asc"},
    {"sort": "month", "order": "desc"},
    {"sort": "price", "order": "asc"},
    {"sort": "price", "order": "desc"},
    {"sort": "area", "order": "asc"},
    {"sort": "area", "order": "desc"},
    {"page": "2"},
    {"town": "BISHAN", "page": "5", "sort": "price"},
    {**TIED_PRICE, "sort": "price", "order": "asc"},
    {**TIED_PRICE, "sort": "price", "order": "asc", "page": "2"},
    {**TIED_PRICE, "sort": "price", "order": "desc", "page": "2"},
    {**TIED_AREA, "sort": "area", "order": "desc", "page": "2"},
    {},
]
# Cases about literal matching, duplicates and paging past the end; some return no rows.
SEARCH_EDGE_PARAMS: list[dict] = [
    {"street": "ST."},
    {"street": "%"},
    {"street": "_"},
    {"street": ".*"},
    {"street": "' OR 1=1 --"},
    {"street": "\\"},
    {"street": "BISHAN ST 1_"},
    DUPLICATE_PAIR,
    {**DUPLICATE_PAIR, "page": "2"},
    {"page": "10000"},
    {"town": "BISHAN", "page": "10000", "sort": "area", "order": "asc"},
]
# Added on full data only (more than SAMPLE_MAX buildings): the street with a dot in its name.
FULL_DATA_EDGE_PARAMS: list[dict] = [
    {"street": "ST. GEORGE'S"},
    {"town": "TAMPINES", "sort": "price", "page": "3"},
    {"flat_type": "5 ROOM", "month_from": "2023-01", "month_to": "2023-06", "sort": "area", "order": "asc"},
]


def search_params(conn) -> list[dict]:
    return list(SEARCH_PARAMS)


def search_edge_params(conn) -> list[dict]:
    return SEARCH_EDGE_PARAMS + ([] if is_sample(conn) else FULL_DATA_EDGE_PARAMS)


def _query(p: dict) -> SearchQuery:
    return SearchQuery.model_validate(p)


def _pg_search(conn, p):
    return PgSearchRepo(conn).search(_query(p))


def _mongo_search(db, p):
    return MongoSearchRepo(db).search(_query(p))


# Trend series: every (town, resale flat type) pair with sales, unbounded and with a range whose
# bounds are two months inside the series that have sales. On full data (more pairs than
# TREND_SAMPLE_PAIRS) the list is exactly 10 distinct pairs chosen by a fixed rule (select_full_pairs):
# the first 3 pairs in (town, flat type) order that have missing months, then pairs from every
# (len // 10)th position of that order that are not already chosen, until there are 10; sorted.
TREND_SAMPLE_PAIRS = 40
TREND_FULL_PAIRS = 10


def trend_series(conn) -> dict[tuple[str, str], list]:
    """(town, flat type) -> ascending months with sales, from the town-month view."""
    rows = conn.execute(
        """
        SELECT tw.name, ft.name, array_agg(m.month ORDER BY m.month)
        FROM mv_town_month_stats m
        JOIN town tw USING (town_id)
        JOIN flat_type ft USING (flat_type_id)
        GROUP BY tw.name, ft.name
        ORDER BY tw.name COLLATE "C", ft.name COLLATE "C"
        """
    ).fetchall()
    return {(t, f): months for t, f, months in rows}


def has_gap(months: list) -> bool:
    return len(months) != (months[-1].year - months[0].year) * 12 + months[-1].month - months[0].month + 1


def select_full_pairs(keys: list, gappy: set) -> list:
    """The full-data selection rule: 3 gappy pairs first, then stride pairs not yet chosen, 10 in all."""
    chosen = [k for k in keys if k in gappy][:3]
    for k in keys[:: max(1, len(keys) // TREND_FULL_PAIRS)]:
        if len(chosen) == TREND_FULL_PAIRS:
            break
        if k not in chosen:
            chosen.append(k)
    for k in keys:  # only reached when the stride gives fewer than enough distinct pairs
        if len(chosen) == TREND_FULL_PAIRS:
            break
        if k not in chosen:
            chosen.append(k)
    return sorted(chosen)


def trend_params(conn) -> list[dict]:
    series = trend_series(conn)
    keys = list(series)
    if len(keys) > TREND_SAMPLE_PAIRS:
        keys = select_full_pairs(keys, {k for k in keys if has_gap(series[k])})
    params = []
    for town, flat_type in keys:
        params.append({"town": town, "flat_type": flat_type})
        months = series[town, flat_type]
        if len(months) >= 2:
            lo, hi = months[len(months) // 4], months[3 * len(months) // 4]
            params.append(
                {"town": town, "flat_type": flat_type, "month_from": f"{lo:%Y-%m}", "month_to": f"{hi:%Y-%m}"}
            )
    return params


def _pg_trend(conn, p):
    return PgTrendRepo(conn).trend(TrendQuery.model_validate(p))


def _mongo_trend(db, p):
    return MongoTrendRepo(db).trend(TrendQuery.model_validate(p))


# Rankings: every year present in the loaded data, with the flat type absent and with each resale
# flat type (types with no sales that year return an empty list in both databases).
def ranking_years(conn) -> list[int]:
    rows = conn.execute(
        "SELECT DISTINCT EXTRACT(YEAR FROM txn_month)::int FROM resale_transaction ORDER BY 1"
    ).fetchall()
    return [r[0] for r in rows]


def ranking_params(conn) -> list[dict]:
    return [
        {"year": str(y), **({"flat_type": t} if t else {})} for y in ranking_years(conn) for t in (None, *RESALE_TYPES)
    ]


def _pg_rank(conn, p):
    return PgRankingRepo(conn).rank(RankingQuery.model_validate(p))


def _mongo_rank(db, p):
    return MongoRankingRepo(db).rank(RankingQuery.model_validate(p))


# Synthetic buyers (etl/synth_buyers.py): ids 1 to 200 exist in both databases. Accounts compare
# equal ignoring password_hash (a separate test checks the hashes), saves compare including saved_at.
def buyer_params(conn) -> list[dict]:
    rows = conn.execute("SELECT buyer_id FROM buyer WHERE buyer_id <= 200 ORDER BY buyer_id").fetchall()
    return [{"buyer_id": r[0]} for r in rows]


def _without_hash(account):
    return None if account is None else replace(account, password_hash="")


def _pg_account(conn, p):
    return _without_hash(PgAccountRepo(conn).by_id(p["buyer_id"]))


def _mongo_account(db, p):
    return _without_hash(MongoAccountRepo(db).by_id(p["buyer_id"]))


# Saved-block script (task 3.2). Unlike the cases above it writes, so it is not in CASES: tests/equivalence/
# test_saved.py runs it on a fresh test buyer in each database and compares outcomes step by step.
# A step is (operation, building_id, note); the operation is "save", "edit" or "remove".
MISSING_BUILDING = 2_000_000_000  # a valid INT that is no building's id
SAVE_CAP = 50
SavedStep = tuple[str, int, str | None]


def saved_script(conn) -> list[SavedStep]:
    rows = conn.execute("SELECT building_id, in_property_dataset FROM building ORDER BY building_id").fetchall()
    stub = next(r[0] for r in rows if not r[1])
    plain = [r[0] for r in rows if r[1]]
    a, b, c = plain[:3]
    steps: list[SavedStep] = [
        ("save", a, "note A"),
        ("save", b, None),
        ("save", c, "note C"),
        ("save", stub, "the stub"),
        ("edit", b, "now with a note"),
        ("remove", a, None),
        ("remove", a, None),  # not_saved
        ("save", b, "again"),  # already_saved
        ("edit", a, "gone"),  # not_saved
        ("save", MISSING_BUILDING, None),  # no_such_building
    ]
    saved = {b, c, stub}
    for building_id in plain:
        if len(saved) == SAVE_CAP:
            break
        if building_id not in saved:
            steps.append(("save", building_id, None))
            saved.add(building_id)
    extra = next(i for i in plain if i not in saved)
    steps += [
        ("save", extra, None),  # cap_reached
        ("save", b, None),  # already_saved at the cap
        ("save", MISSING_BUILDING, None),  # no_such_building at the cap
    ]
    return steps


def run_step(repo, buyer_id: int, step: SavedStep):
    op, building_id, note = step
    if op == "save":
        return repo.save(buyer_id, building_id, note)
    if op == "edit":
        return repo.edit_note(buyer_id, building_id, note)
    return repo.remove(buyer_id, building_id)


# Admin sale script (task 3.3). Like the saved-block script it writes, so it is not in CASES: it picks two blocks in one
# town (A has at least two loaded sales and a completion year, B has sales) and a flat type and month that A already
# has, so recorded sales land in an existing trends series. tests/equivalence/test_admin_sales.py runs it in both DBs.
@dataclass(frozen=True)
class AdminPlan:
    a_id: int
    a_block: str
    a_street: str
    a_year_completed: int
    b_id: int
    b_block: str
    b_street: str
    town: str
    flat_type: str
    flat_model: str
    month: date
    snapshot_txn_id: int  # A's oldest loaded sale, the one the script deletes


def admin_plan(conn) -> AdminPlan:
    candidates = conn.execute(
        """
        SELECT b.building_id, b.block, b.street, b.year_completed, tw.name
        FROM building b JOIN town tw USING (town_id)
        WHERE b.in_property_dataset
          AND (SELECT count(*) FROM resale_transaction t WHERE t.building_id = b.building_id) >= 2
        ORDER BY b.building_id
        """
    ).fetchall()
    for a_id, block, street, year, town in candidates:
        latest = conn.execute(
            "SELECT flat_type, flat_model, txn_month FROM v_transaction_detail WHERE building_id = %s"
            " ORDER BY txn_month DESC, txn_id DESC LIMIT 1",
            (a_id,),
        ).fetchone()
        other = conn.execute(
            """
            SELECT b.building_id, b.block, b.street FROM building b JOIN town tw USING (town_id)
            WHERE tw.name = %s AND b.building_id <> %s
              AND (b.year_completed IS NULL OR b.year_completed <= %s)
              AND EXISTS (SELECT 1 FROM resale_transaction t WHERE t.building_id = b.building_id)
            ORDER BY b.building_id LIMIT 1
            """,
            (town, a_id, latest[2].year),
        ).fetchone()
        if other is None:
            continue
        oldest = conn.execute(
            "SELECT txn_id FROM resale_transaction WHERE building_id = %s AND source = 'snapshot'"
            " ORDER BY txn_month, txn_id LIMIT 1",
            (a_id,),
        ).fetchone()
        return AdminPlan(a_id, block, street, year, *other, town, latest[0], latest[1], latest[2], oldest[0])
    raise AssertionError("the loaded data has no block pair for the admin script")


def admin_sale(plan: AdminPlan, building_id: int, price: str, *, month: date | None = None) -> NewSale:
    return NewSale(
        building_id, month or plan.month, plan.flat_type, plan.flat_model, 3, 5,
        Decimal("93.5"), 700, Decimal(price),
    )  # fmt: skip


# Food access (task 3.4). Block view: every geocoded block on the sample (including the stub); on full data every
# FULL_DATA_STEP-th geocoded block id in id order plus the stub. Town view: every town with a geocoded block on the
# sample (5); on full data 3 towns, the ones with the fewest, the median number and the most geocoded blocks
# (ties by name).
FOOD_FULL_TOWNS = 3


def food_block_params(conn) -> list[dict]:
    ids = [r[0] for r in conn.execute(
        "SELECT building_id FROM building WHERE latitude IS NOT NULL ORDER BY building_id"
    ).fetchall()]  # fmt: skip
    if not is_sample(conn):
        stub = [r[0] for r in conn.execute(
            "SELECT building_id FROM building WHERE NOT in_property_dataset AND latitude IS NOT NULL"
        ).fetchall()]  # fmt: skip
        ids = sorted({*ids[::FULL_DATA_STEP], *stub})
    return [{"building_id": i} for i in ids]


def food_town_params(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT tw.name, count(*) FROM building b JOIN town tw USING (town_id)
        WHERE b.latitude IS NOT NULL
        GROUP BY tw.name ORDER BY count(*), tw.name COLLATE "C"
        """
    ).fetchall()
    names = [r[0] for r in rows]
    if not is_sample(conn):
        names = [names[0], names[len(names) // 2], names[-1]]
    return [{"town": n} for n in sorted(names)]


# Explore (task T2). Windows end at the latest month and 24 months earlier, so a second, fully populated year is
# compared too. Budgets: three round amounts, plus each flat type's own recent typical price as reported by
# PostgreSQL, which puts a sale exactly on the budget (sales at the budget count as within it). The same list
# runs on the sample and on full data: every case is one aggregate over one flat type and one window.
EXPLORE_BUDGETS = ("300000", "650000", "1000000")


def explore_window_params(conn) -> list[dict]:
    latest = PgExploreRepo(conn).latest_month()
    return [{"end": latest}, {"end": add_months(latest, -24)}]


def explore_town_params(conn) -> list[dict]:
    out = []
    for w in explore_window_params(conn):
        window = window_ending(w["end"])
        for flat_type in RESALE_TYPES:
            towns = PgExploreRepo(conn).towns_fit(flat_type, Decimal("1"), window)
            budgets = [*EXPLORE_BUDGETS, *([str(towns[len(towns) // 2].median_price)] if towns else [])]
            out += [{**w, "flat_type": flat_type, "budget": b} for b in budgets]
    return out


def _explore_towns(repo, p):
    q = FitQuery.model_validate(p)
    return repo.towns_fit(q.flat_type, q.budget, window_ending(p["end"]))


# Places (task T4). Picker: typed texts covering full words, abbreviations, a block number, a lone block-like
# word, LIKE and regex metacharacters and no match. Nearby: every radius around every located block on the
# sample; on full data every PLACES_FULL_STEP-th located block id. An unlocated block and an unknown id give
# None in both.
PLACE_TEXTS = (
    "marine terrace", "17 marine ter", "Blk 186 Bishan Street 13", "bishan street", "ROAD", "rd", "st", "10",
    "SIN MING", "queen's", "a%b_c", "(x)", "no such street",
)  # fmt: skip
PLACES_FULL_STEP = 100


def place_find_params(conn) -> list[dict]:
    return [{"text": t} for t in PLACE_TEXTS]


def _find_place(repo, p):
    parsed = parse_place(p["text"])
    return repo.find(parsed.block, parsed.street, SUGGESTIONS)


def place_near_params(conn) -> list[dict]:
    located = [r[0] for r in conn.execute(
        "SELECT building_id FROM building WHERE latitude IS NOT NULL ORDER BY building_id"
    ).fetchall()]  # fmt: skip
    unlocated = conn.execute("SELECT min(building_id) FROM building WHERE latitude IS NULL").fetchone()[0]
    if not is_sample(conn):
        located = located[::PLACES_FULL_STEP]
    ids = [*located, unlocated, 2_000_000_000]
    return [{"building_id": i, "radius_m": r} for i in ids for r in RADII_M]


CASES: list[Case] = [
    Case(
        name="building",
        pg_call=lambda conn, p: PgBuildingRepo(conn).get(p["building_id"]),
        mongo_call=lambda db, p: MongoBuildingRepo(db).get(p["building_id"]),
        params=building_params,
    ),
    Case(name="search", pg_call=_pg_search, mongo_call=_mongo_search, params=search_params),
    Case(name="search_edges", pg_call=_pg_search, mongo_call=_mongo_search, params=search_edge_params),
    Case(name="trends", pg_call=_pg_trend, mongo_call=_mongo_trend, params=trend_params),
    Case(name="rankings", pg_call=_pg_rank, mongo_call=_mongo_rank, params=ranking_params),
    Case(name="accounts", pg_call=_pg_account, mongo_call=_mongo_account, params=buyer_params),
    Case(
        name="saved",
        pg_call=lambda conn, p: PgSavedRepo(conn).list(p["buyer_id"]),
        mongo_call=lambda db, p: MongoSavedRepo(db).list(p["buyer_id"]),
        params=buyer_params,
    ),
    Case(
        name="food_block",
        pg_call=lambda conn, p: PgFoodRepo(conn).block(p["building_id"]),
        mongo_call=lambda db, p: MongoFoodRepo(db).block(p["building_id"]),
        params=food_block_params,
    ),
    Case(
        name="food_town",
        pg_call=lambda conn, p: PgFoodRepo(conn).town(p["town"]),
        mongo_call=lambda db, p: MongoFoodRepo(db).town(p["town"]),
        params=food_town_params,
    ),
    Case(
        name="places_find",
        pg_call=lambda conn, p: _find_place(PgPlaceRepo(conn), p),
        mongo_call=lambda db, p: _find_place(MongoPlaceRepo(db), p),
        params=place_find_params,
    ),
    Case(
        name="places_near",
        pg_call=lambda conn, p: PgPlaceRepo(conn).near(p["building_id"], p["radius_m"]),
        mongo_call=lambda db, p: MongoPlaceRepo(db).near(p["building_id"], p["radius_m"]),
        params=place_near_params,
    ),
    Case(
        name="explore_latest",
        pg_call=lambda conn, p: PgExploreRepo(conn).latest_month(),
        mongo_call=lambda db, p: MongoExploreRepo(db).latest_month(),
        params=lambda conn: [{}],
    ),
    Case(
        name="explore_ranges",
        pg_call=lambda conn, p: PgExploreRepo(conn).flat_type_ranges(window_ending(p["end"])),
        mongo_call=lambda db, p: MongoExploreRepo(db).flat_type_ranges(window_ending(p["end"])),
        params=explore_window_params,
    ),
    Case(
        name="explore_baselines",
        pg_call=lambda conn, p: PgExploreRepo(conn).baselines(p["flat_type"], window_ending(p["end"])),
        mongo_call=lambda db, p: MongoExploreRepo(db).baselines(p["flat_type"], window_ending(p["end"])),
        params=lambda conn: [{**w, "flat_type": t} for w in explore_window_params(conn) for t in RESALE_TYPES],
    ),
    Case(
        name="explore_towns",
        pg_call=lambda conn, p: _explore_towns(PgExploreRepo(conn), p),
        mongo_call=lambda db, p: _explore_towns(MongoExploreRepo(db), p),
        params=explore_town_params,
    ),
]
