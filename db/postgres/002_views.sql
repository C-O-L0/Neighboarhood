-- Views, the town-month materialised view and its refresh function.

-- One row per sale with every lookup resolved. Search and the building page read this.
CREATE VIEW v_transaction_detail AS
SELECT
    t.txn_id,
    t.txn_month,
    t.building_id,
    b.block,
    b.street,
    tw.town_id,
    tw.name                                              AS town,
    t.flat_type_id,
    ft.name                                              AS flat_type,
    t.flat_model_id,
    fm.name                                              AS flat_model,
    t.storey_min,
    t.storey_max,
    lpad(t.storey_min::text, 2, '0') || ' TO ' || lpad(t.storey_max::text, 2, '0') AS storey_range,
    t.floor_area_sqm,
    b.lease_commence_year,
    t.remaining_lease_months,
    t.resale_price,
    round(t.resale_price / t.floor_area_sqm, 2)          AS price_psm
FROM resale_transaction t
JOIN building   b  ON b.building_id = t.building_id
JOIN town       tw ON tw.town_id = b.town_id
JOIN flat_type  ft ON ft.flat_type_id = t.flat_type_id
JOIN flat_model fm ON fm.flat_model_id = t.flat_model_id;

-- Dwelling units per block, derived from supply (replaces the source total_dwelling_units).
CREATE VIEW v_building_units AS
SELECT
    b.building_id,
    coalesce(sum(s.sold_units), 0)::int                     AS sold_units,
    coalesce(sum(s.rental_units), 0)::int                   AS rental_units,
    coalesce(sum(s.sold_units + s.rental_units), 0)::int    AS total_units
FROM building b
LEFT JOIN building_flat_supply s ON s.building_id = b.building_id
GROUP BY b.building_id;

-- Live yearly figures per town and flat type (no materialisation; for comparison with the MV).
CREATE VIEW v_town_year_stats AS
SELECT
    b.town_id,
    t.flat_type_id,
    EXTRACT(YEAR FROM t.txn_month)::int                                  AS year,
    count(*)::int                                                        AS n,
    percentile_disc(0.5) WITHIN GROUP (ORDER BY t.resale_price)          AS median_price,
    round(avg(t.resale_price), 2)                                        AS avg_price,
    round(avg(t.resale_price / t.floor_area_sqm), 2)                     AS avg_psm
FROM resale_transaction t
JOIN building b ON b.building_id = t.building_id
GROUP BY b.town_id, t.flat_type_id, EXTRACT(YEAR FROM t.txn_month);

-- Monthly figures per town and flat type. Created empty: the first population is a
-- plain REFRESH (done by etl/load_pg.py through refresh_town_stats), later ones run
-- CONCURRENTLY, which needs the unique index below.
CREATE MATERIALIZED VIEW mv_town_month_stats AS
SELECT
    b.town_id,
    t.flat_type_id,
    t.txn_month                                                          AS month,
    count(*)::int                                                        AS n,
    percentile_disc(0.5) WITHIN GROUP (ORDER BY t.resale_price)          AS median_price,
    round(avg(t.resale_price / t.floor_area_sqm), 2)                     AS avg_psm
FROM resale_transaction t
JOIN building b ON b.building_id = t.building_id
GROUP BY b.town_id, t.flat_type_id, t.txn_month
WITH NO DATA;

CREATE UNIQUE INDEX mv_town_month_stats_key ON mv_town_month_stats (town_id, flat_type_id, month);

-- Runs as the owner so app_admin can refresh without owning the view.
-- Fixed search_path stops a caller from shadowing objects through their own schema.
CREATE FUNCTION refresh_town_stats() RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF (SELECT relispopulated FROM pg_class WHERE oid = 'public.mv_town_month_stats'::regclass) THEN
        REFRESH MATERIALIZED VIEW CONCURRENTLY public.mv_town_month_stats;
    ELSE
        REFRESH MATERIALIZED VIEW public.mv_town_month_stats;
    END IF;
END;
$$;
REVOKE EXECUTE ON FUNCTION refresh_town_stats() FROM PUBLIC;
