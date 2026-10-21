#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use inet_viewer_server::{
    api::StatusResponse,
    discovery::{
        browse_mdns, build_port_scan, detect_macos_network, device_scan_targets,
        filter_macos_lan_observations, parse_macos_arp, probe_liveness_with_progress,
        resolve_names, scan_tcp_ports, DiscoveryError, DiscoveryProvider, DiscoveryRun,
        DiscoveryRunner, LivenessConfig, MacOsArpProvider, MacOsNetwork, MdnsConfig,
        NetbirdProvider, PortScanConfig, ProgressSink,
    },
    events::EventBus,
    refresh::{LivenessReachability, StatusRefresher},
    repository::{DeviceEventRecord, Repository},
    scan::{ScanService, StartError},
};
use inet_viewer_shared::{Device, DeviceStatus, DeviceType, DiscoverySourceKind, PortScan};
use serde::{Deserialize, Serialize};
use std::{
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};
use tauri::{Emitter, Manager, RunEvent, State};

/// Last run summary per source for the diagnostics panel.
type SourceDetails = Arc<std::sync::Mutex<std::collections::HashMap<DiscoverySourceKind, String>>>;

fn set_detail(details: &SourceDetails, source: DiscoverySourceKind, text: String) {
    if let Ok(mut map) = details.lock() {
        map.insert(source, text);
    }
}

struct DesktopState {
    repository: Repository,
    scans: ScanService,
    local_network_authorized: Arc<AtomicBool>,
    local_probe: Arc<dyn DiscoveryProvider>,
    network: MacOsNetwork,
    details: SourceDetails,
    /// First launch only: until this instant a denial is reported as
    /// `pending`, because macOS denies while its permission prompt is open.
    prompt_deadline: std::sync::Mutex<Option<std::time::Instant>>,
}

/// System Settings > Privacy & Security > Local Network.
const LOCAL_NETWORK_SETTINGS_URL: &str =
    "x-apple.systempreferences:com.apple.preference.security?Privacy_LocalNetwork";
/// How long a first-launch denial is treated as "waiting for the prompt".
const PROMPT_GRACE: Duration = Duration::from_secs(30);
/// Native probe timeout for the light status check (polled every ~2 s).
const STATUS_PROBE_TIMEOUT: f64 = 1.5;

#[derive(Serialize)]
struct DesktopSourceResponse {
    source: DiscoverySourceKind,
    healthy: bool,
    last_success_at: Option<chrono::DateTime<chrono::Utc>>,
    last_error_at: Option<chrono::DateTime<chrono::Utc>>,
    last_error: Option<String>,
    interface: Option<String>,
    cidr: Option<String>,
    observation_count: usize,
    detail: Option<String>,
}

#[derive(Serialize)]
struct DevicesResponse {
    devices: Vec<Device>,
    total: usize,
}

#[derive(Serialize)]
struct ScanQueuedResponse {
    scan_id: String,
}

#[derive(Serialize)]
struct DesktopEnvironment {
    netbird_installed: bool,
    netbird_path: Option<String>,
    local_database: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum LocalNetworkStatus {
    Granted,
    Denied,
    /// First launch: the OS prompt may still be open; not scanning yet.
    Pending,
    Offline,
    ProbeFailed,
    /// No signal proved or disproved access; scanning is still allowed.
    Unknown,
}

#[derive(Serialize)]
struct LocalNetworkCheck {
    authorized: bool,
    status: LocalNetworkStatus,
    code: &'static str,
    message: String,
    interface: String,
    private_ip: String,
    cidr: String,
    gateway: String,
    native_probe: &'static str,
    unicast_probe: &'static str,
    lan_probe: &'static str,
}

#[cfg(target_os = "macos")]
extern "C" {
    fn inet_viewer_arp_table(
        out: *mut std::os::raw::c_char,
        out_len: usize,
        entries: *mut std::os::raw::c_int,
    ) -> std::os::raw::c_int;
    fn inet_viewer_local_network_probe(
        timeout_seconds: f64,
        log: *mut std::os::raw::c_char,
        log_len: usize,
    ) -> std::os::raw::c_int;
}

/// Runs the native Bonjour round-trip probe; returns its code and state log.
fn native_local_network_probe(timeout_seconds: f64) -> (i32, String) {
    #[cfg(target_os = "macos")]
    {
        let mut buffer = vec![0u8; 8192];
        let code = unsafe {
            inet_viewer_local_network_probe(timeout_seconds, buffer.as_mut_ptr().cast(), buffer.len())
        };
        let end = buffer.iter().position(|byte| *byte == 0).unwrap_or(buffer.len());
        (code, String::from_utf8_lossy(&buffer[..end]).into_owned())
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = timeout_seconds;
        (0, String::new())
    }
}

/// Reads the ARP table in this process (sysctl), returned in `arp -an` format.
fn native_arp_table() -> Result<(String, i32), i32> {
    #[cfg(target_os = "macos")]
    {
        let mut buffer = vec![0u8; 512 * 1024];
        let mut entries: std::os::raw::c_int = 0;
        let rc = unsafe {
            inet_viewer_arp_table(buffer.as_mut_ptr().cast(), buffer.len(), &mut entries)
        };
        if rc != 0 {
            return Err(rc);
        }
        let end = buffer.iter().position(|byte| *byte == 0).unwrap_or(buffer.len());
        Ok((String::from_utf8_lossy(&buffer[..end]).into_owned(), entries))
    }
    #[cfg(not(target_os = "macos"))]
    Ok((String::new(), 0))
}

fn lan_targets(network: &MacOsNetwork) -> Vec<std::net::Ipv4Addr> {
    let Some((base, prefix)) = network.cidr.split_once('/') else {
        return Vec::new();
    };
    let (Ok(base), Ok(prefix)) = (base.parse::<std::net::Ipv4Addr>(), prefix.parse::<u32>())
    else {
        return Vec::new();
    };
    if !(24..=30).contains(&prefix) {
        return Vec::new();
    }
    let mask = u32::MAX << (32 - prefix);
    let start = u32::from(base) & mask;
    let broadcast = start | !mask;
    (start + 1..broadcast)
        .map(std::net::Ipv4Addr::from)
        .filter(|address| *address != network.address)
        .collect()
}

/// Neighbor discovery that runs inside the app process: a unicast UDP sweep
/// fills the ARP cache, then the table is read via sysctl (in-process) and via
/// `/usr/sbin/arp -an`; both are merged and filtered to the active LAN. Each
/// step writes a short line to probe.log.
struct DesktopNeighborProvider {
    network: MacOsNetwork,
    arp: MacOsArpProvider,
    details: SourceDetails,
}

impl DesktopNeighborProvider {
    fn new(network: MacOsNetwork, details: SourceDetails) -> Result<Self, DiscoveryError> {
        Ok(Self {
            arp: MacOsArpProvider::new("/usr/sbin/arp")?,
            network,
            details,
        })
    }
}

fn udp_sweep(targets: &[std::net::Ipv4Addr]) -> String {
    use std::net::{SocketAddr, UdpSocket};
    let socket = match UdpSocket::bind("0.0.0.0:0") {
        Ok(socket) => socket,
        Err(error) => return format!("neighbor sweep bind failed: {error}"),
    };
    let mut sent = 0;
    let mut errors: std::collections::BTreeMap<String, usize> = Default::default();
    for target in targets {
        match socket.send_to(&[0u8], SocketAddr::from((*target, 9))) {
            Ok(_) => sent += 1,
            Err(error) => {
                *errors
                    .entry(format!("errno={:?}", error.raw_os_error()))
                    .or_default() += 1
            }
        }
    }
    format!(
        "neighbor sweep targets={} sent={sent} errors={errors:?}",
        targets.len()
    )
}

#[async_trait::async_trait]
impl DiscoveryProvider for DesktopNeighborProvider {
    fn kind(&self) -> DiscoverySourceKind {
        DiscoverySourceKind::Neighbor
    }

    async fn discover(&self) -> Result<DiscoveryRun, DiscoveryError> {
        self.discover_with_progress(ProgressSink::noop(DiscoverySourceKind::Neighbor))
            .await
    }

    async fn discover_with_progress(
        &self,
        progress: ProgressSink,
    ) -> Result<DiscoveryRun, DiscoveryError> {
        let started_at = chrono::Utc::now();
        progress.report("ARP", 0, 1);
        let mut log = format!(
            "--- neighbor interface={} cidr={}\n",
            self.network.interface, self.network.cidr
        );
        let targets = lan_targets(&self.network);
        let sweep_targets = targets.clone();
        let sweep = tauri::async_runtime::spawn_blocking(move || udp_sweep(&sweep_targets))
            .await
            .unwrap_or_else(|error| format!("neighbor sweep task failed: {error}"));
        log.push_str(&sweep);
        log.push('\n');
        tokio::time::sleep(Duration::from_secs(2)).await;

        let mut observations = Vec::new();
        let mut warnings = Vec::new();
        let native = tauri::async_runtime::spawn_blocking(native_arp_table)
            .await
            .unwrap_or(Err(-1));
        let native_ok = match native {
            Ok((text, entries)) => {
                let parsed = parse_macos_arp(&text, chrono::Utc::now());
                log.push_str(&format!(
                    "neighbor sysctl rc=0 entries={entries} parsed={} warnings={}\n",
                    parsed.observations.len(),
                    parsed.warnings.len()
                ));
                observations.extend(parsed.observations);
                warnings.extend(parsed.warnings);
                true
            }
            Err(rc) => {
                log.push_str(&format!("neighbor sysctl rc={rc}\n"));
                false
            }
        };
        let command_error = match self.arp.discover().await {
            Ok(run) => {
                log.push_str(&format!(
                    "neighbor cmd=/usr/sbin/arp -an exit=0 parsed={} warnings={}\n",
                    run.observations.len(),
                    run.warnings.len()
                ));
                for warning in run.warnings.iter().take(3) {
                    log.push_str(&format!("neighbor warning: {}\n", warning.input));
                }
                observations.extend(run.observations);
                warnings.extend(run.warnings);
                None
            }
            Err(error) => {
                log.push_str(&format!("neighbor cmd=/usr/sbin/arp -an error={error}\n"));
                Some(error)
            }
        };
        let total = observations.len();
        let mut seen = std::collections::HashSet::new();
        observations.retain(|observation| {
            seen.insert((
                observation.ip_addresses.first().copied(),
                observation.mac_address.as_ref().map(ToString::to_string),
            ))
        });
        let unique = observations.len();
        let mut kept = filter_macos_lan_observations(observations, &self.network);
        log.push_str(&format!(
            "neighbor arp merged={total} unique={unique} after_filter={}\n",
            kept.len()
        ));

        // ARP-independent liveness (ICMP + TCP connect) from this process.
        // TCP is only tried on hosts that did not answer ICMP.
        let target_count = targets.len();
        let report = probe_liveness_with_progress(targets, &LivenessConfig::default(), |done, total| {
            progress.report("LAN", done, total)
        })
        .await;
        let alive: Vec<_> = report.alive.keys().copied().collect();
        log.push_str(&format!(
            "liveness targets={target_count} alive={} icmp={} ({}) tcp_targets={} tcp={} tcp_errors={:?} elapsed={:.1}s\n",
            alive.len(),
            report.icmp_alive,
            report.icmp_status,
            target_count.saturating_sub(report.icmp_alive),
            report.tcp_alive,
            report.tcp_errors,
            report.elapsed.as_secs_f32()
        ));
        progress.report("İsimler", 0, 1);
        let names = resolve_names(&alive, Duration::from_secs(4)).await;
        let best = names
            .names
            .values()
            .filter(|item| item.best_hostname().is_some())
            .count();
        log.push_str(&format!(
            "names alive={} mdns={} netbios={} ssdp={} ptr={} named={best} errors={:?} elapsed={:.1}s\n",
            alive.len(),
            names.mdns,
            names.netbios,
            names.ssdp,
            names.ptr,
            names.errors,
            names.elapsed.as_secs_f32()
        ));
        let now = chrono::Utc::now();
        let mut added = 0;
        for (address, method) in &report.alive {
            let ip = std::net::IpAddr::V4(*address);
            let host_names = names.names.get(address).cloned().unwrap_or_default();
            let best_name = host_names.best_hostname();
            let observation = match kept
                .iter_mut()
                .position(|observation| observation.ip_addresses.contains(&ip))
            {
                Some(index) => &mut kept[index],
                None => {
                    let mut attributes = std::collections::HashMap::new();
                    attributes.insert("interface".into(), self.network.interface.clone());
                    kept.push(inet_viewer_shared::Observation {
                        source: DiscoverySourceKind::Neighbor,
                        source_id: None,
                        hostname: None,
                        ip_addresses: vec![ip],
                        mac_address: None,
                        manufacturer: None,
                        model: None,
                        status: inet_viewer_shared::ObservationStatus::Online,
                        services: Vec::new(),
                        observed_at: now,
                        attributes,
                    });
                    added += 1;
                    kept.last_mut().expect("just pushed")
                }
            };
            observation
                .attributes
                .insert("liveness".into(), method.to_string());
            for (key, value) in host_names.attributes() {
                observation.attributes.insert(key, value);
            }
            if observation.hostname.is_none() {
                if let Some((name, source)) = best_name {
                    observation.hostname = Some(name);
                    observation
                        .attributes
                        .insert("hostname_source".into(), source.into());
                }
            }
            if observation.model.is_none() {
                observation.model = host_names.ssdp_model.clone();
            }
            if observation.manufacturer.is_none() {
                observation.manufacturer = host_names.ssdp_manufacturer.clone();
            }
        }
        log.push_str(&format!(
            "neighbor observations={} (liveness_only={added})\n",
            kept.len()
        ));
        set_detail(
            &self.details,
            DiscoverySourceKind::Neighbor,
            format!(
                "Canlı {}/{target_count} (ICMP {}, TCP {}) · isim {best} · gözlem {}",
                alive.len(),
                report.icmp_alive,
                report.tcp_alive,
                kept.len()
            ),
        );
        write_probe_log(&log);
        if let (false, Some(error), true) = (native_ok, command_error, kept.is_empty()) {
            return Err(error);
        }
        Ok(DiscoveryRun {
            kind: DiscoverySourceKind::Neighbor,
            observations: kept,
            warnings,
            started_at,
            finished_at: chrono::Utc::now(),
        })
    }
}

/// Info.plist NSBonjourServices (kept in sync with MDNS_BROWSE_TYPES by a test).
#[cfg(test)]
fn plist_bonjour_types() -> Vec<String> {
    let plist = include_str!("../Info.plist");
    let Some(start) = plist.find("<key>NSBonjourServices</key>") else {
        return Vec::new();
    };
    let section = &plist[start..];
    let section = &section[..section.find("</array>").unwrap_or(section.len())];
    section
        .split("<string>")
        .skip(1)
        .filter_map(|item| item.split_once("</string>").map(|(value, _)| value.trim().to_owned()))
        .collect()
}

/// In-process mDNS / DNS-SD discovery (mdns-sd); results are limited to the
/// active LAN and each run writes an `mdns` line to probe.log.
struct DesktopMdnsProvider {
    network: MacOsNetwork,
    details: SourceDetails,
}

#[async_trait::async_trait]
impl DiscoveryProvider for DesktopMdnsProvider {
    fn kind(&self) -> DiscoverySourceKind {
        DiscoverySourceKind::Mdns
    }

    async fn discover(&self) -> Result<DiscoveryRun, DiscoveryError> {
        self.discover_with_progress(ProgressSink::noop(DiscoverySourceKind::Mdns))
            .await
    }

    async fn discover_with_progress(
        &self,
        progress: ProgressSink,
    ) -> Result<DiscoveryRun, DiscoveryError> {
        progress.report("mDNS", 0, 1);
        let started_at = chrono::Utc::now();
        // Raw-socket browse of every advertised type; NSBonjourServices does
        // not filter mdns-sd, so the plist is not used as a filter here.
        let report = browse_mdns(MdnsConfig::default()).await;
        let mut lan: std::collections::HashSet<std::net::Ipv4Addr> =
            lan_targets(&self.network).into_iter().collect();
        lan.insert(self.network.address);
        let total = report.observations.len();
        let observations: Vec<_> = report
            .observations
            .into_iter()
            .filter_map(|mut observation| {
                observation.ip_addresses.retain(|address| match address {
                    std::net::IpAddr::V4(v4) => lan.contains(v4),
                    std::net::IpAddr::V6(v6) => v6.segments()[0] & 0xffc0 == 0xfe80,
                });
                observation
                    .ip_addresses
                    .iter()
                    .any(std::net::IpAddr::is_ipv4)
                    .then_some(observation)
            })
            .collect();
        let found: Vec<_> = report.types_found.iter().map(String::as_str).collect();
        let unresolved: Vec<_> = report.unresolved.iter().map(String::as_str).collect();
        write_probe_log(&format!(
            "mdns types={} browsed={} resolved={} lan_observations={} (of {total}) unresolved={} {:?} error={} elapsed={:.1}s\nmdns found_types={found:?}\nmdns resolved_by_type={:?}\n",
            report.types_found.len(),
            report.browsed,
            report.resolved,
            observations.len(),
            unresolved.len(),
            unresolved,
            report.error.as_deref().unwrap_or("none"),
            report.elapsed.as_secs_f32(),
            report.resolved_by_type
        ));
        set_detail(
            &self.details,
            DiscoverySourceKind::Mdns,
            format!(
                "Tip {} · göz atılan {} · çözümlenen {} · LAN gözlem {}/{total}{}{}",
                report.types_found.len(),
                report.browsed,
                report.resolved,
                observations.len(),
                if unresolved.is_empty() { String::new() } else { format!(" · çözümlenemeyen {} ({})", unresolved.len(), unresolved.join(", ")) },
                report.error.as_deref().map(|error| format!(" · hata: {error}")).unwrap_or_default()
            ),
        );
        if let (Some(error), 0) = (&report.error, report.browsed) {
            return Err(DiscoveryError::Exit {
                code: None,
                stderr: error.clone(),
            });
        }
        Ok(DiscoveryRun {
            kind: DiscoverySourceKind::Mdns,
            observations,
            warnings: Vec::new(),
            started_at,
            finished_at: chrono::Utc::now(),
        })
    }
}

/// Outcome of one unicast attempt to the gateway from this (main app) process.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum UnicastSignal {
    Granted,
    Denied,
    Unknown,
}

impl UnicastSignal {
    fn label(self) -> &'static str {
        match self {
            UnicastSignal::Granted => "granted",
            UnicastSignal::Denied => "denied",
            UnicastSignal::Unknown => "unknown",
        }
    }
}

const EHOSTUNREACH: i32 = 65;
const ECONNREFUSED: i32 = 61;
const ECONNRESET: i32 = 54;

/// Under a Local Network denial macOS fails LAN unicast with EHOSTUNREACH.
fn classify_unicast_errno(errno: Option<i32>) -> UnicastSignal {
    match errno {
        Some(EHOSTUNREACH) => UnicastSignal::Denied,
        // The peer answered (refused/reset), so the packet left this Mac.
        Some(ECONNREFUSED) | Some(ECONNRESET) => UnicastSignal::Granted,
        _ => UnicastSignal::Unknown,
    }
}

/// UDP sendto + TCP connect to the gateway. Returns the combined signal and
/// a short log line with the errno of each attempt.
fn unicast_gateway_probe(gateway: std::net::Ipv4Addr) -> (UnicastSignal, String) {
    use std::net::{SocketAddr, TcpStream, UdpSocket};
    let udp = UdpSocket::bind("0.0.0.0:0")
        .and_then(|socket| socket.send_to(&[0u8], SocketAddr::from((gateway, 9))));
    let udp_signal = match &udp {
        Ok(_) => UnicastSignal::Granted,
        Err(error) => classify_unicast_errno(error.raw_os_error()),
    };
    let tcp = TcpStream::connect_timeout(
        &SocketAddr::from((gateway, 80)),
        Duration::from_millis(1500),
    );
    let tcp_signal = match &tcp {
        Ok(_) => UnicastSignal::Granted,
        Err(error) => classify_unicast_errno(error.raw_os_error()),
    };
    let describe = |result: Result<(), &std::io::Error>| match result {
        Ok(()) => "ok".to_string(),
        Err(error) => format!("errno={:?} {}", error.raw_os_error(), error.kind()),
    };
    let line = format!(
        "unicast gateway={gateway} udp:9 {} ({}) tcp:80 {} ({})",
        describe(udp.as_ref().map(|_| ())),
        udp_signal.label(),
        describe(tcp.as_ref().map(|_| ())),
        tcp_signal.label(),
    );
    let signal = if udp_signal == UnicastSignal::Granted || tcp_signal == UnicastSignal::Granted {
        UnicastSignal::Granted
    } else if udp_signal == UnicastSignal::Denied || tcp_signal == UnicastSignal::Denied {
        UnicastSignal::Denied
    } else {
        UnicastSignal::Unknown
    };
    (signal, line)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Verdict {
    Granted,
    Denied,
    Unknown,
}

/// Any granted signal wins; scanning is blocked only when both signals deny.
fn combine_signals(native: i32, unicast: UnicastSignal) -> Verdict {
    if native == 0 || unicast == UnicastSignal::Granted {
        Verdict::Granted
    } else if native == 1 && unicast == UnicastSignal::Denied {
        Verdict::Denied
    } else {
        Verdict::Unknown
    }
}

/// Permission phase shown to the UI. A denial inside the first-launch prompt
/// window is `Pending`; a grant ends that window for good.
fn permission_phase(
    verdict: Verdict,
    now: std::time::Instant,
    prompt_deadline: &mut Option<std::time::Instant>,
) -> LocalNetworkStatus {
    match verdict {
        Verdict::Granted => {
            *prompt_deadline = None;
            LocalNetworkStatus::Granted
        }
        Verdict::Denied if prompt_deadline.is_some_and(|deadline| now < deadline) => {
            LocalNetworkStatus::Pending
        }
        Verdict::Denied => {
            *prompt_deadline = None;
            LocalNetworkStatus::Denied
        }
        Verdict::Unknown => LocalNetworkStatus::Unknown,
    }
}

fn native_label(native: i32) -> &'static str {
    match native {
        0 => "granted",
        1 => "denied",
        2 => "pending",
        _ => "unavailable",
    }
}

/// Appends diagnostics to ~/Library/Logs/Inet Viewer/probe.log (best effort).
fn write_probe_log(lines: &str) {
    use std::io::Write;
    let Some(home) = std::env::var_os("HOME") else {
        return;
    };
    let dir = Path::new(&home).join("Library/Logs/Inet Viewer");
    if std::fs::create_dir_all(&dir).is_err() {
        return;
    }
    let path = dir.join("probe.log");
    if std::fs::metadata(&path).is_ok_and(|meta| meta.len() > 1_000_000) {
        let _ = std::fs::remove_file(&path);
    }
    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let stamp = chrono::Local::now().format("%Y-%m-%d %H:%M:%S");
        for line in lines.lines().filter(|line| !line.trim().is_empty()) {
            let _ = writeln!(file, "{stamp} {line}");
        }
    }
}

fn classify_lan_probe_error(error: &str) -> (LocalNetworkStatus, &'static str) {
    let normalized = error.to_ascii_lowercase();
    if normalized.contains("permission denied") || normalized.contains("operation not permitted") {
        (LocalNetworkStatus::Denied, "permission_denied")
    } else if normalized.contains("no route to host") {
        // macOS reports a Local Network denial as EHOSTUNREACH.
        (LocalNetworkStatus::Denied, "no_route_local_network")
    } else if normalized.contains("network is unreachable") {
        (LocalNetworkStatus::Offline, "no_route")
    } else {
        (LocalNetworkStatus::ProbeFailed, "probe_failed")
    }
}

fn local_network_check(
    network: &MacOsNetwork,
    status: LocalNetworkStatus,
    code: &'static str,
    message: impl Into<String>,
    authorized: bool,
    native_probe: &'static str,
    unicast_probe: &'static str,
    lan_probe: &'static str,
) -> LocalNetworkCheck {
    LocalNetworkCheck {
        authorized,
        status,
        code,
        message: message.into(),
        interface: network.interface.clone(),
        private_ip: network.address.to_string(),
        cidr: network.cidr.clone(),
        gateway: network.gateway.to_string(),
        native_probe,
        unicast_probe,
        lan_probe,
    }
}

#[derive(Deserialize)]
struct DevicePatch {
    display_name: String,
    device_type: String,
}

fn error_message(error: impl std::fmt::Display) -> String {
    error.to_string()
}

#[tauri::command]
async fn desktop_status(state: State<'_, DesktopState>) -> Result<StatusResponse, String> {
    let devices = state
        .repository
        .list_devices()
        .await
        .map_err(error_message)?;
    let health = state
        .repository
        .load_health()
        .await
        .map_err(error_message)?;
    Ok(StatusResponse {
        status: if health.values().any(|item| !item.healthy) {
            "degraded"
        } else {
            "ok"
        },
        total_devices: devices.len(),
        online_devices: devices
            .iter()
            .filter(|item| item.status == DeviceStatus::Online)
            .count(),
        offline_devices: devices
            .iter()
            .filter(|item| item.status == DeviceStatus::Offline)
            .count(),
        netbird_devices: devices
            .iter()
            .filter(|item| item.sources.contains(&DiscoverySourceKind::Netbird))
            .count(),
        lan_only_devices: devices
            .iter()
            .filter(|item| {
                !item.sources.contains(&DiscoverySourceKind::Netbird)
                    && (!item.lan_ips.is_empty()
                        || item.sources.contains(&DiscoverySourceKind::Neighbor)
                        || item.sources.contains(&DiscoverySourceKind::Mdns))
            })
            .count(),
        mdns_devices: devices
            .iter()
            .filter(|item| item.sources.contains(&DiscoverySourceKind::Mdns))
            .count(),
        unhealthy_sources: health.values().filter(|item| !item.healthy).count(),
    })
}

#[tauri::command]
async fn desktop_devices(state: State<'_, DesktopState>) -> Result<DevicesResponse, String> {
    let devices = state
        .repository
        .list_devices()
        .await
        .map_err(error_message)?;
    let total = devices.len();
    Ok(DevicesResponse { devices, total })
}

#[tauri::command]
async fn desktop_sources(
    state: State<'_, DesktopState>,
) -> Result<Vec<DesktopSourceResponse>, String> {
    let health = state
        .repository
        .load_health()
        .await
        .map_err(error_message)?;
    let devices = state
        .repository
        .list_devices()
        .await
        .map_err(error_message)?;
    Ok([
        DiscoverySourceKind::Mdns,
        DiscoverySourceKind::Neighbor,
        DiscoverySourceKind::Netbird,
    ]
    .into_iter()
    .map(|source| {
        let item = health.get(&source);
        DesktopSourceResponse {
            source,
            healthy: item.is_some_and(|value| value.healthy),
            last_success_at: item.and_then(|value| value.last_success_at),
            last_error_at: item.and_then(|value| value.last_error_at),
            last_error: item.and_then(|value| value.last_error.clone()),
            interface: (source == DiscoverySourceKind::Neighbor)
                .then(|| state.network.interface.clone()),
            cidr: (source == DiscoverySourceKind::Neighbor).then(|| state.network.cidr.clone()),
            observation_count: devices
                .iter()
                .filter(|device| device.sources.contains(&source))
                .count(),
            detail: state
                .details
                .lock()
                .ok()
                .and_then(|map| map.get(&source).cloned()),
        }
    })
    .collect())
}

#[tauri::command]
async fn desktop_device(id: String, state: State<'_, DesktopState>) -> Result<Device, String> {
    state
        .repository
        .get_device(&id)
        .await
        .map_err(error_message)?
        .ok_or_else(|| "Cihaz bulunamadı.".into())
}

#[tauri::command]
async fn desktop_device_events(
    id: String,
    state: State<'_, DesktopState>,
) -> Result<Vec<DeviceEventRecord>, String> {
    state
        .repository
        .list_device_events(&id, 15)
        .await
        .map_err(error_message)
}

#[tauri::command]
async fn desktop_update_device(
    id: String,
    patch: DevicePatch,
    state: State<'_, DesktopState>,
) -> Result<Device, String> {
    let name = patch.display_name.trim();
    if name.chars().count() > 128 {
        return Err("Görünen ad en fazla 128 karakter olabilir.".into());
    }
    let kind = match patch.device_type.as_str() {
        "server" => DeviceType::Server,
        "computer" => DeviceType::Computer,
        "phone" => DeviceType::Phone,
        "tablet" => DeviceType::Tablet,
        "television" => DeviceType::Television,
        "printer" => DeviceType::Printer,
        "network" => DeviceType::Network,
        "iot" => DeviceType::Iot,
        "unknown" => DeviceType::Unknown,
        _ => return Err("Bilinmeyen cihaz türü.".into()),
    };
    state
        .repository
        .update_device_override(&id, Some((!name.is_empty()).then_some(name)), Some(kind))
        .await
        .map_err(error_message)?
        .ok_or_else(|| "Cihaz bulunamadı.".into())
}

#[tauri::command]
async fn desktop_device_ports(
    id: String,
    state: State<'_, DesktopState>,
) -> Result<Option<PortScan>, String> {
    state
        .repository
        .load_port_scan(&id)
        .await
        .map_err(error_message)
}

/// TCP connect scan of one device, only when the user asks for it.
#[tauri::command]
async fn desktop_scan_ports(
    id: String,
    state: State<'_, DesktopState>,
) -> Result<PortScan, String> {
    if !state.local_network_authorized.load(Ordering::Acquire) {
        return Err("Yerel Ağ izni doğrulanmadan port taraması yapılamaz.".into());
    }
    let device = state
        .repository
        .get_device(&id)
        .await
        .map_err(error_message)?
        .ok_or_else(|| "Cihaz bulunamadı.".to_string())?;
    let (addresses, ports) = device_scan_targets(&device);
    if addresses.is_empty() {
        return Err("Cihazın taranabilir IPv4 adresi yok.".into());
    }
    let report = scan_tcp_ports(&addresses, &ports, &PortScanConfig::default()).await;
    let scan = build_port_scan(&device, addresses, &report, chrono::Utc::now());
    write_probe_log(&format!(
        "ports device={id} addresses={:?} checked={} open={} [{}] elapsed={:.1}s",
        scan.addresses,
        scan.ports_checked,
        scan.open_ports.len(),
        scan.open_ports
            .iter()
            .map(|port| format!("{}:{}", port.address, port.port))
            .collect::<Vec<_>>()
            .join(" "),
        report.elapsed.as_secs_f32()
    ));
    state
        .repository
        .save_port_scan(&id, &scan)
        .await
        .map_err(error_message)?;
    Ok(scan)
}

/// Last progress of the running scan (fallback when events cannot be listened to).
#[tauri::command]
fn desktop_scan_progress(state: State<'_, DesktopState>) -> Option<serde_json::Value> {
    state.scans.progress()
}

#[tauri::command]
async fn desktop_start_scan(state: State<'_, DesktopState>) -> Result<ScanQueuedResponse, String> {
    if !state.local_network_authorized.load(Ordering::Acquire) {
        return Err("Yerel Ağ izni doğrulanmadan tarama başlatılamaz.".into());
    }
    state
        .scans
        .trigger()
        .await
        .map(|scan_id| ScanQueuedResponse { scan_id })
        .map_err(|error| match error {
            StartError::AlreadyRunning => "Bir tarama zaten çalışıyor.".into(),
            StartError::RateLimited => "Yeni tarama için birkaç saniye bekleyin.".into(),
        })
}

#[tauri::command]
async fn desktop_request_local_network(
    state: State<'_, DesktopState>,
) -> Result<LocalNetworkCheck, String> {
    let gateway = state.network.gateway;
    let ((native, native_log), (unicast, unicast_log)) =
        tauri::async_runtime::spawn_blocking(move || {
            (native_local_network_probe(8.0), unicast_gateway_probe(gateway))
        })
        .await
        .map_err(error_message)?;
    let verdict = combine_signals(native, unicast);
    let native_probe = native_label(native);
    let unicast_probe = unicast.label();
    let mut log = format!(
        "--- probe interface={} ip={} gateway={gateway}\n{native_log}{unicast_log}\nverdict={verdict:?}\n",
        state.network.interface, state.network.address
    );
    // Re-evaluated on every check so a later grant or revoke in System
    // Settings is picked up without restarting the app. Only a definite
    // denial blocks scanning.
    state
        .local_network_authorized
        .store(verdict != Verdict::Denied, Ordering::Release);
    if verdict == Verdict::Denied {
        write_probe_log(&log);
        return Ok(local_network_check(
            &state.network,
            LocalNetworkStatus::Denied,
            "tcc_denied",
            "macOS Yerel Ağ izni kapalı. Sistem Ayarları'ndan izin verip tekrar kontrol edin.",
            false,
            native_probe,
            unicast_probe,
            "not_run",
        ));
    }

    let probe = match state.local_probe.discover().await {
        Ok(run) if run.observations.is_empty() => "empty",
        Ok(_) => "success",
        Err(error) => {
            let detail = error.to_string();
            let (status, code) = classify_lan_probe_error(&detail);
            log.push_str(&format!("lan_probe failed: {detail}\n"));
            write_probe_log(&log);
            return Ok(local_network_check(
                &state.network,
                status,
                code,
                format!("Yerel ağ keşif probu hata verdi: {detail}"),
                true,
                native_probe,
                unicast_probe,
                "failed",
            ));
        }
    };
    log.push_str(&format!("lan_probe={probe}\n"));
    write_probe_log(&log);
    let (status, code, message) = if verdict == Verdict::Granted {
        (
            LocalNetworkStatus::Granted,
            "granted",
            "Yerel Ağ izni açık; fiziksel LAN rotası doğrulandı.",
        )
    } else {
        (
            LocalNetworkStatus::Unknown,
            "unverified",
            "Yerel Ağ izni doğrulanamadı; tarama yine de deneniyor.",
        )
    };
    Ok(local_network_check(
        &state.network,
        status,
        code,
        message,
        true,
        native_probe,
        unicast_probe,
        probe,
    ))
}

/// Light permission check (native probe + gateway unicast, no LAN sweep);
/// the UI polls it while waiting for the prompt or while the modal is open.
#[tauri::command]
async fn desktop_local_network_status(
    state: State<'_, DesktopState>,
) -> Result<LocalNetworkCheck, String> {
    let gateway = state.network.gateway;
    let ((native, native_log), (unicast, unicast_log)) =
        tauri::async_runtime::spawn_blocking(move || {
            (
                native_local_network_probe(STATUS_PROBE_TIMEOUT),
                unicast_gateway_probe(gateway),
            )
        })
        .await
        .map_err(error_message)?;
    let verdict = combine_signals(native, unicast);
    let status = {
        let mut deadline = state.prompt_deadline.lock().map_err(error_message)?;
        permission_phase(verdict, std::time::Instant::now(), &mut deadline)
    };
    let authorized = matches!(status, LocalNetworkStatus::Granted | LocalNetworkStatus::Unknown);
    let previous = state
        .local_network_authorized
        .swap(authorized, Ordering::AcqRel);
    if previous != authorized {
        write_probe_log(&format!(
            "--- status gateway={gateway}\n{native_log}{unicast_log}\nverdict={verdict:?} status={status:?}\n"
        ));
    }
    let (code, message) = match status {
        LocalNetworkStatus::Granted => ("granted", "Yerel Ağ izni açık."),
        LocalNetworkStatus::Pending => ("prompt_pending", "Yerel Ağ izni bekleniyor."),
        LocalNetworkStatus::Denied => ("tcc_denied", "macOS Yerel Ağ izni kapalı."),
        _ => ("unverified", "Yerel Ağ izni doğrulanamadı; tarama yine de deneniyor."),
    };
    Ok(local_network_check(
        &state.network,
        status,
        code,
        message,
        authorized,
        native_label(native),
        unicast.label(),
        "not_run",
    ))
}

#[tauri::command]
fn desktop_environment() -> DesktopEnvironment {
    let netbird = netbird_path();
    DesktopEnvironment {
        netbird_installed: netbird.is_some(),
        netbird_path: netbird.map(|path| path.display().to_string()),
        local_database: true,
    }
}

#[tauri::command]
fn desktop_open_local_network_settings() -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let status = std::process::Command::new("/usr/bin/open")
            .arg(LOCAL_NETWORK_SETTINGS_URL)
            .status()
            .map_err(error_message)?;
        status
            .success()
            .then_some(())
            .ok_or_else(|| "Sistem Ayarları açılamadı.".into())
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = LOCAL_NETWORK_SETTINGS_URL;
        Err("Yerel Ağ ayarı yalnızca macOS'ta vardır.".into())
    }
}

fn netbird_path() -> Option<std::path::PathBuf> {
    ["/opt/homebrew/bin/netbird", "/usr/local/bin/netbird"]
        .into_iter()
        .map(std::path::PathBuf::from)
        .find(|path| path.is_file())
}

fn providers(
    local_probe: Arc<dyn DiscoveryProvider>,
    network: &MacOsNetwork,
    details: &SourceDetails,
) -> Vec<Arc<dyn DiscoveryProvider>> {
    let mut providers = vec![
        local_probe,
        Arc::new(DesktopMdnsProvider {
            network: network.clone(),
            details: Arc::clone(details),
        }) as Arc<dyn DiscoveryProvider>,
    ];
    if let Some(path) = netbird_path() {
        if let Ok(provider) = NetbirdProvider::new(path) {
            providers.push(Arc::new(provider));
        }
    }
    providers
}

/// Background status refresh interval while the window is visible.
const REFRESH_VISIBLE: Duration = Duration::from_secs(30);
/// Interval while the window is hidden or minimized.
const REFRESH_HIDDEN: Duration = Duration::from_secs(120);
const REFRESH_TICK: Duration = Duration::from_secs(5);

fn refresh_due(since_last: Duration, visible: bool) -> bool {
    since_last >= if visible { REFRESH_VISIBLE } else { REFRESH_HIDDEN }
}

fn main() {
    let app =
        tauri::Builder::default()
            .setup(|app| {
                let network = tauri::async_runtime::block_on(detect_macos_network(
                    Path::new("/sbin/route"),
                    Path::new("/usr/sbin/ipconfig"),
                ))?;
                let details = SourceDetails::default();
                let local_probe: Arc<dyn DiscoveryProvider> = Arc::new(
                    DesktopNeighborProvider::new(network.clone(), Arc::clone(&details))?,
                );
                let local_network_authorized = Arc::new(AtomicBool::new(false));
                let data_dir = app.path().app_data_dir()?;
                std::fs::create_dir_all(&data_dir)?;
                let database = data_dir.join("inet-viewer.sqlite");
                // No database yet = first launch: macOS shows its one-time
                // Local Network prompt now, so early denials mean "pending".
                let prompt_deadline = (!database.exists())
                    .then(|| std::time::Instant::now() + PROMPT_GRACE);
                // Trigger the OS prompt from the main app process right away.
                tauri::async_runtime::spawn_blocking(|| {
                    let (code, log) = native_local_network_probe(8.0);
                    write_probe_log(&format!("--- startup probe result={code}\n{log}"));
                });
                let database_url = format!("sqlite://{}", database.display());
                let pool = tauri::async_runtime::block_on(inet_viewer_server::connect_database(
                    &database_url,
                ))?;
                let repository = Repository::new(pool);
                let events = EventBus::default();
                let scans = ScanService::new(
                    repository.clone(),
                    DiscoveryRunner::new(providers(Arc::clone(&local_probe), &network, &details)),
                    events.clone(),
                    Duration::from_secs(5),
                );
                // Forward scan lifecycle/progress (already throttled by the
                // scan service) to the webview as `scan-progress`.
                let mut scan_events = events.subscribe();
                let emitter = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    use tokio::sync::broadcast::error::RecvError;
                    loop {
                        match scan_events.recv().await {
                            Ok(event) => {
                                if event.event_type == "snapshot.updated" {
                                    let _ = emitter.emit("devices-changed", event.data);
                                    continue;
                                }
                                let Some(kind) = event.event_type.strip_prefix("scan.") else {
                                    continue;
                                };
                                let mut payload = event.data;
                                if let Some(object) = payload.as_object_mut() {
                                    object.insert("kind".into(), kind.into());
                                }
                                let _ = emitter.emit("scan-progress", payload);
                            }
                            Err(RecvError::Lagged(_)) => continue,
                            Err(RecvError::Closed) => break,
                        }
                    }
                });
                // Full scan runs once at startup (triggered by the UI after the
                // Local Network check) and on "Şimdi tara". In between, a silent
                // status refresh probes only known devices + `netbird status`.
                let refresher = StatusRefresher::new(
                    repository.clone(),
                    events.clone(),
                    Arc::new(LivenessReachability::default()),
                    netbird_path()
                        .and_then(|path| NetbirdProvider::new(path).ok())
                        .map(|provider| Arc::new(provider) as Arc<dyn DiscoveryProvider>),
                );
                let refresh_scans = scans.clone();
                let refresh_authorized = Arc::clone(&local_network_authorized);
                let refresh_handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    let mut last_run = tokio::time::Instant::now();
                    loop {
                        tokio::time::sleep(REFRESH_TICK).await;
                        let visible = refresh_handle
                            .webview_windows()
                            .values()
                            .any(|window| {
                                window.is_visible().unwrap_or(true)
                                    && !window.is_minimized().unwrap_or(false)
                            });
                        if !refresh_due(last_run.elapsed(), visible) || refresh_scans.is_running().await {
                            continue;
                        }
                        last_run = tokio::time::Instant::now();
                        let lan_allowed = refresh_authorized.load(Ordering::Acquire);
                        if let Err(error) = refresher.refresh(lan_allowed).await {
                            write_probe_log(&format!("refresh failed: {error}"));
                        }
                    }
                });
                app.manage(DesktopState {
                    repository,
                    scans,
                    local_network_authorized,
                    local_probe,
                    network,
                    details,
                    prompt_deadline: std::sync::Mutex::new(prompt_deadline),
                });
                Ok(())
            })
            .invoke_handler(tauri::generate_handler![
                desktop_status,
                desktop_devices,
                desktop_sources,
                desktop_device,
                desktop_device_events,
                desktop_update_device,
                desktop_device_ports,
                desktop_scan_ports,
                desktop_start_scan,
                desktop_scan_progress,
                desktop_request_local_network,
                desktop_local_network_status,
                desktop_open_local_network_settings,
                desktop_environment,
            ])
            .build(tauri::generate_context!())
            .expect("inet-viewer masaüstü uygulaması başlatılamadı");

    app.run(|handle, event| match event {
        RunEvent::Reopen { .. } => {
            if let Some(window) = handle.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }
        // Back from System Settings: the UI re-checks the permission.
        RunEvent::WindowEvent {
            event: tauri::WindowEvent::Focused(true),
            ..
        } => {
            let _ = handle.emit("window-focused", ());
        }
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_route_is_classified_as_local_network_denial() {
        assert_eq!(
            classify_lan_probe_error("No route to host (os error 65)"),
            (LocalNetworkStatus::Denied, "no_route_local_network")
        );
        assert_eq!(
            classify_lan_probe_error("Network is unreachable (os error 51)"),
            (LocalNetworkStatus::Offline, "no_route")
        );
    }

    #[test]
    fn background_refresh_slows_down_when_hidden() {
        assert!(!refresh_due(Duration::from_secs(29), true));
        assert!(refresh_due(Duration::from_secs(30), true));
        assert!(!refresh_due(Duration::from_secs(60), false));
        assert!(refresh_due(Duration::from_secs(120), false));
    }

    #[test]
    fn lan_targets_skip_network_broadcast_and_self() {
        let network = MacOsNetwork {
            interface: "en0".into(),
            address: std::net::Ipv4Addr::new(192, 168, 0, 103),
            cidr: "192.168.0.0/24".into(),
            gateway: std::net::Ipv4Addr::new(192, 168, 0, 1),
        };
        let targets = lan_targets(&network);
        assert_eq!(targets.len(), 253);
        assert_eq!(targets[0], std::net::Ipv4Addr::new(192, 168, 0, 1));
        assert_eq!(targets[252], std::net::Ipv4Addr::new(192, 168, 0, 254));
        assert!(!targets.contains(&network.address));
    }

    #[test]
    fn unicast_errno_classification() {
        assert_eq!(classify_unicast_errno(Some(65)), UnicastSignal::Denied);
        assert_eq!(classify_unicast_errno(Some(61)), UnicastSignal::Granted);
        assert_eq!(classify_unicast_errno(Some(60)), UnicastSignal::Unknown);
        assert_eq!(classify_unicast_errno(None), UnicastSignal::Unknown);
    }

    #[test]
    fn any_granted_signal_wins_and_only_double_denial_blocks() {
        assert_eq!(combine_signals(1, UnicastSignal::Granted), Verdict::Granted);
        assert_eq!(combine_signals(0, UnicastSignal::Denied), Verdict::Granted);
        assert_eq!(combine_signals(1, UnicastSignal::Denied), Verdict::Denied);
        assert_eq!(combine_signals(1, UnicastSignal::Unknown), Verdict::Unknown);
        assert_eq!(combine_signals(2, UnicastSignal::Denied), Verdict::Unknown);
    }

    #[test]
    fn every_browsed_mdns_type_is_declared_in_info_plist() {
        let declared = plist_bonjour_types();
        assert!(declared.iter().any(|ty| ty == "_services._dns-sd._udp"));
        for ty in inet_viewer_server::discovery::MDNS_BROWSE_TYPES {
            assert!(declared.iter().any(|item| item == ty), "{ty} missing in Info.plist");
        }
    }

    #[test]
    fn first_launch_denial_is_pending_until_the_prompt_window_ends() {
        let start = std::time::Instant::now();
        let mut deadline = Some(start + PROMPT_GRACE);
        assert_eq!(
            permission_phase(Verdict::Denied, start, &mut deadline),
            LocalNetworkStatus::Pending
        );
        assert_eq!(
            permission_phase(Verdict::Granted, start + Duration::from_secs(5), &mut deadline),
            LocalNetworkStatus::Granted
        );
        // A grant ends the prompt window: a later revoke is a real denial.
        assert_eq!(deadline, None);
        assert_eq!(
            permission_phase(Verdict::Denied, start + Duration::from_secs(6), &mut deadline),
            LocalNetworkStatus::Denied
        );
    }

    #[test]
    fn denial_after_the_prompt_window_or_on_later_launches_is_denied() {
        let start = std::time::Instant::now();
        let mut deadline = Some(start + PROMPT_GRACE);
        assert_eq!(
            permission_phase(Verdict::Denied, start + PROMPT_GRACE, &mut deadline),
            LocalNetworkStatus::Denied
        );
        let mut later_launch = None;
        assert_eq!(
            permission_phase(Verdict::Denied, start, &mut later_launch),
            LocalNetworkStatus::Denied
        );
        assert_eq!(
            permission_phase(Verdict::Unknown, start, &mut later_launch),
            LocalNetworkStatus::Unknown
        );
    }

    #[test]
    fn settings_url_opens_the_local_network_pane() {
        assert_eq!(
            LOCAL_NETWORK_SETTINGS_URL,
            "x-apple.systempreferences:com.apple.preference.security?Privacy_LocalNetwork"
        );
    }

    #[test]
    fn permission_errors_remain_distinct() {
        assert_eq!(
            classify_lan_probe_error("Operation not permitted (os error 1)"),
            (LocalNetworkStatus::Denied, "permission_denied")
        );
    }
}
