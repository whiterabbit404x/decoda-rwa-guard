ALTER TABLE monitored_systems
    ALTER COLUMN is_enabled SET DEFAULT TRUE;

UPDATE monitored_systems
SET is_enabled = TRUE
WHERE is_enabled IS NULL;

ALTER TABLE monitored_systems
    ALTER COLUMN is_enabled SET NOT NULL;
