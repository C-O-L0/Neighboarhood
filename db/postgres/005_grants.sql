-- Least-privilege grants beyond the defaults set by db/migrate.py.
-- Defaults already give app_rw, app_admin and app_ro SELECT on every table and view
-- the owner creates, and take EXECUTE on new functions away from PUBLIC.
--
--   app_rw     normal web requests: writes accounts and saved blocks only
--   app_admin  admin routes: also records and deletes sales and refreshes the MV
--   app_ro     benchmark: read only

GRANT INSERT, UPDATE, DELETE ON buyer, saved_building TO app_rw, app_admin;
GRANT USAGE ON SEQUENCE buyer_buyer_id_seq TO app_rw, app_admin;

GRANT INSERT, UPDATE, DELETE ON resale_transaction TO app_admin;
GRANT USAGE ON SEQUENCE resale_transaction_txn_id_seq TO app_admin;
GRANT EXECUTE ON FUNCTION refresh_town_stats() TO app_admin;

-- The migration log is internal.
REVOKE ALL ON schema_migration FROM app_rw, app_admin, app_ro;
