CREATE TABLE device_overrides (
    device_id TEXT PRIMARY KEY NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    display_name TEXT,
    device_type TEXT,
    updated_at TEXT NOT NULL,
    CHECK (display_name IS NULL OR (length(display_name) BETWEEN 1 AND 128 AND display_name = trim(display_name))),
    CHECK (device_type IS NULL OR device_type IN ('server', 'computer', 'phone', 'tablet', 'television', 'printer', 'network', 'iot', 'unknown'))
);
