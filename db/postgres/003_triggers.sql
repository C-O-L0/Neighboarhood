-- Business rules that a CHECK constraint cannot express (they read another table).

-- (a) A sale must be of a resale flat type and may not predate its block's completion year.
-- Fires for COPY too, so the initial load proves the snapshot has no violations.
CREATE FUNCTION check_resale_transaction() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    completed SMALLINT;
BEGIN
    IF NOT (SELECT is_resale_type FROM flat_type WHERE flat_type_id = NEW.flat_type_id) THEN
        RAISE EXCEPTION 'flat type % is not sold on the resale market', NEW.flat_type_id
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT year_completed INTO completed FROM building WHERE building_id = NEW.building_id;
    IF completed IS NOT NULL AND EXTRACT(YEAR FROM NEW.txn_month) < completed THEN
        RAISE EXCEPTION 'sale in % predates block completion in %', to_char(NEW.txn_month, 'YYYY-MM'), completed
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER resale_transaction_check
BEFORE INSERT OR UPDATE OF building_id, flat_type_id, txn_month ON resale_transaction
FOR EACH ROW EXECUTE FUNCTION check_resale_transaction();

-- (b) At most 50 saved blocks per buyer. Locking the buyer row first serialises
-- concurrent saves by the same buyer, so two sessions cannot both see 49 and insert.
CREATE FUNCTION check_saved_building_cap() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    saved INT;
BEGIN
    PERFORM 1 FROM buyer WHERE buyer_id = NEW.buyer_id FOR UPDATE;
    SELECT count(*) INTO saved FROM saved_building WHERE buyer_id = NEW.buyer_id;
    IF saved >= 50 THEN
        RAISE EXCEPTION 'buyer % already has 50 saved blocks', NEW.buyer_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER saved_building_cap
BEFORE INSERT OR UPDATE OF buyer_id ON saved_building
FOR EACH ROW EXECUTE FUNCTION check_saved_building_cap();
