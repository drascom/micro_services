PRAGMA foreign_keys = ON;

CREATE TABLE devices (
    id TEXT PRIMARY KEY NOT NULL,
    display_name TEXT NOT NULL,
    device_type TEXT NOT NULL DEFAULT 'unknown',
    status TEXT NOT NULL DEFAULT 'unknown',
    manufacturer TEXT,
    model TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT REFERENCES devices(id) ON DELETE SET NULL,
    source TEXT NOT NULL,
    source_id TEXT,
    payload_json TEXT NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE TABLE device_addresses (device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE, kind TEXT NOT NULL, address TEXT NOT NULL, PRIMARY KEY (device_id, kind, address));
CREATE TABLE device_services (id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE, service_type TEXT NOT NULL, name TEXT, protocol TEXT NOT NULL, port INTEGER NOT NULL, hostname TEXT, txt_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE device_sources (device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE, source TEXT NOT NULL, source_id TEXT, PRIMARY KEY (device_id, source));
CREATE TABLE scan_runs (id TEXT PRIMARY KEY NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, error TEXT);
CREATE TABLE source_health (source TEXT PRIMARY KEY NOT NULL, healthy INTEGER NOT NULL, last_success_at TEXT, last_error_at TEXT, last_error TEXT);
CREATE TABLE device_events (id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT REFERENCES devices(id) ON DELETE CASCADE, event_type TEXT NOT NULL, data_json TEXT NOT NULL, occurred_at TEXT NOT NULL);
CREATE TABLE manual_identity_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, match_json TEXT NOT NULL, device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE, created_at TEXT NOT NULL);

CREATE INDEX observations_observed_at_idx ON observations(observed_at);
CREATE INDEX device_events_device_time_idx ON device_events(device_id, occurred_at);
