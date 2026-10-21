use futures_util::StreamExt;
use gloo_net::http::Request;
use gloo_net::websocket::{futures::WebSocket, Message};
use gloo_timers::future::TimeoutFuture;
use inet_viewer_shared::{Device, DeviceName, DeviceStatus, DeviceType, DiscoverySourceKind, Event, PortScan};
use leptos::prelude::*;
use serde::Deserialize;
use std::cmp::Ordering;
use wasm_bindgen::prelude::*;
use wasm_bindgen_futures::spawn_local;

/// Removed theme preference; cleared once at startup.
const LEGACY_THEME_STORAGE_KEY: &str = "inet-viewer-theme";

fn is_desktop() -> bool {
    desktop_runtime()
}

#[wasm_bindgen(
    inline_js = "export function isDesktopRuntime() { return !!window.__TAURI_INTERNALS__; } export function enableGlass() { if (window.__TAURI_INTERNALS__ && /Mac/.test(navigator.userAgent)) document.documentElement.classList.add('glass'); } export async function desktopInvoke(command, args) { return await window.__TAURI_INTERNALS__.invoke(command, args || {}); } export async function copyText(value) { await navigator.clipboard.writeText(value); } export async function listenEvent(name, callback) { const tauri = window.__TAURI_INTERNALS__; const handler = tauri.transformCallback((event) => callback(event.payload)); return await tauri.invoke('plugin:event|listen', { event: name, target: { kind: 'Any' }, handler }); }"
)]
extern "C" {
    #[wasm_bindgen(js_name = isDesktopRuntime)]
    fn desktop_runtime() -> bool;

    /// macOS desktop: transparent window with vibrancy, so the page is see-through.
    #[wasm_bindgen(js_name = enableGlass)]
    fn enable_glass();

    #[wasm_bindgen(catch, js_name = desktopInvoke)]
    async fn desktop_invoke(command: &str, args: JsValue) -> Result<JsValue, JsValue>;

    #[wasm_bindgen(catch, js_name = copyText)]
    async fn copy_text(value: &str) -> Result<JsValue, JsValue>;

    #[wasm_bindgen(catch, js_name = listenEvent)]
    async fn listen_event(name: &str, callback: &js_sys::Function) -> Result<JsValue, JsValue>;
}

async fn invoke<T: serde::de::DeserializeOwned>(
    command: &str,
    args: serde_json::Value,
) -> Result<T, String> {
    let args = serde_wasm_bindgen::to_value(&args).map_err(|error| error.to_string())?;
    let value = desktop_invoke(command, args)
        .await
        .map_err(|error| error.as_string().unwrap_or_else(|| format!("{error:?}")))?;
    serde_wasm_bindgen::from_value(value).map_err(|error| error.to_string())
}

fn rest(path: &str) -> String {
    format!("/{}", path.trim_start_matches('/'))
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
enum KpiFilter {
    #[default]
    All,
    Online,
    Offline,
    Netbird,
    LanOnly,
    Mdns,
}

impl KpiFilter {
    const fn criteria(self) -> (&'static str, &'static str) {
        match self {
            Self::All => ("", ""),
            Self::Online => ("online", ""),
            Self::Offline => ("offline", ""),
            Self::Netbird => ("", "netbird"),
            Self::LanOnly => ("", "lan_only"),
            Self::Mdns => ("", "mdns"),
        }
    }
}

/// Same rule as the server's `lan_only_devices` count.
fn is_lan_only(device: &Device) -> bool {
    !device.sources.contains(&DiscoverySourceKind::Netbird)
        && (!device.lan_ips.is_empty()
            || device.sources.contains(&DiscoverySourceKind::Neighbor)
            || device.sources.contains(&DiscoverySourceKind::Mdns))
}

/// KPI network filter: "contains this source" (a device can be in several).
fn matches_network(device: &Device, network: &str) -> bool {
    match network {
        "netbird" => device.sources.contains(&DiscoverySourceKind::Netbird),
        "mdns" => device.sources.contains(&DiscoverySourceKind::Mdns),
        "lan_only" => is_lan_only(device),
        _ => true,
    }
}

/// Progress of the running discovery scan as shown under the toolbar.
#[derive(Clone, Debug, Default, PartialEq)]
struct ScanProgressView {
    scan_id: String,
    percent: f64,
    phase: String,
    error: Option<String>,
}

#[derive(Clone, Debug, Default, Deserialize)]
struct ScanProgressEvent {
    #[serde(default)]
    kind: String,
    #[serde(default)]
    scan_id: String,
    #[serde(default)]
    phase: String,
    #[serde(default)]
    percent: f64,
    #[serde(default)]
    error: Option<String>,
}

/// Next progress state for an incoming event; `None` hides the bar.
fn next_progress(current: Option<ScanProgressView>, event: ScanProgressEvent) -> Option<ScanProgressView> {
    match event.kind.as_str() {
        "started" | "progress" => Some(ScanProgressView {
            scan_id: event.scan_id,
            percent: event.percent.clamp(0.0, 100.0),
            phase: if event.phase.is_empty() { "Başlıyor".into() } else { event.phase },
            error: None,
        }),
        "failed" => Some(ScanProgressView {
            scan_id: event.scan_id,
            percent: current.map(|item| item.percent).unwrap_or_default(),
            phase: String::new(),
            error: Some(event.error.unwrap_or_else(|| "Tarama başarısız".into())),
        }),
        "completed" => None,
        _ => current,
    }
}

const DIAGNOSTICS_OPEN_KEY: &str = "inet-viewer-diagnostics-open";

fn local_storage() -> Option<web_sys::Storage> {
    web_sys::window().and_then(|window| window.local_storage().ok().flatten())
}

fn stored_diagnostics_open() -> bool {
    local_storage()
        .and_then(|storage| storage.get_item(DIAGNOSTICS_OPEN_KEY).ok().flatten())
        .as_deref()
        == Some("1")
}

fn store_diagnostics_open(open: bool) {
    if let Some(storage) = local_storage() {
        let _ = storage.set_item(DIAGNOSTICS_OPEN_KEY, if open { "1" } else { "0" });
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SortColumn {
    Status,
    Name,
    Type,
    LanIp,
    NetbirdIp,
    Services,
    LastSeen,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SortDirection {
    Asc,
    Desc,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct SortState {
    column: SortColumn,
    direction: SortDirection,
}

impl SortState {
    const fn new(column: SortColumn, direction: SortDirection) -> Self {
        Self { column, direction }
    }
    fn aria_sort(self, column: SortColumn) -> &'static str {
        if self.column != column {
            "none"
        } else {
            match self.direction {
                SortDirection::Asc => "ascending",
                SortDirection::Desc => "descending",
            }
        }
    }
    fn icon(self, column: SortColumn) -> &'static str {
        if self.column != column {
            ""
        } else {
            match self.direction {
                SortDirection::Asc => "↑",
                SortDirection::Desc => "↓",
            }
        }
    }
}

fn default_direction(column: SortColumn) -> SortDirection {
    match column {
        SortColumn::Status | SortColumn::Services | SortColumn::LastSeen => SortDirection::Desc,
        _ => SortDirection::Asc,
    }
}

fn next_sort(current: Option<SortState>, column: SortColumn) -> SortState {
    match current {
        Some(state) if state.column == column => SortState::new(
            column,
            match state.direction {
                SortDirection::Asc => SortDirection::Desc,
                SortDirection::Desc => SortDirection::Asc,
            },
        ),
        _ => SortState::new(column, default_direction(column)),
    }
}

fn compare_devices(left: &Device, right: &Device, sort: SortState) -> Ordering {
    let ordering = match sort.column {
        SortColumn::Status => left.status.cmp(&right.status),
        SortColumn::Name => left
            .display_name
            .to_lowercase()
            .cmp(&right.display_name.to_lowercase()),
        SortColumn::Type => type_label(left.device_type).cmp(type_label(right.device_type)),
        SortColumn::LanIp => left.lan_ips.first().cmp(&right.lan_ips.first()),
        SortColumn::NetbirdIp => left.netbird_ip.cmp(&right.netbird_ip),
        SortColumn::Services => left.services.len().cmp(&right.services.len()),
        SortColumn::LastSeen => left.last_seen_at.cmp(&right.last_seen_at),
    }
    .then_with(|| {
        left.display_name
            .to_lowercase()
            .cmp(&right.display_name.to_lowercase())
    });
    match sort.direction {
        SortDirection::Asc => ordering,
        SortDirection::Desc => ordering.reverse(),
    }
}

#[derive(Clone, Debug, Deserialize)]
struct StatusSummary {
    total_devices: usize,
    online_devices: usize,
    offline_devices: usize,
    netbird_devices: usize,
    lan_only_devices: usize,
    #[serde(default)]
    mdns_devices: usize,
    unhealthy_sources: usize,
}

#[derive(Clone, Debug, Deserialize)]
struct DevicesResponse {
    devices: Vec<Device>,
}

#[derive(Clone, Debug, Deserialize)]
struct SourceHealth {
    source: DiscoverySourceKind,
    healthy: bool,
    last_success_at: Option<chrono::DateTime<chrono::Utc>>,
    last_error: Option<String>,
    #[serde(default)]
    interface: Option<String>,
    #[serde(default)]
    cidr: Option<String>,
    #[serde(default)]
    observation_count: usize,
    #[serde(default)]
    detail: Option<String>,
}

fn diagnostic_text(sources: &[SourceHealth]) -> String {
    sources
        .iter()
        .map(|item| {
            format!(
                "{} | durum={} | son_başarı={} | hata={} | arayüz={} | cidr={} | gözlem={} | ayrıntı={}",
                source_label(item.source),
                if item.healthy { "ok" } else { "hata" },
                item.last_success_at
                    .map(|value| value.to_rfc3339())
                    .unwrap_or_else(|| "yok".into()),
                item.last_error.as_deref().unwrap_or("yok"),
                item.interface.as_deref().unwrap_or("—"),
                item.cidr.as_deref().unwrap_or("—"),
                item.observation_count,
                item.detail.as_deref().unwrap_or("—"),
            )
        })
        .collect::<Vec<_>>()
        .join("\n")
}

#[derive(Clone, Debug, Deserialize)]
struct DeviceEventRecord {
    event_type: String,
    occurred_at: chrono::DateTime<chrono::Utc>,
}

#[derive(Clone, Debug, Deserialize)]
struct ScanQueuedResponse {}

#[derive(Clone, Debug, Deserialize)]
struct LocalNetworkCheck {
    authorized: bool,
    status: String,
    code: String,
    message: String,
    interface: String,
    private_ip: String,
    cidr: String,
    gateway: String,
    native_probe: String,
    #[serde(default)]
    unicast_probe: String,
    lan_probe: String,
}

impl LocalNetworkCheck {
    fn diagnostics(&self) -> String {
        format!(
            "durum={} | kod={} | arayüz={} | private_ip={} | cidr={} | gateway={} | native_probe={} | unicast_probe={} | lan_probe={}",
            self.status, self.code, self.interface, self.private_ip, self.cidr, self.gateway,
            self.native_probe, self.unicast_probe, self.lan_probe
        )
    }
}

async fn start_scan() -> Result<ScanQueuedResponse, String> {
    if is_desktop() {
        return invoke("desktop_start_scan", serde_json::json!({})).await;
    }
    let response = Request::post(&rest("/api/v1/scans"))
        .send()
        .await
        .map_err(|error| error.to_string())?;
    if response.ok() {
        response.json().await.map_err(|error| error.to_string())
    } else {
        Err(format!("Tarama başlatılamadı ({})", response.status()))
    }
}

fn onboarding_accepted() -> bool {
    !is_desktop()
        || web_sys::window()
            .and_then(|window| window.local_storage().ok().flatten())
            .and_then(|storage| {
                storage
                    .get_item("inet-viewer-local-network-onboarding")
                    .ok()
                    .flatten()
            })
            .as_deref()
            == Some("accepted")
}

async fn load_summary() -> Result<StatusSummary, String> {
    if is_desktop() {
        return invoke("desktop_status", serde_json::json!({})).await;
    }
    Request::get(&rest("/api/v1/status"))
        .send()
        .await
        .map_err(|error| error.to_string())?
        .json::<StatusSummary>()
        .await
        .map_err(|error| error.to_string())
}

async fn load_devices() -> Result<Vec<Device>, String> {
    if is_desktop() {
        return invoke::<DevicesResponse>("desktop_devices", serde_json::json!({}))
            .await
            .map(|response| response.devices);
    }
    Request::get(&rest("/api/v1/devices?sort=name"))
        .send()
        .await
        .map_err(|error| error.to_string())?
        .json::<DevicesResponse>()
        .await
        .map(|response| response.devices)
        .map_err(|error| error.to_string())
}

async fn load_sources() -> Result<Vec<SourceHealth>, String> {
    if is_desktop() {
        return invoke("desktop_sources", serde_json::json!({})).await;
    }
    Request::get(&rest("/api/v1/sources"))
        .send()
        .await
        .map_err(|error| error.to_string())?
        .json::<Vec<SourceHealth>>()
        .await
        .map_err(|error| error.to_string())
}

async fn load_device(id: &str) -> Result<Device, String> {
    if is_desktop() {
        return invoke("desktop_device", serde_json::json!({"id": id})).await;
    }
    Request::get(&rest(&format!("/api/v1/devices/{id}")))
        .send()
        .await
        .map_err(|error| error.to_string())?
        .json::<Device>()
        .await
        .map_err(|error| error.to_string())
}

async fn save_device_override(
    id: &str,
    display_name: &str,
    device_type: &str,
) -> Result<Device, String> {
    if is_desktop() {
        return invoke(
            "desktop_update_device",
            serde_json::json!({
                "id": id,
                "patch": {"display_name": display_name, "device_type": device_type}
            }),
        )
        .await;
    }
    let response = Request::patch(&rest(&format!("/api/v1/devices/{id}")))
        .header("content-type", "application/json")
        .body(
            serde_json::json!({"display_name": display_name, "device_type": device_type})
                .to_string(),
        )
        .map_err(|error| error.to_string())?
        .send()
        .await
        .map_err(|error| error.to_string())?;
    if response.ok() {
        response
            .json::<Device>()
            .await
            .map_err(|error| error.to_string())
    } else {
        let status = response.status();
        Err(response
            .json::<serde_json::Value>()
            .await
            .ok()
            .and_then(|body| body["error"].as_str().map(str::to_owned))
            .unwrap_or_else(|| format!("Cihaz kaydedilemedi ({status})")))
    }
}

fn event_device_id(event: &Event) -> Option<String> {
    event
        .data
        .get("device_id")
        .and_then(|value| value.as_str())
        .map(str::to_owned)
}

fn apply_device_update(devices: &mut Vec<Device>, updated: Device) {
    if let Some(existing) = devices.iter_mut().find(|device| device.id == updated.id) {
        *existing = updated;
    } else {
        devices.push(updated);
    }
}

fn should_reset_detail(previous: Option<uuid::Uuid>, next: Option<uuid::Uuid>) -> bool {
    previous != next
}

async fn refresh_dashboard(
    set_summary: WriteSignal<Option<StatusSummary>>,
    set_devices: WriteSignal<Vec<Device>>,
    set_sources: WriteSignal<Vec<SourceHealth>>,
    set_error: WriteSignal<Option<String>>,
    set_loading: WriteSignal<bool>,
) {
    set_loading.set(true);
    match futures_util::try_join!(load_summary(), load_devices(), load_sources()) {
        Ok((summary, devices, sources)) => {
            set_summary.set(Some(summary));
            set_devices.set(devices);
            set_sources.set(sources);
            set_error.set(None);
        }
        Err(error) => set_error.set(Some(error)),
    }
    set_loading.set(false);
}

async fn refresh_overview(
    set_summary: WriteSignal<Option<StatusSummary>>,
    set_sources: WriteSignal<Vec<SourceHealth>>,
) {
    if let Ok(summary) = load_summary().await {
        set_summary.set(Some(summary));
    }
    if let Ok(sources) = load_sources().await {
        set_sources.set(sources);
    }
}

/// Listens to the desktop `scan-progress` event; polls the last progress
/// when the event API is unavailable.
async fn progress_loop(
    set_progress: WriteSignal<Option<ScanProgressView>>,
    on_completed: impl Fn() + 'static,
) {
    if !is_desktop() {
        return;
    }
    let (sender, mut receiver) = futures_channel::mpsc::unbounded::<ScanProgressEvent>();
    let callback = Closure::<dyn FnMut(JsValue)>::new(move |payload: JsValue| {
        if let Ok(event) = serde_wasm_bindgen::from_value::<ScanProgressEvent>(payload) {
            let _ = sender.unbounded_send(event);
        }
    });
    let listening = listen_event("scan-progress", callback.as_ref().unchecked_ref())
        .await
        .is_ok();
    callback.forget();
    if listening {
        while let Some(event) = receiver.next().await {
            apply_progress_event(set_progress, &on_completed, event);
        }
        return;
    }
    let mut active = false;
    loop {
        TimeoutFuture::new(400).await;
        match invoke::<Option<ScanProgressEvent>>("desktop_scan_progress", serde_json::json!({})).await {
            Ok(Some(mut event)) => {
                active = true;
                event.kind = "progress".into();
                apply_progress_event(set_progress, &on_completed, event);
            }
            Ok(None) if active => {
                active = false;
                apply_progress_event(set_progress, &on_completed, ScanProgressEvent { kind: "completed".into(), ..Default::default() });
            }
            _ => {}
        }
    }
}

fn apply_progress_event(
    set_progress: WriteSignal<Option<ScanProgressView>>,
    on_completed: &impl Fn(),
    event: ScanProgressEvent,
) {
    let kind = event.kind.clone();
    let failed_id = event.scan_id.clone();
    set_progress.update(|current| *current = next_progress(current.take(), event));
    match kind.as_str() {
        "completed" => on_completed(),
        "failed" => spawn_local(async move {
            TimeoutFuture::new(8_000).await;
            set_progress.update(|current| {
                if current.as_ref().is_some_and(|item| item.error.is_some() && item.scan_id == failed_id) {
                    *current = None;
                }
            });
        }),
        _ => {}
    }
}

/// Desktop: reloads the list when the backend reports changed devices
/// (`devices-changed`, after a full scan or a background status refresh).
async fn devices_changed_loop(on_changed: impl Fn() + 'static) {
    if !is_desktop() {
        return;
    }
    let (sender, mut receiver) = futures_channel::mpsc::unbounded::<()>();
    let callback = Closure::<dyn FnMut(JsValue)>::new(move |_payload: JsValue| {
        let _ = sender.unbounded_send(());
    });
    let listening = listen_event("devices-changed", callback.as_ref().unchecked_ref())
        .await
        .is_ok();
    callback.forget();
    if listening {
        while receiver.next().await.is_some() {
            on_changed();
        }
    }
}

async fn websocket_loop(
    set_summary: WriteSignal<Option<StatusSummary>>,
    set_devices: WriteSignal<Vec<Device>>,
    set_sources: WriteSignal<Vec<SourceHealth>>,
    set_connection: WriteSignal<String>,
) {
    if is_desktop() {
        set_connection.set("yerel".into());
        // Changes arrive via `devices-changed`; this is only a slow fallback.
        loop {
            TimeoutFuture::new(30_000).await;
            if let Ok(devices) = load_devices().await {
                set_devices.set(devices);
            }
            refresh_overview(set_summary, set_sources).await;
        }
    }
    let mut delay = 500_u32;
    loop {
        let location = web_sys::window().expect("window").location();
        let scheme = if location.protocol().as_deref() == Ok("https:") {
            "wss"
        } else {
            "ws"
        };
        let url = format!(
            "{scheme}://{}/api/v1/events/ws",
            location.host().unwrap_or_default()
        );
        if let Ok(mut socket) = WebSocket::open(&url) {
            set_connection.set("canlı".into());
            delay = 500;
            while let Some(message) = socket.next().await {
                match message {
                    Ok(Message::Text(text)) => {
                        let Ok(event) = serde_json::from_str::<Event>(&text) else {
                            continue;
                        };
                        if let Some(device_id) = event_device_id(&event) {
                            if let Ok(device) = load_device(&device_id).await {
                                set_devices
                                    .update(|devices| apply_device_update(devices, device.clone()));
                                if let Ok(summary) = load_summary().await {
                                    set_summary.set(Some(summary));
                                }
                            }
                        } else if event.event_type == "snapshot.updated" {
                            refresh_overview(set_summary, set_sources).await;
                        }
                    }
                    Ok(_) => {}
                    Err(_) => break,
                }
            }
        }
        set_connection.set("yeniden bağlanıyor".into());
        TimeoutFuture::new(delay).await;
        delay = (delay * 2).min(30_000);
    }
}

#[component]
fn App() -> impl IntoView {
    let (summary, set_summary) = signal(None::<StatusSummary>);
    let (devices, set_devices) = signal(Vec::<Device>::new());
    let (sources, set_sources) = signal(Vec::<SourceHealth>::new());
    let (load_error, set_load_error) = signal(None::<String>);
    let (loading, set_loading) = signal(true);
    let (query, set_query) = signal(String::new());
    let (filter, set_filter) = signal(KpiFilter::All);
    let (sort, set_sort) = signal(SortState::new(SortColumn::Name, SortDirection::Asc));
    let (selected, set_selected) = signal(None::<uuid::Uuid>);
    let (connection, set_connection) = signal("bağlanıyor".to_string());
    let (scan_message, set_scan_message) = signal(String::new());
    let (onboarded, set_onboarded) = signal(onboarding_accepted());
    let (progress, set_progress) = signal(None::<ScanProgressView>);
    let (diagnostics_open, set_diagnostics_open) = signal(stored_diagnostics_open());
    // Requested but no progress yet, or a scan is running: no second start.
    let (scan_pending, set_scan_pending) = signal(false);
    let scan_busy = move || {
        scan_pending.get() || progress.get().is_some_and(|item| item.error.is_none())
    };
    Effect::new(move |_| {
        if progress.get().is_some() {
            set_scan_pending.set(false);
        }
    });
    Effect::new(move |_| {
        spawn_local(devices_changed_loop(move || {
            spawn_local(async move {
                if let Ok(devices) = load_devices().await {
                    set_devices.set(devices);
                }
                refresh_overview(set_summary, set_sources).await;
            });
        }));
        spawn_local(progress_loop(set_progress, move || {
            set_scan_pending.set(false);
            set_scan_message.set(String::new());
            spawn_local(async move {
                if let Ok(devices) = load_devices().await {
                    set_devices.set(devices);
                }
                refresh_overview(set_summary, set_sources).await;
            });
        }));
        spawn_local(refresh_dashboard(
            set_summary,
            set_devices,
            set_sources,
            set_load_error,
            set_loading,
        ));
        spawn_local(websocket_loop(
            set_summary,
            set_devices,
            set_sources,
            set_connection,
        ));
        if is_desktop() && onboarded.get_untracked() {
            spawn_local(async move {
                match invoke::<LocalNetworkCheck>(
                    "desktop_request_local_network",
                    serde_json::json!({}),
                )
                .await
                {
                    Ok(check) if check.authorized => {
                        let _ = start_scan().await;
                        set_scan_message.set("İlk yerel ağ taraması sürüyor…".into());
                    }
                    Ok(check) => {
                        set_scan_message.set(format!(
                            "{} ({})",
                            check.message,
                            check.diagnostics()
                        ));
                        set_onboarded.set(false);
                    }
                    Err(error) => {
                        set_scan_message.set(format!("Yerel ağ doğrulanamadı: {error}"));
                        set_onboarded.set(false);
                    }
                }
            });
        }
    });

    view! {
        <a class="skip-link" href="#devices">"Cihaz listesine geç"</a>
        <header class="toolbar" data-tauri-drag-region>
            <div class="toolbar-title" data-tauri-drag-region>
                <h1 data-tauri-drag-region>"Ağ görünümü"</h1>
                <span class="conn-dot" class:live=move || connection.get() == "canlı" || connection.get() == "yerel" role="img" aria-live="polite" aria-label=move || format!("Bağlantı: {}", connection.get()) title=move || format!("Bağlantı: {} · Veri: {}", connection.get(), if is_desktop() { "Bu Mac" } else { "Web sunucusu" })></span>
            </div>
            <div class="toolbar-actions">
                <label class="search-field">
                    <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="6.8" cy="6.8" r="4.6" /><path d="m10.3 10.3 3.6 3.6" /></svg>
                    <input type="search" aria-label="Cihaz ara" placeholder="Ara" title="Ad, hostname, IP, üretici" prop:value=query on:input=move |event| set_query.set(event_target_value(&event)) />
                </label>
                <Show when=move || is_desktop()>
                    <button class="tool-button" type="button" class:active=move || diagnostics_open.get() aria-expanded=move || diagnostics_open.get().to_string() aria-controls="sources" title="Keşif tanılama" on:click=move |_| {
                        let open = !diagnostics_open.get_untracked();
                        set_diagnostics_open.set(open);
                        store_diagnostics_open(open);
                    }>
                        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M1.5 8h3l1.8-4.5 3.4 9 1.8-4.5h3" /></svg>
                        "Tanılama"
                        {move || {
                            let count = summary.get().map(|item| item.unhealthy_sources).unwrap_or(0);
                            (count > 0).then(|| view! { <span class="warn-badge" aria-label=format!("{count} sorunlu kaynak")>{count}</span> })
                        }}
                    </button>
                </Show>
                {move || match progress.get() {
                    Some(ScanProgressView { error: Some(error), .. }) => view! { <span class="scan-status error" role="alert" title=error.clone()>{format!("Tarama hatası: {error}")}</span> }.into_any(),
                    Some(item) => view! { <span class="scan-status">{format!("{} · %{}", item.phase, item.percent.round() as u32)}</span> }.into_any(),
                    None => view! { <span class="scan-status" aria-live="polite" title=move || scan_message.get()>{move || scan_message.get()}</span> }.into_any(),
                }}
                <button class="primary-button" type="button" class:busy=scan_busy aria-busy=move || scan_busy().to_string() disabled=scan_busy on:click=move |_| {
                    if scan_busy() {
                        return;
                    }
                    set_scan_pending.set(true);
                    set_scan_message.set("Tarama isteniyor…".into());
                    spawn_local(async move {
                        let message = match start_scan().await {
                            Ok(_) => String::new(),
                            Err(error) => {
                                set_scan_pending.set(false);
                                error
                            }
                        };
                        set_scan_message.set(message);
                    });
                }>{move || if scan_busy() {
                    view! { <span class="spinner" aria-hidden="true"></span>"Taranıyor" }.into_any()
                } else {
                    view! { "Şimdi tara" }.into_any()
                }}</button>
            </div>
            {move || progress.get().filter(|item| item.error.is_none()).map(|item| {
                let percent = item.percent.round() as u32;
                view! {
                    <div class="toolbar-progress" role="progressbar" aria-label="Tarama ilerlemesi" aria-valuemin="0" aria-valuemax="100" aria-valuenow=percent.to_string() aria-valuetext=format!("{} · %{percent}", item.phase)>
                        <div class="toolbar-progress-fill" style=format!("width:{:.1}%", item.percent)></div>
                    </div>
                }
            })}
        </header>
        <main class="app-shell">
            {move || {
                if loading.get() && summary.get().is_none() {
                    return view! { <Loading /> }.into_any();
                }
                if let Some(error) = load_error.get() {
                    if summary.get().is_none() {
                        return view! {
                            <section class="error-panel" role="alert">
                                <strong>"Veriler yüklenemedi"</strong>
                                <p>{if is_desktop() { format!("{error} NetBird bağlantısını ve API endpoint'ini kontrol edin.") } else { error }}</p>
                                <button type="button" on:click=move |_| spawn_local(refresh_dashboard(set_summary, set_devices, set_sources, set_load_error, set_loading))>"Tekrar dene"</button>
                            </section>
                        }.into_any();
                    }
                }
                summary.get().map(|summary| view! {
                        <div class="kpi-strip" role="group" aria-label="Ağ özeti ve filtre">
                            <KpiSegment label="Toplam" value=summary.total_devices active=move || filter.get() == KpiFilter::All on_activate=move || set_filter.set(KpiFilter::All) />
                            <KpiSegment label="Çevrimiçi" value=summary.online_devices active=move || filter.get() == KpiFilter::Online on_activate=move || set_filter.update(|value| *value = if *value == KpiFilter::Online { KpiFilter::All } else { KpiFilter::Online }) />
                            <KpiSegment label="Çevrimdışı" value=summary.offline_devices active=move || filter.get() == KpiFilter::Offline on_activate=move || set_filter.update(|value| *value = if *value == KpiFilter::Offline { KpiFilter::All } else { KpiFilter::Offline }) />
                            <KpiSegment label="NetBird" value=summary.netbird_devices source=DiscoverySourceKind::Netbird active=move || filter.get() == KpiFilter::Netbird on_activate=move || set_filter.update(|value| *value = if *value == KpiFilter::Netbird { KpiFilter::All } else { KpiFilter::Netbird }) />
                            <KpiSegment label="LAN" value=summary.lan_only_devices source=DiscoverySourceKind::Neighbor active=move || filter.get() == KpiFilter::LanOnly on_activate=move || set_filter.update(|value| *value = if *value == KpiFilter::LanOnly { KpiFilter::All } else { KpiFilter::LanOnly }) />
                            <KpiSegment label="mDNS" value=summary.mdns_devices source=DiscoverySourceKind::Mdns active=move || filter.get() == KpiFilter::Mdns on_activate=move || set_filter.update(|value| *value = if *value == KpiFilter::Mdns { KpiFilter::All } else { KpiFilter::Mdns }) />
                            {(summary.unhealthy_sources > 0).then(|| view! {
                                <button type="button" class="kpi-alert" title="Keşif tanılamayı aç" on:click=move |_| {
                                    set_diagnostics_open.set(true);
                                    store_diagnostics_open(true);
                                    if let Some(element) = web_sys::window().and_then(|w| w.document()).and_then(|d| d.get_element_by_id("sources")) { element.scroll_into_view(); }
                                }><span class="warn-badge">{summary.unhealthy_sources}</span>"sorunlu kaynak"</button>
                            })}
                        </div>
                        <Show when=move || is_desktop() && diagnostics_open.get()>
                            <section id="sources" class="diag-panel" aria-label="Keşif tanılama">
                                <div class="diag-head">
                                    <h2>"Keşif tanılama"</h2>
                                    <button type="button" class="tool-button" on:click=move |_| {
                                        let text = diagnostic_text(&sources.get_untracked());
                                        spawn_local(async move { let _ = copy_text(&text).await; });
                                    }>"Kopyala"</button>
                                    <button type="button" class="tool-button icon-only" aria-label="Tanılamayı kapat" on:click=move |_| {
                                        set_diagnostics_open.set(false);
                                        store_diagnostics_open(false);
                                    }>"✕"</button>
                                </div>
                                <div class="diag-grid">
                                    {move || sources.get().into_iter().map(|item| view! {
                                        <article>
                                            <strong><SourceDot source=item.source />{source_label(item.source)}</strong>
                                            <span>{match (item.healthy, item.observation_count) { (false, _) => "Sorunlu".to_string(), (true, 0) => "Çalıştı · cihaz bulunamadı".to_string(), (true, count) => format!("Başarılı · {count} cihaz") }}</span>
                                            <span>{format!("Son çalışma: {}", item.detail.clone().unwrap_or_else(|| "bu oturumda henüz yok".into()))}</span>
                                            <span>{format!("Son başarı: {}", item.last_success_at.map(relative_time).unwrap_or_else(|| "henüz yok".into()))}</span>
                                            <span>{format!("Arayüz/CIDR: {} / {}", item.interface.as_deref().unwrap_or("—"), item.cidr.as_deref().unwrap_or("—"))}</span>
                                            <span>{format!("Gözlem: {}", item.observation_count)}</span>
                                            <span class="diag-error">{item.last_error.unwrap_or_else(|| "Hata yok".into())}</span>
                                        </article>
                                    }).collect_view()}
                                </div>
                            </section>
                        </Show>
                        <DeviceTable
                            devices
                            query filter sort set_sort
                            on_select=move |device| set_selected.set(Some(device.id.0))
                        />
                    }).map(IntoAny::into_any).unwrap_or_else(|| view! { <Loading /> }.into_any())
            }}
        </main>
        <Show when=move || is_desktop() && !onboarded.get()>
            <LocalNetworkOnboarding set_onboarded />
        </Show>
        <DeviceDetails devices selected set_selected set_devices />
    }
}

#[component]
fn LocalNetworkOnboarding(set_onboarded: WriteSignal<bool>) -> impl IntoView {
    let (message, set_message) = signal(None::<Result<String, String>>);
    let (testing, set_testing) = signal(false);
    view! {
        <div class="onboarding-backdrop" role="presentation">
          <section class="onboarding-card" role="dialog" aria-modal="true" aria-labelledby="onboarding-title">
            <p class="eyebrow">"İLK KURULUM"</p>
            <h2 id="onboarding-title">"Yerel ağ erişimi gerekli"</h2>
            <p>"Inet Viewer cihazları doğrudan bu Mac üzerinde keşfeder ve envanteri yerel SQLite veritabanında saklar. Ana panel için macOS Yerel Ağ izni gerekir."</p>
            <p>"NetBird isteğe bağlıdır. NetBird kurulu değilse yalnızca LAN cihazlarıyla devam edebilirsiniz; uygulama parola veya token istemez."</p>
            <div class="endpoint-actions">
                <button class="primary" type="button" disabled=move || testing.get() on:click=move |_| {
                    set_testing.set(true);
                    spawn_local(async move {
                        let result = invoke::<LocalNetworkCheck>("desktop_request_local_network", serde_json::json!({})).await;
                        match result {
                            Ok(check) if check.authorized => {
                                if let Some(storage) = web_sys::window().and_then(|window| window.local_storage().ok().flatten()) {
                                    let _ = storage.set_item("inet-viewer-local-network-onboarding", "accepted");
                                }
                                let _ = start_scan().await;
                                set_message.set(Some(Ok(format!("{} {}", check.message, check.diagnostics()))));
                                set_onboarded.set(true);
                            }
                            Ok(check) => set_message.set(Some(Err(format!("{} {}", check.message, check.diagnostics())))),
                            Err(error) => set_message.set(Some(Err(format!("Yerel ağ denemesi başarısız: {error}. Sistem Ayarları > Gizlilik ve Güvenlik > Yerel Ağ bölümünde Inet Viewer iznini açıp tekrar deneyin.")))),
                        }
                        set_testing.set(false);
                    });
                }>{move || if testing.get() { "Kontrol ediliyor…" } else { "Yerel ağ erişimini iste / tekrar kontrol et" }}</button>
                <button type="button" on:click=move |_| {
                    spawn_local(async move {
                        let _ = invoke::<()>("desktop_open_local_network_settings", serde_json::json!({})).await;
                    });
                }>"Sistem Ayarları'nı aç"</button>
            </div>
            <p class="endpoint-message" role=move || message.get().and_then(Result::err).map(|_| "alert")>{move || message.get().map(|result| result.unwrap_or_else(|error| error)).unwrap_or_default()}</p>
          </section>
        </div>
    }
}

#[component]
fn Loading() -> impl IntoView {
    view! { <section class="loading" role="status"><span class="spinner" aria-hidden="true"></span>"Ağ envanteri yükleniyor…"</section> }
}

/// One segment of the KPI strip: small label + count; click filters the list.
#[component]
fn KpiSegment(
    label: &'static str,
    value: usize,
    active: impl Fn() -> bool + Copy + Send + Sync + 'static,
    on_activate: impl Fn() + Copy + Send + Sync + 'static,
    #[prop(optional)] source: Option<DiscoverySourceKind>,
) -> impl IntoView {
    view! { <button type="button" class="kpi-segment" class:active=active aria-pressed=move || active().to_string() on:click=move |_| on_activate()><span class="kpi-label">{source.map(|source| view! { <SourceDot source /> })}{label}</span><strong>{value}</strong></button> }
}

/// Fixed colour dot of a discovery source (legend in the source strip).
#[component]
fn SourceDot(source: DiscoverySourceKind) -> impl IntoView {
    view! { <span class=format!("source-dot {}", source_class(source)) title=source_label(source) aria-hidden="true"></span> }
}

/// One dot per source a device was found in; the label is in title/aria.
#[component]
fn SourceDots(sources: Vec<DiscoverySourceKind>) -> impl IntoView {
    let label = sources.iter().map(|item| source_label(*item)).collect::<Vec<_>>().join(", ");
    view! {
        <span class="source-dots" role="img" aria-label=format!("Kaynaklar: {label}") title=label.clone()>
            {sources.into_iter().map(|source| view! { <span class=format!("source-dot {}", source_class(source)) title=source_label(source)></span> }).collect_view()}
        </span>
    }
}

/// Devices list their sources in a stable order (NetBird, LAN, mDNS).
fn ordered_sources(sources: &[DiscoverySourceKind]) -> Vec<DiscoverySourceKind> {
    [DiscoverySourceKind::Netbird, DiscoverySourceKind::Neighbor, DiscoverySourceKind::Mdns]
        .into_iter()
        .filter(|kind| sources.contains(kind))
        .collect()
}

#[component]
fn DeviceTable(
    devices: ReadSignal<Vec<Device>>,
    query: ReadSignal<String>,
    filter: ReadSignal<KpiFilter>,
    sort: ReadSignal<SortState>,
    set_sort: WriteSignal<SortState>,
    on_select: impl Fn(Device) + Copy + Send + Sync + 'static,
) -> impl IntoView {
    let visible = Memo::new(move |_| {
        let needle = query.get().trim().to_lowercase();
        let (status, network) = filter.get().criteria();
        let mut rows = devices
            .get()
            .into_iter()
            .filter(|device| {
                (needle.is_empty() || searchable(device).contains(&needle))
                    && (status.is_empty() || status_name(device.status) == status)
                    && matches_network(device, network)
            })
            .collect::<Vec<_>>();
        rows.sort_by(|left, right| compare_devices(left, right, sort.get()));
        rows
    });
    view! {
        <section id="devices" class="device-panel" aria-labelledby="device-title">
            <div class="panel-heading"><h2 id="device-title">"Cihazlar"</h2><span class="result-count" aria-live="polite">{move || format!("{} sonuç", visible.get().len())}</span></div>
            <div class="table-wrap"><table><thead><tr>
                <SortHeader label="Durum" column=SortColumn::Status sort set_sort />
                <SortHeader label="Cihaz" column=SortColumn::Name sort set_sort />
                <SortHeader label="Tür" column=SortColumn::Type sort set_sort />
                <SortHeader label="Yerel IP" column=SortColumn::LanIp sort set_sort />
                <SortHeader label="NetBird IP" column=SortColumn::NetbirdIp sort set_sort />
                <th>"Kaynaklar"</th>
                <SortHeader label="Servis" column=SortColumn::Services sort set_sort />
                <SortHeader label="Son görülme" column=SortColumn::LastSeen sort set_sort />
            </tr></thead>
            <tbody>
                <Show when=move || visible.get().is_empty()>
                    <tr><td colspan="8" class="empty">"Bu filtrelerle eşleşen cihaz yok."</td></tr>
                </Show>
                <For
                    each=move || visible.get()
                    key=|device| device.id.0
                    children=move |device| {
                        let chosen = device.clone();
                        view! {
                            <tr tabindex="0" on:click=move |_| on_select(chosen.clone()) on:keydown=move |event| { if event.key() == "Enter" || event.key() == " " { on_select(device.clone()); } }>
                                <td><StatusBadge status=device.status /></td>
                                <td><strong>{device.display_name.clone()}</strong><small>{device.hostnames.first().cloned().unwrap_or_else(|| "Hostname yok".into())}</small></td>
                                <td>{type_label(device.device_type)}</td>
                                <td class="mono">{join_display(&device.lan_ips)}</td>
                                <td class="mono">{device.netbird_ip.map(|ip| ip.to_string()).unwrap_or_else(|| "—".into())}</td>
                                <td><SourceDots sources=ordered_sources(&device.sources) /></td>
                                <td>{device.services.len()}</td>
                                <td title=device.last_seen_at.to_rfc3339()>{relative_time(device.last_seen_at)}</td>
                            </tr>
                        }
                    }
                />
            </tbody></table></div>
        </section>
    }
}

#[component]
fn SortHeader(
    label: &'static str,
    column: SortColumn,
    sort: ReadSignal<SortState>,
    set_sort: WriteSignal<SortState>,
) -> impl IntoView {
    view! {
        <th aria-sort=move || sort.get().aria_sort(column)>
            <button type="button" class="sort-button" on:click=move |_| set_sort.set(next_sort(Some(sort.get()), column))>
                <span>{label}</span><span class="sort-icon" aria-hidden="true">{move || sort.get().icon(column)}</span>
            </button>
        </th>
    }
}

#[component]
fn StatusBadge(status: DeviceStatus) -> impl IntoView {
    view! { <span class=format!("status-badge {}", status_name(status))><span aria-hidden="true">"●"</span>{status_label(status)}</span> }
}

#[component]
fn DeviceDetails(
    devices: ReadSignal<Vec<Device>>,
    selected: ReadSignal<Option<uuid::Uuid>>,
    set_selected: WriteSignal<Option<uuid::Uuid>>,
    set_devices: WriteSignal<Vec<Device>>,
) -> impl IntoView {
    let device = Memo::new(move |_| {
        selected
            .get()
            .and_then(|id| devices.get().into_iter().find(|item| item.id.0 == id))
    });
    let (editing, set_editing) = signal(false);
    let (edit_name, set_edit_name) = signal(String::new());
    let (edit_type, set_edit_type) = signal(String::new());
    let (saving, set_saving) = signal(false);
    let (save_message, set_save_message) = signal(None::<Result<String, String>>);
    let (detail_device_id, set_detail_device_id) = signal(None::<uuid::Uuid>);
    Effect::new(move |_| {
        let next = selected.get();
        if !should_reset_detail(detail_device_id.get_untracked(), next) {
            return;
        }
        set_detail_device_id.set(next);
        if let Some(id) = next {
            if let Some(current) = devices
                .get_untracked()
                .into_iter()
                .find(|item| item.id.0 == id)
            {
                set_edit_name.set(current.display_name);
                set_edit_type.set(type_name(current.device_type).into());
                set_editing.set(false);
                set_save_message.set(None);
            }
        }
    });
    let history = LocalResource::new(move || {
        let device_id = selected.get().map(|id| id.to_string());
        async move {
            let Some(device_id) = device_id else {
                return Ok(Vec::new());
            };
            if is_desktop() {
                return invoke(
                    "desktop_device_events",
                    serde_json::json!({"id": device_id}),
                )
                .await;
            }
            Request::get(&rest(&format!(
                "/api/v1/devices/{device_id}/events?limit=15"
            )))
            .send()
            .await
            .map_err(|error| error.to_string())?
            .json::<Vec<DeviceEventRecord>>()
            .await
            .map_err(|error| error.to_string())
        }
    });
    let (port_scan, set_port_scan) = signal(None::<PortScan>);
    let (port_scanning, set_port_scanning) = signal(false);
    let (port_error, set_port_error) = signal(None::<String>);
    Effect::new(move |_| {
        let device_id = selected.get().map(|id| id.to_string());
        set_port_scan.set(None);
        set_port_error.set(None);
        let Some(device_id) = device_id else { return; };
        if !is_desktop() {
            return;
        }
        spawn_local(async move {
            if let Ok(stored) = invoke::<Option<PortScan>>("desktop_device_ports", serde_json::json!({"id": device_id.clone()})).await {
                if selected.get_untracked().map(|id| id.to_string()) == Some(device_id) {
                    set_port_scan.set(stored);
                }
            }
        });
    });
    let scan_ports = move |_| {
        if port_scanning.get_untracked() {
            return;
        }
        let Some(id) = selected.get_untracked() else { return; };
        let device_id = id.to_string();
        set_port_scanning.set(true);
        set_port_error.set(None);
        spawn_local(async move {
            let started = js_sys::Date::now();
            let result = invoke::<PortScan>("desktop_scan_ports", serde_json::json!({"id": device_id})).await;
            // Keep the busy state visible briefly so a fast scan still shows feedback.
            let elapsed = js_sys::Date::now() - started;
            if elapsed < 450.0 {
                TimeoutFuture::new((450.0 - elapsed) as u32).await;
            }
            if selected.get_untracked() == Some(id) {
                match result {
                    Ok(scan) => set_port_scan.set(Some(scan)),
                    Err(error) => set_port_error.set(Some(error)),
                }
            }
            set_port_scanning.set(false);
        });
    };
    view! {
        <div class=move || if selected.get().is_some() { "modal-backdrop" } else { "modal-backdrop modal-hidden" } role="presentation" on:click=move |_| set_selected.set(None)>
            <article class="detail-panel" role="dialog" aria-modal="true" aria-labelledby="detail-title" on:click=|event| event.stop_propagation()>
                <div class="detail-heading">
                    <div class="detail-title"><p class="eyebrow">"CİHAZ AYRINTISI"</p><h2 id="detail-title">{move || device.get().map(|item| item.display_name).unwrap_or_default()}</h2></div>
                    <div class="detail-actions">
                        <button class="edit-toggle" type="button" on:click=move |_| set_editing.set(true)>"Düzenle"</button>
                        <button class="close" type="button" aria-label="Ayrıntıları kapat" on:click=move |_| set_selected.set(None)>"×"</button>
                    </div>
                </div>
                {move || device.get().map(|item| view! { <StatusBadge status=item.status /> })}
                <Show when=move || editing.get()>
                    <form class="device-editor" on:submit=move |event| {
                        event.prevent_default();
                        let Some(id) = selected.get_untracked() else { return; };
                        let name = edit_name.get_untracked();
                        let kind = edit_type.get_untracked();
                        set_saving.set(true); set_save_message.set(None);
                        spawn_local(async move {
                            match save_device_override(&id.to_string(), &name, &kind).await {
                                Ok(updated) => { set_devices.update(|items| apply_device_update(items, updated)); set_editing.set(false); set_save_message.set(Some(Ok("Değişiklikler kaydedildi.".into()))); }
                                Err(error) => set_save_message.set(Some(Err(error))),
                            }
                            set_saving.set(false);
                        });
                    }>
                        <label><span>"Görünen ad"</span><input maxlength="128" prop:value=edit_name on:input=move |event| set_edit_name.set(event_target_value(&event)) /></label>
                        <label><span>"Cihaz türü"</span><select prop:value=edit_type on:change=move |event| set_edit_type.set(event_target_value(&event))><option value="server">"Sunucu"</option><option value="computer">"Bilgisayar"</option><option value="phone">"Telefon"</option><option value="tablet">"Tablet"</option><option value="television">"Televizyon"</option><option value="printer">"Yazıcı"</option><option value="network">"Ağ cihazı"</option><option value="iot">"IoT"</option><option value="unknown">"Bilinmiyor"</option></select></label>
                        <div class="editor-actions"><button type="submit" disabled=move || saving.get()>{move || if saving.get() { "Kaydediliyor…" } else { "Kaydet" }}</button><button type="button" on:click=move |_| { if let Some(current) = device.get_untracked() { set_edit_name.set(current.display_name); set_edit_type.set(type_name(current.device_type).into()); } set_editing.set(false); set_save_message.set(None); }>"İptal"</button></div>
                    </form>
                </Show>
                <p class="save-message" aria-live="polite" role=move || save_message.get().and_then(Result::err).map(|_| "alert")>{move || save_message.get().map(|result| result.unwrap_or_else(|error| error)).unwrap_or_default()}</p>
                {move || device.get().map(|device| view! {
                    <dl class="facts"><div><dt>"Kaynaklar"</dt><dd class="source-list">{ordered_sources(&device.sources).into_iter().map(|source| view! { <span class="source-item"><SourceDot source />{source_label(source)}</span> }).collect_view()}</dd></div><div><dt>"Tür"</dt><dd>{type_label(device.device_type)}</dd></div><div><dt>"Üretici / model"</dt><dd>{format!("{}{}", device.manufacturer.clone().unwrap_or_else(|| "Bilinmiyor".into()), device.model.as_ref().map(|m| format!(" · {m}")).unwrap_or_default())}</dd></div><div><dt>"İlk görülme"</dt><dd>{device.first_seen_at.to_rfc3339()}</dd></div><div><dt>"Son görülme"</dt><dd>{device.last_seen_at.to_rfc3339()}</dd></div></dl>
                    <NameList names=device_names(&device) /><DetailList title="Yerel IP adresleri" values=device.lan_ips.iter().map(ToString::to_string).collect() /><DetailList title="MAC adresleri" values=device.mac_addresses.iter().map(ToString::to_string).collect() />
                    <section class="detail-section"><h3>"mDNS servisleri"</h3>{if device.services.is_empty() { view! { <p>"Servis bulunamadı."</p> }.into_any() } else { view! { <ul class="service-list">{device.services.iter().map(|service| view! { <li><strong>{service.name.clone().unwrap_or_else(|| service.service_type.clone())}</strong><span>{format!("{} · {} · {}/{}", service.service_type, service.hostname.clone().unwrap_or_else(|| "host yok".into()), protocol_label(service.protocol), service.port)}</span></li> }).collect_view()}</ul> }.into_any() }}</section>
                })}
                <section class="detail-section port-section">
                    <div class="section-heading"><h3>"Açık portlar"</h3>
                        <Show when=is_desktop><button class="port-scan" type="button" class:busy=move || port_scanning.get() aria-busy=move || port_scanning.get().to_string() disabled=move || port_scanning.get() on:click=scan_ports>{move || if port_scanning.get() { view! { <span class="spinner" aria-hidden="true"></span>"Taranıyor…" }.into_any() } else { "Portları tara".into_any() }}</button></Show>
                    </div>
                    {move || if port_scanning.get() { view! { <p class="port-status"><span class="spinner" aria-hidden="true"></span>"Yaygın TCP portları taranıyor…"</p> }.into_any() } else { ().into_any() }}
                    {move || port_error.get().map(|error| view! { <p role="alert">{error}</p> })}
                    {move || match port_scan.get() {
                        None if !is_desktop() => view! { <p>"Port taraması yalnızca masaüstü uygulamasında yapılabilir."</p> }.into_any(),
                        None => view! { <p>"Henüz port taraması yapılmadı."</p> }.into_any(),
                        Some(scan) => view! {
                            <p class="port-meta">{port_scan_summary(&scan)}</p>
                            {if scan.open_ports.is_empty() { view! { <p>"Açık port bulunamadı."</p> }.into_any() } else { view! { <ul class="service-list">{scan.open_ports.iter().map(|port| view! { <li><strong class="mono">{format!("{}/{}", port.port, protocol_label(port.protocol))}</strong><span>{format!("{} · {} · {}", port.service.clone().unwrap_or_else(|| "bilinmiyor".into()), port_source_label(&port.source), port.address)}</span></li> }).collect_view()}</ul> }.into_any() }}
                        }.into_any(),
                    }}
                </section>
                <section class="detail-section"><h3>"Yakın durum geçmişi"</h3><Suspense fallback=move || view! { <p>"Geçmiş yükleniyor…"</p> }>{move || history.get().map(|result| match &*result { Ok(events) if events.is_empty() => view! { <p>"Henüz durum olayı yok."</p> }.into_any(), Ok(events) => view! { <ul class="service-list">{events.iter().map(|event| view! { <li><strong>{event_label(&event.event_type)}</strong><span>{event.occurred_at.to_rfc3339()}</span></li> }).collect_view()}</ul> }.into_any(), Err(error) => view! { <p role="alert">{error.clone()}</p> }.into_any() })}</Suspense></section>
            </article>
        </div>
    }
}

#[component]
fn NameList(names: Vec<DeviceName>) -> impl IntoView {
    view! { <section class="detail-section"><h3>"Hostname'ler"</h3><div class="badges">{if names.is_empty() { view! { <span>"—"</span> }.into_any() } else { names.into_iter().map(|name| view! { <span class="tag mono" title=name_source_label(&name.source)>{name.name}<small class="name-source">{name_source_label(&name.source)}</small></span> }).collect_view().into_any() }}</div></section> }
}

/// Names with their method; hostnames without a recorded method are kept.
fn device_names(device: &Device) -> Vec<DeviceName> {
    let mut names = device.names.clone();
    for hostname in &device.hostnames {
        if !names.iter().any(|name| name.name.eq_ignore_ascii_case(hostname)) {
            names.push(DeviceName { name: hostname.clone(), source: String::new() });
        }
    }
    names
}

fn name_source_label(source: &str) -> &'static str {
    match source {
        "mdns" => "mDNS",
        "netbios" => "NetBIOS",
        "ssdp" => "SSDP",
        "ptr" => "DNS PTR",
        "netbird" => "NetBird",
        "neighbor" => "Komşu",
        "" => "kayıtlı",
        _ => "diğer",
    }
}

fn port_source_label(source: &str) -> &'static str {
    match source {
        "mdns" => "mDNS",
        _ => "TCP taraması",
    }
}

fn protocol_label(protocol: inet_viewer_shared::TransportProtocol) -> &'static str {
    match protocol {
        inet_viewer_shared::TransportProtocol::Tcp => "tcp",
        inet_viewer_shared::TransportProtocol::Udp => "udp",
    }
}

fn port_scan_summary(scan: &PortScan) -> String {
    format!(
        "Son tarama {} · {} port denendi · {:.1} sn",
        relative_time(scan.scanned_at),
        scan.ports_checked,
        scan.elapsed_ms as f64 / 1000.0
    )
}

#[component]
fn DetailList(title: &'static str, values: Vec<String>) -> impl IntoView {
    view! { <section class="detail-section"><h3>{title}</h3><div class="badges">{if values.is_empty() { view! { <span>"—"</span> }.into_any() } else { values.into_iter().map(|value| view! { <span class="tag mono">{value}</span> }).collect_view().into_any() }}</div></section> }
}

fn searchable(device: &Device) -> String {
    format!(
        "{} {} {} {} {}",
        device.display_name,
        device.hostnames.join(" "),
        join_display(&device.lan_ips),
        device.manufacturer.as_deref().unwrap_or(""),
        device.model.as_deref().unwrap_or("")
    )
    .to_lowercase()
}
fn join_display<T: ToString>(values: &[T]) -> String {
    if values.is_empty() {
        "—".into()
    } else {
        values
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>()
            .join(", ")
    }
}
fn status_name(value: DeviceStatus) -> &'static str {
    match value {
        DeviceStatus::Online => "online",
        DeviceStatus::Idle => "idle",
        DeviceStatus::Connecting => "connecting",
        DeviceStatus::Offline => "offline",
        DeviceStatus::Unknown => "unknown",
    }
}
fn status_label(value: DeviceStatus) -> &'static str {
    match value {
        DeviceStatus::Online => "Çevrimiçi",
        DeviceStatus::Idle => "Boşta",
        DeviceStatus::Connecting => "Bağlanıyor",
        DeviceStatus::Offline => "Çevrimdışı",
        DeviceStatus::Unknown => "Bilinmiyor",
    }
}
fn source_label(value: DiscoverySourceKind) -> &'static str {
    match value {
        DiscoverySourceKind::Netbird => "NetBird",
        DiscoverySourceKind::Neighbor => "LAN",
        DiscoverySourceKind::Mdns => "mDNS",
    }
}
fn source_class(value: DiscoverySourceKind) -> &'static str {
    match value {
        DiscoverySourceKind::Netbird => "src-netbird",
        DiscoverySourceKind::Neighbor => "src-lan",
        DiscoverySourceKind::Mdns => "src-mdns",
    }
}
fn type_name(value: DeviceType) -> &'static str {
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
fn type_label(value: DeviceType) -> &'static str {
    match value {
        DeviceType::Server => "Sunucu",
        DeviceType::Computer => "Bilgisayar",
        DeviceType::Phone => "Telefon",
        DeviceType::Tablet => "Tablet",
        DeviceType::Television => "Televizyon",
        DeviceType::Printer => "Yazıcı",
        DeviceType::Network => "Ağ cihazı",
        DeviceType::Iot => "IoT",
        DeviceType::Unknown => "Bilinmiyor",
    }
}
fn relative_time(value: chrono::DateTime<chrono::Utc>) -> String {
    let seconds = (chrono::Utc::now() - value).num_seconds().max(0);
    match seconds.cmp(&60) {
        Ordering::Less => "şimdi".into(),
        _ if seconds < 3600 => format!("{} dk önce", seconds / 60),
        _ if seconds < 86400 => format!("{} sa önce", seconds / 3600),
        _ => format!("{} gün önce", seconds / 86400),
    }
}

fn event_label(value: &str) -> &'static str {
    match value {
        "device.discovered" => "Keşfedildi",
        "device.online" => "Çevrimiçi oldu",
        "device.offline" => "Çevrimdışı oldu",
        _ => "Güncellendi",
    }
}

fn main() {
    if let Some(storage) = local_storage() {
        let _ = storage.remove_item(LEGACY_THEME_STORAGE_KEY);
    }
    enable_glass();
    mount_to_body(App);
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;
    use inet_viewer_shared::DeviceId;

    fn device(id: &str, name: &str) -> Device {
        let id = id.parse().expect("valid UUID");
        Device {
            id: DeviceId(id),
            display_name: name.into(),
            hostnames: Vec::new(),
            names: Vec::new(),
            lan_ips: Vec::new(),
            netbird_ip: None,
            netbird_ipv6: None,
            mac_addresses: Vec::new(),
            manufacturer: None,
            model: None,
            device_type: DeviceType::Unknown,
            status: DeviceStatus::Online,
            sources: Vec::new(),
            services: Vec::new(),
            first_seen_at: Utc::now(),
            last_seen_at: Utc::now(),
        }
    }

    #[test]
    fn detail_names_keep_method_and_unrecorded_hostnames() {
        let mut item = device("11111111-1111-4111-8111-111111111111", "tv");
        item.names.push(DeviceName { name: "tv.local".into(), source: "mdns".into() });
        item.hostnames = vec!["TV.LOCAL".into(), "tv.lan".into()];
        let names = device_names(&item);
        assert_eq!(names.len(), 2);
        assert_eq!(name_source_label(&names[0].source), "mDNS");
        assert_eq!(name_source_label(&names[1].source), "kayıtlı");
        assert_eq!(port_source_label("tcp"), "TCP taraması");
    }

    #[test]
    fn device_event_extracts_the_changed_device_id() {
        let id = "11111111-1111-4111-8111-111111111111";
        let event = Event {
            event_type: "device.updated".into(),
            occurred_at: Utc::now(),
            data: serde_json::json!({"device_id": id, "changed_fields": ["status"]}),
        };

        assert_eq!(event_device_id(&event), Some(id.to_string()));
    }

    #[test]
    fn device_update_replaces_only_the_matching_record() {
        let first_id = "11111111-1111-4111-8111-111111111111";
        let second_id = "22222222-2222-4222-8222-222222222222";
        let first = device(first_id, "first");
        let second = device(second_id, "second");
        let updated = device(second_id, "second updated");
        let mut devices = vec![first.clone(), second];

        apply_device_update(&mut devices, updated.clone());

        assert_eq!(devices, vec![first, updated]);
    }

    #[test]
    fn discovered_device_is_appended_without_replacing_existing_records() {
        let first = device("11111111-1111-4111-8111-111111111111", "first");
        let discovered = device("22222222-2222-4222-8222-222222222222", "discovered");
        let mut devices = vec![first.clone()];

        apply_device_update(&mut devices, discovered.clone());

        assert_eq!(devices, vec![first, discovered]);
    }

    #[test]
    fn same_device_live_update_does_not_reset_detail_ui_state() {
        let id = "11111111-1111-4111-8111-111111111111".parse().unwrap();
        let other = "22222222-2222-4222-8222-222222222222".parse().unwrap();
        assert!(!should_reset_detail(Some(id), Some(id)));
        assert!(should_reset_detail(Some(id), Some(other)));
        assert!(should_reset_detail(Some(id), None));
    }

    #[test]
    fn kpi_cards_map_to_the_expected_device_filters() {
        assert_eq!(KpiFilter::All.criteria(), ("", ""));
        assert_eq!(KpiFilter::Online.criteria(), ("online", ""));
        assert_eq!(KpiFilter::Offline.criteria(), ("offline", ""));
        assert_eq!(KpiFilter::Netbird.criteria(), ("", "netbird"));
        assert_eq!(KpiFilter::LanOnly.criteria(), ("", "lan_only"));
        assert_eq!(KpiFilter::Mdns.criteria(), ("", "mdns"));
    }

    #[test]
    fn network_filters_mean_contains_this_source() {
        let mut both = device("00000000-0000-0000-0000-000000000001", "tv");
        both.sources = vec![DiscoverySourceKind::Neighbor, DiscoverySourceKind::Mdns];
        both.lan_ips = vec!["192.168.1.5".parse().unwrap()];
        assert!(matches_network(&both, "mdns"));
        assert!(matches_network(&both, "lan_only"));
        assert!(!matches_network(&both, "netbird"));
        let mut peer = both.clone();
        peer.sources.push(DiscoverySourceKind::Netbird);
        assert!(matches_network(&peer, "netbird") && matches_network(&peer, "mdns"));
        assert!(!matches_network(&peer, "lan_only"));
        assert_eq!(
            ordered_sources(&[DiscoverySourceKind::Mdns, DiscoverySourceKind::Netbird]),
            [DiscoverySourceKind::Netbird, DiscoverySourceKind::Mdns]
        );
    }

    #[test]
    fn progress_events_show_hide_and_report_errors() {
        let event = |kind: &str| ScanProgressEvent { kind: kind.into(), scan_id: "s".into(), phase: "LAN: 1/2".into(), percent: 42.0, error: None };
        let shown = next_progress(None, event("progress")).unwrap();
        assert_eq!((shown.percent, shown.phase.as_str()), (42.0, "LAN: 1/2"));
        assert_eq!(next_progress(Some(shown.clone()), event("completed")), None);
        let mut failed = event("failed");
        failed.error = Some("all discovery sources failed".into());
        assert_eq!(next_progress(Some(shown), failed).unwrap().error.as_deref(), Some("all discovery sources failed"));
        assert_eq!(next_progress(None, event("started")).unwrap().phase, "LAN: 1/2");
    }

    #[test]
    fn sorting_uses_a_sensible_initial_direction_then_toggles() {
        assert_eq!(
            next_sort(None, SortColumn::Name),
            SortState::new(SortColumn::Name, SortDirection::Asc)
        );
        assert_eq!(
            next_sort(
                Some(SortState::new(SortColumn::Name, SortDirection::Asc)),
                SortColumn::Name
            ),
            SortState::new(SortColumn::Name, SortDirection::Desc)
        );
        assert_eq!(
            next_sort(None, SortColumn::LastSeen),
            SortState::new(SortColumn::LastSeen, SortDirection::Desc)
        );
    }

    #[test]
    fn device_comparator_honours_column_and_direction() {
        let alpha = device("11111111-1111-4111-8111-111111111111", "Alpha");
        let zulu = device("22222222-2222-4222-8222-222222222222", "Zulu");
        assert_eq!(
            compare_devices(
                &alpha,
                &zulu,
                SortState::new(SortColumn::Name, SortDirection::Asc)
            ),
            Ordering::Less
        );
        assert_eq!(
            compare_devices(
                &alpha,
                &zulu,
                SortState::new(SortColumn::Name, SortDirection::Desc)
            ),
            Ordering::Greater
        );
    }

    #[test]
    fn detail_header_actions_have_non_overlapping_responsive_layout() {
        let styles = include_str!("../styles.css");

        assert!(styles
            .contains(".detail-heading{display:grid;grid-template-columns:minmax(0,1fr) auto"));
        assert!(styles.contains(".detail-actions{display:flex;align-items:center;justify-content:flex-end;gap:.5rem;flex-wrap:wrap}"));
        assert!(styles.contains(".detail-actions>button{position:static;inset:auto"));
        assert!(
            styles.contains(".detail-actions>button:focus-visible{outline:2px solid var(--accent)")
        );
        assert!(styles.contains(
            "@media(max-width:620px){.detail-heading{grid-template-columns:minmax(0,1fr)}"
        ));
    }
}
