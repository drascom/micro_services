use super::{normalize_hostname, push_ip, warning, ParseOutput};
use chrono::{DateTime, Utc};
use inet_viewer_shared::{DiscoverySourceKind, Observation, ObservationStatus};
use std::{
    collections::{BTreeMap, HashMap},
    net::{IpAddr, Ipv4Addr},
    time::Duration,
};

/// A WireGuard handshake at most this old means the peer is up right now
/// (WireGuard re-handshakes every ~2 minutes while traffic flows).
pub const RECENT_HANDSHAKE: Duration = Duration::from_secs(180);

/// Parses NetBird's relative times ("10 seconds ago", "1 minute ago",
/// "2 hours, 3 minutes ago", "now"); `-` or anything else is `None`.
pub fn parse_handshake_age(value: &str) -> Option<Duration> {
    let value = value.trim().to_ascii_lowercase();
    if value == "now" || value == "just now" || value == "a moment ago" {
        return Some(Duration::ZERO);
    }
    let value = value.strip_suffix("ago")?.trim();
    let mut words = value.split(|c: char| c.is_whitespace() || c == ',').filter(|w| !w.is_empty());
    let mut total = 0u64;
    let mut found = false;
    while let Some(amount) = words.next() {
        let amount: u64 = match amount {
            "a" | "an" => 1,
            other => other.parse().ok()?,
        };
        let unit = words.next()?;
        let seconds = match unit.trim_end_matches('s') {
            "second" | "sec" => 1,
            "minute" | "min" => 60,
            "hour" => 3600,
            "day" => 86_400,
            "week" => 604_800,
            "month" => 2_592_000,
            "year" => 31_536_000,
            _ => return None,
        };
        total = total.saturating_add(amount.saturating_mul(seconds));
        found = true;
    }
    found.then(|| Duration::from_secs(total))
}

/// Effective status of a NetBird peer.
///
/// `Connected` is online. `Connecting` and `Idle` are what NetBird keeps
/// showing for peers that are simply offline, so they only count as online
/// when the last WireGuard handshake is recent or the peer answered an
/// ICMP/TCP check from this process; otherwise they are offline.
/// `Disconnected` is offline unless the peer answers. `Unknown` (e.g. the
/// local peer) stays unknown unless it answers.
pub fn netbird_effective_status(
    raw: ObservationStatus,
    handshake_age: Option<Duration>,
    reachable: bool,
) -> ObservationStatus {
    match raw {
        ObservationStatus::Online => ObservationStatus::Online,
        _ if reachable => ObservationStatus::Online,
        ObservationStatus::Connecting | ObservationStatus::Idle
            if handshake_age.is_some_and(|age| age <= RECENT_HANDSHAKE) =>
        {
            ObservationStatus::Online
        }
        ObservationStatus::Unknown => ObservationStatus::Unknown,
        _ => ObservationStatus::Offline,
    }
}

/// Applies [`netbird_effective_status`] to parsed NetBird observations;
/// `reachable` holds the peers that answered a liveness check. The raw NetBird
/// status is kept in the `netbird_status` attribute.
pub fn resolve_netbird_statuses<M>(
    observations: &mut [Observation],
    reachable: &BTreeMap<Ipv4Addr, M>,
) where
    M: std::fmt::Display,
{
    for observation in observations
        .iter_mut()
        .filter(|observation| observation.source == DiscoverySourceKind::Netbird)
    {
        let answered = observation.ip_addresses.iter().find_map(|address| match address {
            IpAddr::V4(v4) => reachable.get(v4),
            IpAddr::V6(_) => None,
        });
        if let Some(method) = answered {
            observation
                .attributes
                .insert("liveness".into(), method.to_string());
        }
        let handshake = observation
            .attributes
            .get("last_wireguard_handshake")
            .and_then(|value| parse_handshake_age(value));
        let raw = observation.status;
        observation
            .attributes
            .entry("netbird_status".into())
            .or_insert_with(|| format!("{raw:?}").to_ascii_lowercase());
        observation.status = netbird_effective_status(raw, handshake, answered.is_some());
    }
}

#[derive(Default)]
struct Peer {
    hostname: Option<String>,
    ips: Vec<std::net::IpAddr>,
    source_id: Option<String>,
    status: Option<ObservationStatus>,
    attributes: HashMap<String, String>,
}

impl Peer {
    fn finish(self, observed_at: DateTime<Utc>, output: &mut ParseOutput) {
        if self.hostname.is_none() && self.ips.is_empty() {
            return;
        }
        output.observations.push(Observation {
            source: DiscoverySourceKind::Netbird,
            source_id: self.source_id,
            hostname: self.hostname,
            ip_addresses: self.ips,
            mac_address: None,
            manufacturer: None,
            model: None,
            status: self.status.unwrap_or(ObservationStatus::Unknown),
            services: Vec::new(),
            observed_at,
            attributes: self.attributes,
        });
    }
}

pub fn parse_netbird_status(input: &str, observed_at: DateTime<Utc>) -> ParseOutput {
    let mut output = ParseOutput::default();
    let mut in_peers = false;
    let mut current: Option<Peer> = None;

    for (index, raw) in input.lines().enumerate() {
        let line_number = index + 1;
        let trimmed = raw.trim();
        if trimmed.is_empty() || trimmed == "-- detail --" {
            continue;
        }
        if trimmed == "Peers detail:" {
            in_peers = true;
            continue;
        }
        if in_peers && !raw.starts_with(char::is_whitespace) {
            if let Some(peer) = current.take() {
                peer.finish(observed_at, &mut output);
            }
            if trimmed == "Events:" {
                in_peers = false;
                continue;
            }
        }
        if !in_peers {
            continue;
        }
        if raw.starts_with(' ') && !raw.starts_with("  ") && trimmed.ends_with(':') {
            if let Some(peer) = current.take() {
                peer.finish(observed_at, &mut output);
            }
            current = Some(Peer {
                hostname: normalize_hostname(trimmed.trim_end_matches(':')),
                ..Peer::default()
            });
            continue;
        }
        let Some(peer) = current.as_mut() else {
            output.warnings.push(warning(
                line_number,
                "content before first NetBird peer",
                raw,
            ));
            continue;
        };
        if let Some(value) = trimmed.strip_prefix("Transfer status (received/sent) ") {
            peer.attributes
                .insert("transfer_status".into(), value.into());
            continue;
        }
        let Some((key, value)) = trimmed.split_once(':') else {
            output
                .warnings
                .push(warning(line_number, "malformed NetBird peer line", raw));
            continue;
        };
        let key = key.trim();
        let value = value.trim();
        match key {
            "NetBird IP" | "NetBird IPv6" => {
                if !push_ip(&mut peer.ips, value) {
                    output
                        .warnings
                        .push(warning(line_number, format!("invalid {key}"), raw));
                }
            }
            "Public key" => {
                peer.source_id = (!value.is_empty() && value != "-").then(|| value.into())
            }
            "Status" => {
                peer.status = Some(match value.to_ascii_lowercase().as_str() {
                    "connected" => ObservationStatus::Online,
                    "connecting" => ObservationStatus::Connecting,
                    "disconnected" => ObservationStatus::Offline,
                    "idle" => ObservationStatus::Idle,
                    _ => {
                        output.warnings.push(warning(
                            line_number,
                            format!("unknown NetBird status {value}"),
                            raw,
                        ));
                        ObservationStatus::Unknown
                    }
                });
            }
            "Connection type"
            | "ICE candidate (Local/Remote)"
            | "ICE candidate endpoints (Local/Remote)"
            | "Relay server address"
            | "Last connection update"
            | "Last WireGuard handshake"
            | "Quantum resistance"
            | "Networks"
            | "Latency" => {
                peer.attributes
                    .insert(key.to_ascii_lowercase().replace(' ', "_"), value.into());
            }
            _ => output.warnings.push(warning(
                line_number,
                format!("unknown NetBird field {key}"),
                raw,
            )),
        }
    }
    if let Some(peer) = current {
        peer.finish(observed_at, &mut output);
    }
    parse_local_peer(input, observed_at, &mut output);
    for observation in &output.observations {
        if observation.hostname.is_none() {
            output
                .warnings
                .push(warning(0, "NetBird peer has no hostname", ""));
        }
    }
    output
}

fn parse_local_peer(input: &str, observed_at: DateTime<Utc>, output: &mut ParseOutput) {
    let Some(summary) = input.lines().position(|line| line.starts_with("OS:")) else {
        return;
    };
    let mut peer = Peer {
        status: Some(ObservationStatus::Unknown),
        ..Peer::default()
    };
    for raw in input.lines().skip(summary) {
        let Some((key, value)) = raw.trim().split_once(':') else {
            continue;
        };
        let value = value.trim();
        match key {
            "FQDN" => peer.hostname = normalize_hostname(value),
            "NetBird IP" | "NetBird IPv6" => {
                if !push_ip(&mut peer.ips, value) {
                    output
                        .warnings
                        .push(warning(summary + 1, format!("invalid local {key}"), raw));
                }
            }
            "Management" | "Signal" => {
                if value.to_ascii_lowercase().starts_with("connected") {
                    peer.status = Some(ObservationStatus::Online);
                }
                peer.attributes
                    .insert(key.to_ascii_lowercase(), value.to_owned());
            }
            "OS" | "Daemon version" | "CLI version" | "Profile" | "Interface type"
            | "Wireguard port" | "Quantum resistance" | "Lazy connection" | "SSH Server"
            | "Networks" | "Peers count" => {
                peer.attributes
                    .insert(key.to_ascii_lowercase().replace(' ', "_"), value.to_owned());
            }
            _ => {}
        }
    }
    peer.finish(observed_at, output);
}
