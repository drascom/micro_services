use crate::{discovery::ProviderHealth, merge::MergedDevice};
use chrono::{DateTime, Utc};
use inet_viewer_shared::{
    Device, DeviceId, DeviceName, PortScan, DeviceStatus, DeviceType, DiscoverySourceKind, MacAddress, Service,
    TransportProtocol,
};
use sqlx::{Row, Sqlite, SqlitePool, Transaction};
use std::{collections::HashMap, str::FromStr};
use uuid::Uuid;

#[derive(Clone, Debug, serde::Serialize)]
pub struct ScanRecord {
    pub id: String,
    pub status: String,
    pub requested_at: DateTime<Utc>,
    pub started_at: DateTime<Utc>,
    pub finished_at: Option<DateTime<Utc>>,
    pub error: Option<String>,
}

#[derive(Clone, Debug, serde::Serialize)]
pub struct DeviceEventRecord {
    pub event_type: String,
    pub data: serde_json::Value,
    pub occurred_at: DateTime<Utc>,
}

#[derive(Clone)]
pub struct Repository {
    pool: SqlitePool,
}

impl Repository {
    pub fn new(pool: SqlitePool) -> Self {
        Self { pool }
    }

    pub async fn save_merged(&self, merged: &mut [MergedDevice]) -> anyhow::Result<()> {
        let mut transaction = self.pool.begin().await?;
        for merged_device in merged {
            if let Some(id) = find_existing_id(&mut transaction, merged_device).await? {
                merged_device.device.id = id;
            }
            save_device(&mut transaction, &merged_device.device).await?;
            save_observations(&mut transaction, merged_device).await?;
        }
        transaction.commit().await?;
        Ok(())
    }

    pub async fn repair_duplicate_devices(&self) -> anyhow::Result<u64> {
        let mut transaction = self.pool.begin().await?;
        let mut repaired = 0;
        loop {
            let pair: Option<(String, String)> = sqlx::query_as(
                "SELECT older.id, newer.id FROM device_addresses shared_old JOIN device_addresses shared_new ON shared_new.kind = shared_old.kind AND shared_new.address = shared_old.address AND shared_new.device_id <> shared_old.device_id JOIN devices older ON older.id = shared_old.device_id JOIN devices newer ON newer.id = shared_new.device_id WHERE shared_old.kind IN ('mac', 'lan') AND (older.first_seen_at < newer.first_seen_at OR (older.first_seen_at = newer.first_seen_at AND older.id < newer.id)) AND (shared_old.kind = 'mac' OR NOT EXISTS (SELECT 1 FROM device_addresses old_mac JOIN device_addresses new_mac ON new_mac.kind = 'mac' AND new_mac.device_id = newer.id WHERE old_mac.kind = 'mac' AND old_mac.device_id = older.id AND old_mac.address <> new_mac.address)) ORDER BY older.first_seen_at, older.id, newer.first_seen_at, newer.id LIMIT 1",
            )
            .fetch_optional(&mut *transaction)
            .await?;
            let Some((keeper, duplicate)) = pair else {
                break;
            };
            merge_device_rows(&mut transaction, &keeper, &duplicate).await?;
            repaired += 1;
        }
        transaction.commit().await?;
        Ok(repaired)
    }

    pub async fn list_devices(&self) -> anyhow::Result<Vec<Device>> {
        let rows = sqlx::query(
            "SELECT devices.id, COALESCE(device_overrides.display_name, devices.display_name) AS display_name, COALESCE(device_overrides.device_type, devices.device_type) AS device_type, status, manufacturer, model, first_seen_at, last_seen_at FROM devices LEFT JOIN device_overrides ON device_overrides.device_id = devices.id ORDER BY display_name, devices.id",
        )
        .fetch_all(&self.pool)
        .await?;
        let mut devices = Vec::with_capacity(rows.len());
        for row in rows {
            let id_text: String = row.try_get("id")?;
            let id = DeviceId(Uuid::parse_str(&id_text)?);
            let mut device = Device {
                id: id.clone(),
                display_name: row.try_get("display_name")?,
                hostnames: Vec::new(),
                names: Vec::new(),
                lan_ips: Vec::new(),
                netbird_ip: None,
                netbird_ipv6: None,
                mac_addresses: Vec::new(),
                manufacturer: row.try_get("manufacturer")?,
                model: row.try_get("model")?,
                device_type: parse_device_type(row.try_get::<String, _>("device_type")?.as_str()),
                status: parse_status(row.try_get::<String, _>("status")?.as_str()),
                sources: Vec::new(),
                services: Vec::new(),
                first_seen_at: parse_time(row.try_get::<String, _>("first_seen_at")?.as_str())?,
                last_seen_at: parse_time(row.try_get::<String, _>("last_seen_at")?.as_str())?,
            };
            load_addresses(&self.pool, &id_text, &mut device).await?;
            load_names(&self.pool, &id_text, &mut device).await?;
            load_sources(&self.pool, &id_text, &mut device).await?;
            load_services(&self.pool, &id_text, &mut device).await?;
            devices.push(device);
        }
        Ok(devices)
    }

    pub async fn get_device(&self, id: &str) -> anyhow::Result<Option<Device>> {
        Ok(self
            .list_devices()
            .await?
            .into_iter()
            .find(|device| device.id.0.to_string() == id))
    }

    pub async fn update_device_override(
        &self,
        id: &str,
        display_name: Option<Option<&str>>,
        device_type: Option<DeviceType>,
    ) -> anyhow::Result<Option<Device>> {
        if self.get_device(id).await?.is_none() {
            return Ok(None);
        }
        let mut transaction = self.pool.begin().await?;
        sqlx::query("INSERT OR IGNORE INTO device_overrides (device_id, updated_at) VALUES (?, ?)")
            .bind(id)
            .bind(Utc::now().to_rfc3339())
            .execute(&mut *transaction)
            .await?;
        if let Some(display_name) = display_name {
            sqlx::query(
                "UPDATE device_overrides SET display_name = ?, updated_at = ? WHERE device_id = ?",
            )
            .bind(display_name)
            .bind(Utc::now().to_rfc3339())
            .bind(id)
            .execute(&mut *transaction)
            .await?;
        }
        if let Some(device_type) = device_type {
            sqlx::query(
                "UPDATE device_overrides SET device_type = ?, updated_at = ? WHERE device_id = ?",
            )
            .bind(device_type_name(device_type))
            .bind(Utc::now().to_rfc3339())
            .bind(id)
            .execute(&mut *transaction)
            .await?;
        }
        sqlx::query("DELETE FROM device_overrides WHERE device_id = ? AND display_name IS NULL AND device_type IS NULL")
            .bind(id)
            .execute(&mut *transaction)
            .await?;
        transaction.commit().await?;
        self.get_device(id).await
    }

    /// Updates only the status (and, when given, `last_seen_at`) of a device.
    pub async fn set_device_status(
        &self,
        id: &str,
        status: DeviceStatus,
        seen_at: Option<DateTime<Utc>>,
    ) -> anyhow::Result<()> {
        sqlx::query("UPDATE devices SET status = ?, last_seen_at = COALESCE(?, last_seen_at) WHERE id = ?")
            .bind(status_name(status))
            .bind(seen_at.map(|time| time.to_rfc3339()))
            .bind(id)
            .execute(&self.pool)
            .await?;
        Ok(())
    }

    /// Replaces the stored port scan of a device.
    pub async fn save_port_scan(&self, device_id: &str, scan: &PortScan) -> anyhow::Result<()> {
        sqlx::query("INSERT INTO device_port_scans (device_id, scanned_at, result_json) VALUES (?, ?, ?) ON CONFLICT(device_id) DO UPDATE SET scanned_at=excluded.scanned_at, result_json=excluded.result_json")
            .bind(device_id)
            .bind(scan.scanned_at.to_rfc3339())
            .bind(serde_json::to_string(scan)?)
            .execute(&self.pool)
            .await?;
        Ok(())
    }

    pub async fn load_port_scan(&self, device_id: &str) -> anyhow::Result<Option<PortScan>> {
        let json: Option<String> =
            sqlx::query_scalar("SELECT result_json FROM device_port_scans WHERE device_id = ?")
                .bind(device_id)
                .fetch_optional(&self.pool)
                .await?;
        json.map(|json| serde_json::from_str(&json).map_err(Into::into))
            .transpose()
    }

    pub async fn start_scan(&self, id: &str, started_at: DateTime<Utc>) -> anyhow::Result<()> {
        let timestamp = started_at.to_rfc3339();
        sqlx::query("INSERT INTO scan_runs (id, status, requested_at, started_at) VALUES (?, 'running', ?, ?)")
            .bind(id).bind(&timestamp).bind(&timestamp).execute(&self.pool).await?;
        Ok(())
    }

    pub async fn finish_scan(
        &self,
        id: &str,
        status: &str,
        error: Option<&str>,
    ) -> anyhow::Result<()> {
        sqlx::query("UPDATE scan_runs SET status = ?, finished_at = ?, error = ? WHERE id = ?")
            .bind(status)
            .bind(Utc::now().to_rfc3339())
            .bind(error)
            .bind(id)
            .execute(&self.pool)
            .await?;
        Ok(())
    }

    pub async fn get_scan(&self, id: &str) -> anyhow::Result<Option<ScanRecord>> {
        let row = sqlx::query("SELECT id, status, requested_at, started_at, finished_at, error FROM scan_runs WHERE id = ?")
            .bind(id).fetch_optional(&self.pool).await?;
        row.map(|row| {
            let finished_at: Option<String> = row.try_get("finished_at")?;
            Ok(ScanRecord {
                id: row.try_get("id")?,
                status: row.try_get("status")?,
                requested_at: parse_time(row.try_get::<String, _>("requested_at")?.as_str())?,
                started_at: parse_time(row.try_get::<String, _>("started_at")?.as_str())?,
                finished_at: finished_at.as_deref().map(parse_time).transpose()?,
                error: row.try_get("error")?,
            })
        })
        .transpose()
    }

    pub async fn record_device_event(
        &self,
        device_id: &str,
        event_type: &str,
        data: &serde_json::Value,
    ) -> anyhow::Result<()> {
        sqlx::query("INSERT INTO device_events (device_id, event_type, data_json, occurred_at) VALUES (?, ?, ?, ?)")
            .bind(device_id).bind(event_type).bind(serde_json::to_string(data)?)
            .bind(Utc::now().to_rfc3339()).execute(&self.pool).await?;
        Ok(())
    }

    pub async fn list_device_events(
        &self,
        device_id: &str,
        limit: u32,
    ) -> anyhow::Result<Vec<DeviceEventRecord>> {
        let rows = sqlx::query("SELECT event_type, data_json, occurred_at FROM device_events WHERE device_id = ? ORDER BY occurred_at DESC, id DESC LIMIT ?")
            .bind(device_id).bind(i64::from(limit)).fetch_all(&self.pool).await?;
        rows.into_iter()
            .map(|row| {
                Ok(DeviceEventRecord {
                    event_type: row.try_get("event_type")?,
                    data: serde_json::from_str(&row.try_get::<String, _>("data_json")?)?,
                    occurred_at: parse_time(row.try_get::<String, _>("occurred_at")?.as_str())?,
                })
            })
            .collect()
    }

    pub async fn save_health(
        &self,
        health: &HashMap<DiscoverySourceKind, ProviderHealth>,
    ) -> anyhow::Result<()> {
        let mut transaction = self.pool.begin().await?;
        for (source, health) in health {
            sqlx::query(
                "INSERT INTO source_health (source, healthy, last_success_at, last_error_at, last_error) VALUES (?, ?, ?, ?, ?) ON CONFLICT(source) DO UPDATE SET healthy=excluded.healthy, last_success_at=excluded.last_success_at, last_error_at=excluded.last_error_at, last_error=excluded.last_error",
            )
            .bind(source_name(*source))
            .bind(health.healthy)
            .bind(health.last_success_at.map(|time| time.to_rfc3339()))
            .bind(health.last_error_at.map(|time| time.to_rfc3339()))
            .bind(&health.last_error)
            .execute(&mut *transaction)
            .await?;
        }
        transaction.commit().await?;
        Ok(())
    }

    pub async fn load_health(
        &self,
    ) -> anyhow::Result<HashMap<DiscoverySourceKind, ProviderHealth>> {
        let rows = sqlx::query(
            "SELECT source, healthy, last_success_at, last_error_at, last_error FROM source_health",
        )
        .fetch_all(&self.pool)
        .await?;
        let mut health = HashMap::new();
        for row in rows {
            let source = parse_source(row.try_get::<String, _>("source")?.as_str())?;
            let last_success: Option<String> = row.try_get("last_success_at")?;
            let last_error_at: Option<String> = row.try_get("last_error_at")?;
            health.insert(
                source,
                ProviderHealth {
                    healthy: row.try_get("healthy")?,
                    last_success_at: last_success.as_deref().map(parse_time).transpose()?,
                    last_error_at: last_error_at.as_deref().map(parse_time).transpose()?,
                    last_error: row.try_get("last_error")?,
                },
            );
        }
        Ok(health)
    }
}

async fn merge_device_rows(
    transaction: &mut Transaction<'_, Sqlite>,
    keeper: &str,
    duplicate: &str,
) -> anyhow::Result<()> {
    sqlx::query("UPDATE devices SET first_seen_at = MIN(first_seen_at, (SELECT first_seen_at FROM devices WHERE id = ?)), last_seen_at = MAX(last_seen_at, (SELECT last_seen_at FROM devices WHERE id = ?)) WHERE id = ?")
        .bind(duplicate).bind(duplicate).bind(keeper).execute(&mut **transaction).await?;
    sqlx::query("INSERT OR IGNORE INTO device_addresses (device_id, kind, address) SELECT ?, kind, address FROM device_addresses WHERE device_id = ?")
        .bind(keeper).bind(duplicate).execute(&mut **transaction).await?;
    sqlx::query("INSERT INTO device_sources (device_id, source, source_id) SELECT ?, source, source_id FROM device_sources WHERE device_id = ? ON CONFLICT(device_id, source) DO UPDATE SET source_id = COALESCE(device_sources.source_id, excluded.source_id)")
        .bind(keeper).bind(duplicate).execute(&mut **transaction).await?;
    sqlx::query("INSERT INTO device_overrides (device_id, display_name, device_type, updated_at) SELECT ?, display_name, device_type, updated_at FROM device_overrides WHERE device_id = ? ON CONFLICT(device_id) DO UPDATE SET display_name = COALESCE(device_overrides.display_name, excluded.display_name), device_type = COALESCE(device_overrides.device_type, excluded.device_type), updated_at = MAX(device_overrides.updated_at, excluded.updated_at)")
        .bind(keeper).bind(duplicate).execute(&mut **transaction).await?;
    sqlx::query("INSERT OR IGNORE INTO device_names (device_id, name, source) SELECT ?, name, source FROM device_names WHERE device_id = ?")
        .bind(keeper).bind(duplicate).execute(&mut **transaction).await?;
    sqlx::query("INSERT OR IGNORE INTO device_port_scans (device_id, scanned_at, result_json) SELECT ?, scanned_at, result_json FROM device_port_scans WHERE device_id = ?")
        .bind(keeper).bind(duplicate).execute(&mut **transaction).await?;
    sqlx::query("DELETE FROM device_overrides WHERE device_id = ?")
        .bind(duplicate)
        .execute(&mut **transaction)
        .await?;
    for table in [
        "observations",
        "device_services",
        "device_events",
        "manual_identity_rules",
    ] {
        let query = format!("UPDATE {table} SET device_id = ? WHERE device_id = ?");
        sqlx::query(&query)
            .bind(keeper)
            .bind(duplicate)
            .execute(&mut **transaction)
            .await?;
    }
    sqlx::query("DELETE FROM devices WHERE id = ?")
        .bind(duplicate)
        .execute(&mut **transaction)
        .await?;
    Ok(())
}

async fn find_existing_id(
    transaction: &mut Transaction<'_, Sqlite>,
    merged: &MergedDevice,
) -> anyhow::Result<Option<DeviceId>> {
    for observation in &merged.observations {
        if let Some(source_id) = &observation.source_id {
            let id: Option<String> = sqlx::query_scalar(
                "SELECT device_id FROM device_sources WHERE source = ? AND source_id = ? LIMIT 1",
            )
            .bind(source_name(observation.source))
            .bind(source_id)
            .fetch_optional(&mut **transaction)
            .await?;
            if let Some(id) = id {
                return Ok(Some(DeviceId(Uuid::parse_str(&id)?)));
            }
        }
        if let Some(hostname) = &observation.hostname {
            for address in &observation.ip_addresses {
                let id: Option<String> = sqlx::query_scalar(
                    "SELECT host.device_id FROM device_addresses host JOIN device_addresses address ON address.device_id = host.device_id WHERE host.kind = 'hostname' AND lower(host.address) = lower(?) AND address.kind IN ('lan', 'netbird', 'netbird_ipv6') AND address.address = ? LIMIT 1",
                )
                .bind(hostname)
                .bind(address.to_string())
                .fetch_optional(&mut **transaction)
                .await?;
                if let Some(id) = id {
                    return Ok(Some(DeviceId(Uuid::parse_str(&id)?)));
                }
            }
        }
    }
    for mac in &merged.device.mac_addresses {
        let id: Option<String> = sqlx::query_scalar(
            "SELECT device_id FROM device_addresses WHERE kind = 'mac' AND address = ? LIMIT 1",
        )
        .bind(mac.to_string())
        .fetch_optional(&mut **transaction)
        .await?;
        if let Some(id) = id {
            return Ok(Some(DeviceId(Uuid::parse_str(&id)?)));
        }
    }
    // A named LAN observation may still adopt an unnamed device seen at the
    // same address (e.g. a liveness-only device that now has an mDNS name).
    for observation in &merged.observations {
        if observation.source == DiscoverySourceKind::Netbird
            || observation.hostname.is_none()
            || observation.mac_address.is_some()
        {
            continue;
        }
        for address in observation.ip_addresses.iter().filter(|address| address.is_ipv4()) {
            let id: Option<String> = sqlx::query_scalar(
                "SELECT a.device_id FROM device_addresses a JOIN devices d ON d.id = a.device_id WHERE a.kind = 'lan' AND a.address = ? AND NOT EXISTS (SELECT 1 FROM device_addresses h WHERE h.device_id = a.device_id AND h.kind IN ('hostname', 'mac')) ORDER BY d.first_seen_at, d.id LIMIT 1",
            )
            .bind(address.to_string())
            .fetch_optional(&mut **transaction)
            .await?;
            if let Some(id) = id {
                return Ok(Some(DeviceId(Uuid::parse_str(&id)?)));
            }
        }
    }
    for observation in &merged.observations {
        if observation.source_id.is_some()
            || observation.hostname.is_some()
            || observation.mac_address.is_some()
        {
            continue;
        }
        let kinds: &[&str] = if observation.source == DiscoverySourceKind::Netbird {
            &["netbird", "netbird_ipv6"]
        } else {
            &["lan"]
        };
        for address in &observation.ip_addresses {
            let id: Option<String> = sqlx::query_scalar(
                "SELECT a.device_id FROM device_addresses a JOIN devices d ON d.id = a.device_id WHERE a.kind IN (?, ?) AND a.address = ? ORDER BY d.first_seen_at, d.id LIMIT 1",
            )
            .bind(kinds[0])
            .bind(kinds.get(1).copied().unwrap_or(kinds[0]))
            .bind(address.to_string())
            .fetch_optional(&mut **transaction)
            .await?;
            if let Some(id) = id {
                return Ok(Some(DeviceId(Uuid::parse_str(&id)?)));
            }
        }
    }
    Ok(None)
}

async fn save_device(
    transaction: &mut Transaction<'_, Sqlite>,
    device: &Device,
) -> anyhow::Result<()> {
    let id = device.id.0.to_string();
    sqlx::query(
        "INSERT INTO devices (id, display_name, device_type, status, manufacturer, model, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name, device_type=excluded.device_type, status=excluded.status, manufacturer=excluded.manufacturer, model=excluded.model, first_seen_at=MIN(devices.first_seen_at, excluded.first_seen_at), last_seen_at=MAX(devices.last_seen_at, excluded.last_seen_at)",
    )
    .bind(&id)
    .bind(&device.display_name)
    .bind(device_type_name(device.device_type))
    .bind(status_name(device.status))
    .bind(&device.manufacturer)
    .bind(&device.model)
    .bind(device.first_seen_at.to_rfc3339())
    .bind(device.last_seen_at.to_rfc3339())
    .execute(&mut **transaction)
    .await?;

    sqlx::query("DELETE FROM device_addresses WHERE device_id = ? AND kind IN ('lan', 'netbird', 'netbird_ipv6')")
        .bind(&id)
        .execute(&mut **transaction)
        .await?;
    sqlx::query("DELETE FROM device_services WHERE device_id = ?")
        .bind(&id)
        .execute(&mut **transaction)
        .await?;

    for hostname in &device.hostnames {
        insert_address(transaction, &id, "hostname", hostname).await?;
    }
    for name in &device.names {
        sqlx::query("INSERT OR IGNORE INTO device_names (device_id, name, source) VALUES (?, ?, ?)")
            .bind(&id)
            .bind(&name.name)
            .bind(&name.source)
            .execute(&mut **transaction)
            .await?;
    }
    for address in &device.lan_ips {
        insert_address(transaction, &id, "lan", &address.to_string()).await?;
    }
    if let Some(address) = device.netbird_ip {
        insert_address(transaction, &id, "netbird", &address.to_string()).await?;
    }
    if let Some(address) = device.netbird_ipv6 {
        insert_address(transaction, &id, "netbird_ipv6", &address.to_string()).await?;
    }
    for mac in &device.mac_addresses {
        insert_address(transaction, &id, "mac", &mac.to_string()).await?;
    }
    for service in &device.services {
        sqlx::query("INSERT INTO device_services (device_id, service_type, name, protocol, port, hostname, txt_json) VALUES (?, ?, ?, ?, ?, ?, ?)")
            .bind(&id)
            .bind(&service.service_type)
            .bind(&service.name)
            .bind(protocol_name(service.protocol))
            .bind(i64::from(service.port))
            .bind(&service.hostname)
            .bind(serde_json::to_string(&service.txt)?)
            .execute(&mut **transaction)
            .await?;
    }
    for source in &device.sources {
        sqlx::query(
            "INSERT OR IGNORE INTO device_sources (device_id, source, source_id) VALUES (?, ?, NULL)",
        )
        .bind(&id)
        .bind(source_name(*source))
        .execute(&mut **transaction)
        .await?;
    }
    Ok(())
}

async fn insert_address(
    transaction: &mut Transaction<'_, Sqlite>,
    id: &str,
    kind: &str,
    address: &str,
) -> anyhow::Result<()> {
    sqlx::query(
        "INSERT OR IGNORE INTO device_addresses (device_id, kind, address) VALUES (?, ?, ?)",
    )
    .bind(id)
    .bind(kind)
    .bind(address)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn save_observations(
    transaction: &mut Transaction<'_, Sqlite>,
    merged: &MergedDevice,
) -> anyhow::Result<()> {
    let id = merged.device.id.0.to_string();
    for observation in &merged.observations {
        sqlx::query("INSERT INTO observations (device_id, source, source_id, payload_json, observed_at) VALUES (?, ?, ?, ?, ?)")
            .bind(&id)
            .bind(source_name(observation.source))
            .bind(&observation.source_id)
            .bind(serde_json::to_string(observation)?)
            .bind(observation.observed_at.to_rfc3339())
            .execute(&mut **transaction)
            .await?;
        sqlx::query("INSERT INTO device_sources (device_id, source, source_id) VALUES (?, ?, ?) ON CONFLICT(device_id, source) DO UPDATE SET source_id=COALESCE(excluded.source_id, device_sources.source_id)")
            .bind(&id)
            .bind(source_name(observation.source))
            .bind(&observation.source_id)
            .execute(&mut **transaction)
            .await?;
    }
    Ok(())
}

async fn load_addresses(pool: &SqlitePool, id: &str, device: &mut Device) -> anyhow::Result<()> {
    let rows = sqlx::query(
        "SELECT kind, address FROM device_addresses WHERE device_id = ? ORDER BY kind, address",
    )
    .bind(id)
    .fetch_all(pool)
    .await?;
    for row in rows {
        let kind: String = row.try_get("kind")?;
        let value: String = row.try_get("address")?;
        match kind.as_str() {
            "hostname" => device.hostnames.push(value),
            "lan" => device.lan_ips.push(value.parse()?),
            "netbird" => device.netbird_ip = Some(value.parse()?),
            "netbird_ipv6" => device.netbird_ipv6 = Some(value.parse()?),
            "mac" => device
                .mac_addresses
                .push(MacAddress::from_str(&value).map_err(anyhow::Error::msg)?),
            _ => {}
        }
    }
    Ok(())
}

async fn load_names(pool: &SqlitePool, id: &str, device: &mut Device) -> anyhow::Result<()> {
    let rows: Vec<(String, String)> = sqlx::query_as(
        "SELECT name, source FROM device_names WHERE device_id = ? ORDER BY CASE source WHEN 'mdns' THEN 0 WHEN 'netbios' THEN 1 WHEN 'ssdp' THEN 2 WHEN 'netbird' THEN 3 WHEN 'ptr' THEN 4 ELSE 5 END, name",
    )
    .bind(id)
    .fetch_all(pool)
    .await?;
    device.names = rows
        .into_iter()
        .map(|(name, source)| DeviceName { name, source })
        .collect();
    Ok(())
}

async fn load_sources(pool: &SqlitePool, id: &str, device: &mut Device) -> anyhow::Result<()> {
    let sources: Vec<String> =
        sqlx::query_scalar("SELECT source FROM device_sources WHERE device_id = ? ORDER BY source")
            .bind(id)
            .fetch_all(pool)
            .await?;
    device.sources = sources
        .iter()
        .map(|source| parse_source(source))
        .collect::<anyhow::Result<_>>()?;
    Ok(())
}

async fn load_services(pool: &SqlitePool, id: &str, device: &mut Device) -> anyhow::Result<()> {
    let rows = sqlx::query("SELECT service_type, name, protocol, port, hostname, txt_json FROM device_services WHERE device_id = ? ORDER BY service_type, port")
        .bind(id)
        .fetch_all(pool)
        .await?;
    for row in rows {
        let protocol: String = row.try_get("protocol")?;
        let port: i64 = row.try_get("port")?;
        device.services.push(Service {
            service_type: row.try_get("service_type")?,
            name: row.try_get("name")?,
            protocol: if protocol == "udp" {
                TransportProtocol::Udp
            } else {
                TransportProtocol::Tcp
            },
            port: u16::try_from(port)?,
            hostname: row.try_get("hostname")?,
            txt: serde_json::from_str(&row.try_get::<String, _>("txt_json")?)?,
        });
    }
    Ok(())
}

fn source_name(source: DiscoverySourceKind) -> &'static str {
    match source {
        DiscoverySourceKind::Netbird => "netbird",
        DiscoverySourceKind::Neighbor => "neighbor",
        DiscoverySourceKind::Mdns => "mdns",
    }
}

fn parse_source(value: &str) -> anyhow::Result<DiscoverySourceKind> {
    match value {
        "netbird" => Ok(DiscoverySourceKind::Netbird),
        "neighbor" => Ok(DiscoverySourceKind::Neighbor),
        "mdns" => Ok(DiscoverySourceKind::Mdns),
        _ => anyhow::bail!("unknown source {value}"),
    }
}

fn status_name(status: DeviceStatus) -> &'static str {
    match status {
        DeviceStatus::Online => "online",
        DeviceStatus::Idle => "idle",
        DeviceStatus::Connecting => "connecting",
        DeviceStatus::Offline => "offline",
        DeviceStatus::Unknown => "unknown",
    }
}

fn parse_status(value: &str) -> DeviceStatus {
    match value {
        "online" => DeviceStatus::Online,
        "idle" => DeviceStatus::Idle,
        "connecting" => DeviceStatus::Connecting,
        "offline" => DeviceStatus::Offline,
        _ => DeviceStatus::Unknown,
    }
}

fn device_type_name(value: DeviceType) -> &'static str {
    match value {
        DeviceType::Server => "server",
        DeviceType::Computer => "computer",
        DeviceType::Phone => "phone",
        DeviceType::Tablet => "tablet",
        DeviceType::Television => "television",
        DeviceType::Printer => "printer",
        DeviceType::Network => "network",
        DeviceType::Iot => "iot",
        DeviceType::Unknown => "unknown",
    }
}

fn parse_device_type(value: &str) -> DeviceType {
    match value {
        "server" => DeviceType::Server,
        "computer" => DeviceType::Computer,
        "phone" => DeviceType::Phone,
        "tablet" => DeviceType::Tablet,
        "television" => DeviceType::Television,
        "printer" => DeviceType::Printer,
        "network" => DeviceType::Network,
        "iot" => DeviceType::Iot,
        _ => DeviceType::Unknown,
    }
}

fn protocol_name(value: TransportProtocol) -> &'static str {
    match value {
        TransportProtocol::Tcp => "tcp",
        TransportProtocol::Udp => "udp",
    }
}

fn parse_time(value: &str) -> anyhow::Result<DateTime<Utc>> {
    Ok(DateTime::parse_from_rfc3339(value)?.with_timezone(&Utc))
}
