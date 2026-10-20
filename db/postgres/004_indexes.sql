-- Secondary indexes. Primary keys and UNIQUE constraints already have theirs.
-- MongoDB gets the same key shapes (db/mongo/indexes.py) so benchmark pairs are like for like.

-- Search and trends: filter by flat type, range on month.
CREATE INDEX resale_transaction_type_month_idx ON resale_transaction (flat_type_id, txn_month);

-- Building page: a block's sales, newest first.
CREATE INDEX resale_transaction_building_month_idx ON resale_transaction (building_id, txn_month DESC);

-- Price sorts within a flat type; txn_id makes the order total (tie-break rule).
CREATE INDEX resale_transaction_type_price_idx ON resale_transaction (flat_type_id, resale_price, txn_id);

-- Town filters join through building.
CREATE INDEX building_town_idx ON building (town_id);

-- RESTRICT check when a building is deleted, and "who saved this block".
CREATE INDEX saved_building_building_idx ON saved_building (building_id);
