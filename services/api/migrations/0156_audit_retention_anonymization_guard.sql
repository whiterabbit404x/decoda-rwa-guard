-- Let the published retention policy anonymize an audit row, and nothing else.
--
-- Migration 0100 installed `guard_audit_logs_append_only`, which permits DELETE
-- when `app.retention_worker='on'` and raises on EVERY update. But the retention
-- policy this product publishes does not delete the audit log at the one-year
-- mark — it ANONYMIZES it: `pilot_retention.PILOT_DELETION_MODES['audit_logs']`
-- is `anonymize`, and `data_retention.ANONYMIZE_SQL['audit_logs']` implements
-- that with an UPDATE. Against real PostgreSQL that UPDATE raised
-- 'audit_logs is append-only', so the anonymization step could never run and
-- the /privacy promise was unproven.
--
-- The fix is NOT to let the retention worker write to audit_logs. It is to
-- recognize one specific transformation and refuse everything else:
--
--   DELETE  -> unchanged: `app.retention_worker='on'` only.
--   UPDATE  -> `app.audit_retention_anonymize='on'` only, AND the row must
--              differ from its stored self in exactly the three columns the
--              privacy statement names, moving to exactly the anonymized
--              values it names.
--
-- The UPDATE capability is a SEPARATE transaction-local flag from the DELETE
-- capability on purpose. `app.retention_worker='on'` remains a delete-only
-- authorization: it cannot be turned into a write by any statement, so the
-- scheduled-deletion path gains no power to rewrite history.
--
-- Immutability is enforced by whole-row comparison rather than a hand-listed
-- column set, so a column added to audit_logs later is frozen by default
-- instead of silently becoming writable by the retention worker.
--
-- Rollback: forward-only. Reverting to the 0100 body restores the defect
-- (anonymization blocked); it does not expose any data, because this migration
-- widens nothing an ordinary caller can reach. No audit row is read, rewritten
-- or deleted here.

CREATE OR REPLACE FUNCTION guard_audit_logs_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    -- The only columns retention anonymization may destroy. Mirrors the
    -- /privacy statement: "the actor identity, IP address and event details are
    -- destroyed and only the action, the object, the timestamp and the
    -- integrity chain remain."
    mutable_columns CONSTANT text[] := ARRAY['user_id', 'ip_address', 'metadata'];
    frozen_old jsonb;
    frozen_new jsonb;
    changed text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF current_setting('app.retention_worker', true) = 'on' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'audit_logs is append-only';
    END IF;

    -- COALESCE, not a bare comparison: current_setting(..., true) returns NULL
    -- when the flag was never set, and `NULL <> 'on'` is NULL, which would fall
    -- through this guard instead of failing closed.
    IF COALESCE(current_setting('app.audit_retention_anonymize', true), '') <> 'on' THEN
        RAISE EXCEPTION 'audit_logs is append-only';
    END IF;

    -- Everything outside the approved set must be byte-identical: primary key,
    -- workspace ownership, action, entity reference, created_at, and the whole
    -- hash chain (row_hash, previous_row_hash, hash_algorithm, sealed_at).
    frozen_old := to_jsonb(OLD) - mutable_columns;
    frozen_new := to_jsonb(NEW) - mutable_columns;
    IF frozen_new IS DISTINCT FROM frozen_old THEN
        SELECT string_agg(key, ', ' ORDER BY key) INTO changed
        FROM jsonb_each(frozen_new) AS n
        WHERE n.value IS DISTINCT FROM (frozen_old -> n.key);
        RAISE EXCEPTION
            'audit_logs retention anonymization may not change immutable columns: %',
            COALESCE(changed, '(column set changed)');
    END IF;

    -- And the approved columns may move only to the approved anonymized values,
    -- so the capability can destroy identity but never author a fact: no
    -- arbitrary metadata, no substituted actor, no substituted IP.
    IF NEW.user_id IS NOT NULL
       OR NEW.ip_address IS NOT NULL
       OR NEW.metadata IS DISTINCT FROM jsonb_build_object('_retention_anonymized', true)
    THEN
        RAISE EXCEPTION
            'audit_logs retention anonymization must clear user_id and ip_address and set metadata to the retention marker';
    END IF;

    RETURN NEW;
END;
$$;

-- The trigger itself is left in place (0100 created it and it resolves the
-- function by name, so replacing the body above is atomic and leaves no window
-- in which audit_logs is unguarded). Recreate it ONLY if a deployment somehow
-- lacks it; never drop a live guard to re-add it.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'audit_logs_append_only'
          AND tgrelid = 'public.audit_logs'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER audit_logs_append_only
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION guard_audit_logs_append_only();
    END IF;
END;
$$;
