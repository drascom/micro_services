ALTER TABLE scan_runs ADD COLUMN requested_at TEXT;

UPDATE scan_runs SET requested_at = started_at WHERE requested_at IS NULL;

CREATE INDEX scan_runs_requested_at_idx ON scan_runs(requested_at);