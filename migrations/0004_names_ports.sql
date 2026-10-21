CREATE TABLE device_names (
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (device_id, source, name)
);

CREATE TABLE device_port_scans (
    device_id TEXT PRIMARY KEY NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    scanned_at TEXT NOT NULL,
    result_json TEXT NOT NULL
);
