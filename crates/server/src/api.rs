use crate::{
    discovery::ProviderHealth,
    events::{serve_socket, EventBus},
    repository::{DeviceEventRecord, Repository, ScanRecord},
    scan::{ScanService, StartError},
};
use axum::{
    extract::{Path, Query, State, WebSocketUpgrade},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use inet_viewer_shared::{Device, DeviceStatus, DeviceType, DiscoverySourceKind};
use serde::{Deserialize, Serialize};
use std::cmp::Ordering;

#[derive(Clone)]
pub struct ApiState {
    pub repository: Repository,
    pub events: EventBus,
    pub scans: ScanService,
}

#[derive(Debug, Serialize)]
pub struct StatusResponse {
    pub status: &'static str,
    pub total_devices: usize,
    pub online_devices: usize,
    pub offline_devices: usize,
    pub netbird_devices: usize,
    pub lan_only_devices: usize,
    /// Devices found (also) over mDNS; a device can count in several sources.
    pub mdns_devices: usize,
    pub unhealthy_sources: usize,
}

#[derive(Debug, Serialize)]
pub struct DevicesResponse {
    pub devices: Vec<Device>,
    pub total: usize,
}

#[derive(Debug, Serialize)]
pub struct SourceResponse {
    pub source: DiscoverySourceKind,
    pub healthy: bool,
    pub last_success_at: Option<chrono::DateTime<chrono::Utc>>,
    pub last_error_at: Option<chrono::DateTime<chrono::Utc>>,
    pub last_error: Option<String>,
}

#[derive(Debug, Default, Deserialize)]
pub struct DeviceQuery {
    pub q: Option<String>,
    pub status: Option<String>,
    pub source: Option<String>,
    #[serde(rename = "type")]
    pub device_type: Option<String>,
    pub network: Option<String>,
    pub sort: Option<String>,
    pub order: Option<String>,
}

#[derive(Debug, Serialize)]
struct ErrorResponse {
    error: String,
}

pub fn router(repository: Repository, events: EventBus, scans: ScanService) -> Router {
    Router::new()
        .route("/api/v1/status", get(status))
        .route("/api/v1/sources", get(sources))
        .route("/api/v1/devices", get(devices))
        .route("/api/v1/devices/{id}", get(device).patch(update_device))
        .route("/api/v1/devices/{id}/events", get(device_events))
        .route("/api/v1/scans", post(start_scan))
        .route("/api/v1/scans/{id}", get(scan))
        .route("/api/v1/events/ws", get(events_ws))
        .with_state(ApiState {
            repository,
            events,
            scans,
        })
}

#[derive(Debug, Serialize)]
struct ScanQueuedResponse {
    scan_id: String,
    status: &'static str,
}

async fn start_scan(
    State(state): State<ApiState>,
) -> Result<(StatusCode, Json<ScanQueuedResponse>), ApiError> {
    match state.scans.trigger().await {
        Ok(scan_id) => Ok((
            StatusCode::ACCEPTED,
            Json(ScanQueuedResponse {
                scan_id,
                status: "queued",
            }),
        )),
        Err(StartError::AlreadyRunning) => Err(ApiError::conflict("a scan is already running")),
        Err(StartError::RateLimited) => Err(ApiError::too_many_requests(
            "manual scan rate limit exceeded",
        )),
    }
}

async fn scan(
    State(state): State<ApiState>,
    Path(id): Path<String>,
) -> Result<Json<ScanRecord>, ApiError> {
    state
        .scans
        .get(&id)
        .await?
        .map(Json)
        .ok_or_else(|| ApiError::not_found("scan not found"))
}

async fn device_events(
    State(state): State<ApiState>,
    Path(id): Path<String>,
    Query(query): Query<DeviceEventsQuery>,
) -> Result<Json<Vec<DeviceEventRecord>>, ApiError> {
    if state.repository.get_device(&id).await?.is_none() {
        return Err(ApiError::not_found("device not found"));
    }
    let limit = match query.limit.as_deref() {
        None => 15,
        Some(value) => value
            .parse::<u32>()
            .ok()
            .filter(|limit| (1..=100).contains(limit))
            .ok_or_else(|| ApiError::bad_request("limit must be an integer between 1 and 100"))?,
    };
    Ok(Json(state.repository.list_device_events(&id, limit).await?))
}

#[derive(Debug, Default, Deserialize)]
struct DeviceEventsQuery {
    limit: Option<String>,
}

async fn events_ws(State(state): State<ApiState>, ws: WebSocketUpgrade) -> impl IntoResponse {
    ws.on_upgrade(move |socket| serve_socket(socket, state.events))
}

async fn status(State(state): State<ApiState>) -> Result<Json<StatusResponse>, ApiError> {
    let devices = state.repository.list_devices().await?;
    let health = state.repository.load_health().await?;
    Ok(Json(StatusResponse {
        status: if health.values().any(|item| !item.healthy) {
            "degraded"
        } else {
            "ok"
        },
        total_devices: devices.len(),
        online_devices: devices
            .iter()
            .filter(|device| device.status == DeviceStatus::Online)
            .count(),
        offline_devices: devices
            .iter()
            .filter(|device| device.status == DeviceStatus::Offline)
            .count(),
        netbird_devices: devices
            .iter()
            .filter(|device| device.sources.contains(&DiscoverySourceKind::Netbird))
            .count(),
        lan_only_devices: devices
            .iter()
            .filter(|device| {
                !device.sources.contains(&DiscoverySourceKind::Netbird)
                    && (!device.lan_ips.is_empty()
                        || device.sources.contains(&DiscoverySourceKind::Neighbor)
                        || device.sources.contains(&DiscoverySourceKind::Mdns))
            })
            .count(),
        mdns_devices: devices
            .iter()
            .filter(|device| device.sources.contains(&DiscoverySourceKind::Mdns))
            .count(),
        unhealthy_sources: health.values().filter(|item| !item.healthy).count(),
    }))
}

async fn sources(State(state): State<ApiState>) -> Result<Json<Vec<SourceResponse>>, ApiError> {
    let health = state.repository.load_health().await?;
    let mut result = [
        DiscoverySourceKind::Netbird,
        DiscoverySourceKind::Neighbor,
        DiscoverySourceKind::Mdns,
    ]
    .into_iter()
    .map(|source| source_response(source, health.get(&source)))
    .collect::<Vec<_>>();
    result.sort_by_key(|item| source_name(item.source));
    Ok(Json(result))
}

fn source_response(source: DiscoverySourceKind, health: Option<&ProviderHealth>) -> SourceResponse {
    SourceResponse {
        source,
        healthy: health.is_some_and(|item| item.healthy),
        last_success_at: health.and_then(|item| item.last_success_at),
        last_error_at: health.and_then(|item| item.last_error_at),
        last_error: health.and_then(|item| item.last_error.clone()),
    }
}

async fn devices(
    State(state): State<ApiState>,
    Query(query): Query<DeviceQuery>,
) -> Result<Json<DevicesResponse>, ApiError> {
    validate_query(&query)?;
    let mut devices = state.repository.list_devices().await?;
    devices.retain(|device| matches_query(device, &query));
    sort_devices(&mut devices, &query);
    let total = devices.len();
    Ok(Json(DevicesResponse { devices, total }))
}

async fn device(
    State(state): State<ApiState>,
    Path(id): Path<String>,
) -> Result<Json<Device>, ApiError> {
    state
        .repository
        .get_device(&id)
        .await?
        .map(Json)
        .ok_or_else(|| ApiError::not_found("device not found"))
}

#[derive(Debug, Deserialize)]
struct DevicePatch {
    display_name: Option<String>,
    device_type: Option<String>,
}

async fn update_device(
    State(state): State<ApiState>,
    Path(id): Path<String>,
    Json(patch): Json<DevicePatch>,
) -> Result<Json<Device>, ApiError> {
    if patch.display_name.is_none() && patch.device_type.is_none() {
        return Err(ApiError::bad_request(
            "at least one editable field is required",
        ));
    }
    let display_name = patch.display_name.as_deref().map(str::trim);
    if display_name.is_some_and(|name| name.chars().count() > 128) {
        return Err(ApiError::bad_request(
            "display_name must be at most 128 characters",
        ));
    }
    let display_name = display_name.map(|name| (!name.is_empty()).then_some(name));
    let device_type = patch
        .device_type
        .as_deref()
        .map(|value| {
            parse_device_type(value).ok_or_else(|| ApiError::bad_request("unknown device type"))
        })
        .transpose()?;
    state
        .repository
        .update_device_override(&id, display_name, device_type)
        .await?
        .map(Json)
        .ok_or_else(|| ApiError::not_found("device not found"))
}

fn validate_query(query: &DeviceQuery) -> Result<(), ApiError> {
    if query
        .status
        .as_deref()
        .is_some_and(|value| parse_status(value).is_none())
    {
        return Err(ApiError::bad_request("unknown status filter"));
    }
    if query
        .source
        .as_deref()
        .is_some_and(|value| parse_source(value).is_none())
    {
        return Err(ApiError::bad_request("unknown source filter"));
    }
    if query
        .device_type
        .as_deref()
        .is_some_and(|value| parse_device_type(value).is_none())
    {
        return Err(ApiError::bad_request("unknown device type filter"));
    }
    if query
        .network
        .as_deref()
        .is_some_and(|value| !matches!(value, "all" | "lan" | "netbird" | "lan_only"))
    {
        return Err(ApiError::bad_request("unknown network filter"));
    }
    if query
        .sort
        .as_deref()
        .is_some_and(|value| !matches!(value, "name" | "status" | "type" | "last_seen"))
    {
        return Err(ApiError::bad_request("unknown sort field"));
    }
    if query
        .order
        .as_deref()
        .is_some_and(|value| !matches!(value, "asc" | "desc"))
    {
        return Err(ApiError::bad_request("unknown sort order"));
    }
    Ok(())
}

fn matches_query(device: &Device, query: &DeviceQuery) -> bool {
    if let Some(search) = query
        .q
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        let search = search.to_lowercase();
        let searchable = std::iter::once(device.display_name.as_str())
            .chain(device.hostnames.iter().map(String::as_str))
            .chain(
                device
                    .lan_ips
                    .iter()
                    .map(|address| address.to_string())
                    .collect::<Vec<_>>()
                    .iter()
                    .map(String::as_str),
            )
            .chain(
                device
                    .netbird_ip
                    .iter()
                    .map(|address| address.to_string())
                    .collect::<Vec<_>>()
                    .iter()
                    .map(String::as_str),
            )
            .chain(device.manufacturer.iter().map(String::as_str))
            .chain(device.model.iter().map(String::as_str))
            .any(|value| value.to_lowercase().contains(&search));
        if !searchable {
            return false;
        }
    }
    if query
        .status
        .as_deref()
        .and_then(parse_status)
        .is_some_and(|status| device.status != status)
    {
        return false;
    }
    if query
        .source
        .as_deref()
        .and_then(parse_source)
        .is_some_and(|source| !device.sources.contains(&source))
    {
        return false;
    }
    if query
        .device_type
        .as_deref()
        .and_then(parse_device_type)
        .is_some_and(|kind| device.device_type != kind)
    {
        return false;
    }
    match query.network.as_deref() {
        Some("lan") => !device.lan_ips.is_empty(),
        Some("netbird") => device.sources.contains(&DiscoverySourceKind::Netbird),
        Some("lan_only") => {
            !device.sources.contains(&DiscoverySourceKind::Netbird) && !device.lan_ips.is_empty()
        }
        _ => true,
    }
}

fn sort_devices(devices: &mut [Device], query: &DeviceQuery) {
    let descending = query.order.as_deref() == Some("desc");
    devices.sort_by(|left, right| {
        let ordering = match query.sort.as_deref().unwrap_or("name") {
            "status" => left.status.cmp(&right.status),
            "type" => type_rank(left.device_type).cmp(&type_rank(right.device_type)),
            "last_seen" => left.last_seen_at.cmp(&right.last_seen_at),
            _ => left
                .display_name
                .to_lowercase()
                .cmp(&right.display_name.to_lowercase()),
        };
        let ordering = if ordering == Ordering::Equal {
            left.id.0.cmp(&right.id.0)
        } else {
            ordering
        };
        if descending {
            ordering.reverse()
        } else {
            ordering
        }
    });
}

fn parse_status(value: &str) -> Option<DeviceStatus> {
    match value {
        "online" => Some(DeviceStatus::Online),
        "idle" => Some(DeviceStatus::Idle),
        "connecting" => Some(DeviceStatus::Connecting),
        "offline" => Some(DeviceStatus::Offline),
        "unknown" => Some(DeviceStatus::Unknown),
        _ => None,
    }
}

fn parse_source(value: &str) -> Option<DiscoverySourceKind> {
    match value {
        "netbird" => Some(DiscoverySourceKind::Netbird),
        "neighbor" => Some(DiscoverySourceKind::Neighbor),
        "mdns" => Some(DiscoverySourceKind::Mdns),
        _ => None,
    }
}

fn parse_device_type(value: &str) -> Option<DeviceType> {
    match value {
        "server" => Some(DeviceType::Server),
        "computer" => Some(DeviceType::Computer),
        "phone" => Some(DeviceType::Phone),
        "tablet" => Some(DeviceType::Tablet),
        "television" => Some(DeviceType::Television),
        "printer" => Some(DeviceType::Printer),
        "network" => Some(DeviceType::Network),
        "iot" => Some(DeviceType::Iot),
        "unknown" => Some(DeviceType::Unknown),
        _ => None,
    }
}

fn type_rank(value: DeviceType) -> u8 {
    match value {
        DeviceType::Server => 0,
        DeviceType::Computer => 1,
        DeviceType::Phone => 2,
        DeviceType::Tablet => 3,
        DeviceType::Television => 4,
        DeviceType::Printer => 5,
        DeviceType::Network => 6,
        DeviceType::Iot => 7,
        DeviceType::Unknown => 8,
    }
}

fn source_name(source: DiscoverySourceKind) -> &'static str {
    match source {
        DiscoverySourceKind::Mdns => "mdns",
        DiscoverySourceKind::Neighbor => "neighbor",
        DiscoverySourceKind::Netbird => "netbird",
    }
}

#[derive(Debug)]
struct ApiError {
    status: StatusCode,
    message: String,
}

impl ApiError {
    fn bad_request(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::BAD_REQUEST,
            message: message.into(),
        }
    }

    fn not_found(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::NOT_FOUND,
            message: message.into(),
        }
    }

    fn conflict(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::CONFLICT,
            message: message.into(),
        }
    }

    fn too_many_requests(message: impl Into<String>) -> Self {
        Self {
            status: StatusCode::TOO_MANY_REQUESTS,
            message: message.into(),
        }
    }
}

impl From<anyhow::Error> for ApiError {
    fn from(error: anyhow::Error) -> Self {
        tracing::error!(%error, "API repository operation failed");
        Self {
            status: StatusCode::INTERNAL_SERVER_ERROR,
            message: "internal server error".into(),
        }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (
            self.status,
            Json(ErrorResponse {
                error: self.message,
            }),
        )
            .into_response()
    }
}
