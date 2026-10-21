use inet_viewer_shared::{
    Device, DeviceId, DeviceName, DeviceStatus, DeviceType, DiscoverySourceKind, MacAddress, Observation,
    ObservationStatus, Service,
};
use std::{collections::HashSet, net::IpAddr};

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum MatchReason {
    SourceIdentity {
        source: DiscoverySourceKind,
        id: String,
    },
    GlobalMac(MacAddress),
    MdnsIdentity(String),
    HostnameAndAddress {
        hostname: String,
        address: IpAddr,
    },
    HostnameAndModel {
        hostname: String,
    },
    /// Two LAN observations (e.g. liveness and mDNS) of the same IPv4 address.
    SharedLanAddress(IpAddr),
}

#[derive(Clone, Debug)]
pub struct MergedDevice {
    pub device: Device,
    pub observations: Vec<Observation>,
    pub evidence: Vec<MatchReason>,
}

pub fn merge_observations(observations: Vec<Observation>) -> Vec<MergedDevice> {
    let count = observations.len();
    let mut parent: Vec<usize> = (0..count).collect();
    let mut reasons = Vec::new();
    for left in 0..count {
        for right in left + 1..count {
            if let Some(reason) = match_reason(&observations[left], &observations[right]) {
                union(&mut parent, left, right);
                reasons.push((left, right, reason));
            }
        }
    }

    let mut roots = Vec::<usize>::new();
    let mut groups: Vec<Vec<Observation>> = Vec::new();
    for (index, observation) in observations.into_iter().enumerate() {
        let root = find(&mut parent, index);
        let group = match roots.iter().position(|candidate| *candidate == root) {
            Some(group) => group,
            None => {
                roots.push(root);
                groups.push(Vec::new());
                groups.len() - 1
            }
        };
        groups[group].push(observation);
    }

    groups
        .into_iter()
        .zip(roots)
        .map(|(group, root)| {
            let evidence = reasons
                .iter()
                .filter(|(left, _, _)| find(&mut parent, *left) == root)
                .map(|(_, _, reason)| reason.clone())
                .collect();
            build_device(group, evidence)
        })
        .collect()
}

fn match_reason(left: &Observation, right: &Observation) -> Option<MatchReason> {
    if left.source == right.source {
        if let (Some(left_id), Some(right_id)) = (&left.source_id, &right.source_id) {
            if left_id.eq_ignore_ascii_case(right_id) {
                return Some(MatchReason::SourceIdentity {
                    source: left.source,
                    id: left_id.clone(),
                });
            }
        }
    }

    if let (Some(left_mac), Some(right_mac)) = (&left.mac_address, &right.mac_address) {
        if left_mac == right_mac && is_global_mac(left_mac) {
            return Some(MatchReason::GlobalMac(left_mac.clone()));
        }
    }

    if left.source == DiscoverySourceKind::Mdns && right.source == DiscoverySourceKind::Mdns {
        if let (Some(left_id), Some(right_id)) = (&left.source_id, &right.source_id) {
            if left_id.eq_ignore_ascii_case(right_id) {
                return Some(MatchReason::MdnsIdentity(left_id.clone()));
            }
        }
    }

    let Some(hostname) = common_hostname(left, right) else {
        return shared_lan_address(left, right).map(MatchReason::SharedLanAddress);
    };
    if let Some(address) = left
        .ip_addresses
        .iter()
        .find(|address| right.ip_addresses.contains(address))
    {
        return Some(MatchReason::HostnameAndAddress {
            hostname,
            address: *address,
        });
    }
    let same_model = equal_present(&left.model, &right.model);
    let same_manufacturer = equal_present(&left.manufacturer, &right.manufacturer);
    (same_model && same_manufacturer).then_some(MatchReason::HostnameAndModel { hostname })
}

/// LAN sources that see the same IPv4 address describe the same host, unless
/// both come from one source and carry different names.
fn shared_lan_address(left: &Observation, right: &Observation) -> Option<IpAddr> {
    if left.source == DiscoverySourceKind::Netbird || right.source == DiscoverySourceKind::Netbird {
        return None;
    }
    if left.source == right.source && left.hostname.is_some() && right.hostname.is_some() {
        return None;
    }
    left.ip_addresses
        .iter()
        .find(|address| address.is_ipv4() && right.ip_addresses.contains(address))
        .copied()
}

fn common_hostname(left: &Observation, right: &Observation) -> Option<String> {
    let left = left.hostname.as_deref()?.trim_end_matches('.');
    let right = right.hostname.as_deref()?.trim_end_matches('.');
    left.eq_ignore_ascii_case(right)
        .then(|| left.to_ascii_lowercase())
}

fn equal_present(left: &Option<String>, right: &Option<String>) -> bool {
    matches!((left, right), (Some(left), Some(right)) if left.eq_ignore_ascii_case(right))
}

fn is_global_mac(mac: &MacAddress) -> bool {
    mac.to_string()
        .get(0..2)
        .and_then(|value| u8::from_str_radix(value, 16).ok())
        .is_some_and(|first| first & 0x02 == 0)
}

fn find(parent: &mut [usize], index: usize) -> usize {
    if parent[index] != index {
        parent[index] = find(parent, parent[index]);
    }
    parent[index]
}

fn union(parent: &mut [usize], left: usize, right: usize) {
    let left = find(parent, left);
    let right = find(parent, right);
    if left != right {
        parent[right] = left;
    }
}

fn build_device(mut observations: Vec<Observation>, evidence: Vec<MatchReason>) -> MergedDevice {
    observations
        .sort_by_key(|observation| (observation.observed_at, source_rank(observation.source)));
    let first_seen_at = observations
        .iter()
        .map(|observation| observation.observed_at)
        .min()
        .expect("a merged group is never empty");
    let last_seen_at = observations
        .iter()
        .map(|observation| observation.observed_at)
        .max()
        .expect("a merged group is never empty");

    let mut hostnames = Vec::new();
    let mut names: Vec<DeviceName> = Vec::new();
    let mut lan_ips = Vec::new();
    let mut netbird_ip = None;
    let mut netbird_ipv6 = None;
    let mut mac_addresses = Vec::new();
    let mut sources = Vec::new();
    let mut services = Vec::new();
    let mut manufacturer = None;
    let mut model = None;
    let mut status = DeviceStatus::Unknown;

    for observation in &observations {
        push_unique_option(&mut hostnames, observation.hostname.clone());
        for name in observation_names(observation) {
            push_unique(&mut names, name);
        }
        push_unique_option(&mut mac_addresses, observation.mac_address.clone());
        push_unique(&mut sources, observation.source);
        for address in &observation.ip_addresses {
            if observation.source == DiscoverySourceKind::Netbird {
                match address {
                    IpAddr::V4(_) if netbird_ip.is_none() => netbird_ip = Some(*address),
                    IpAddr::V6(_) if netbird_ipv6.is_none() => netbird_ipv6 = Some(*address),
                    _ => {}
                }
            } else {
                push_unique(&mut lan_ips, *address);
            }
        }
        for service in &observation.services {
            if !services.contains(service) {
                services.push(service.clone());
            }
        }
        if manufacturer.is_none() {
            manufacturer.clone_from(&observation.manufacturer);
        }
        if model.is_none() {
            model.clone_from(&observation.model);
        }
        status = status.max(status_from_observation(observation.status));
    }

    let display_name = observations
        .iter()
        .filter(|observation| observation.source == DiscoverySourceKind::Mdns)
        .flat_map(|observation| observation.services.iter())
        .find_map(|service| service.name.clone())
        .or_else(|| {
            names
                .iter()
                .find(|name| name.source == "ssdp")
                .map(|name| name.name.clone())
        })
        .or_else(|| best_hostname(&names))
        .or_else(|| hostnames.first().cloned())
        .or_else(|| lan_ips.first().map(ToString::to_string))
        .or_else(|| netbird_ip.map(|address| address.to_string()))
        .unwrap_or_else(|| "Unknown device".into());
    let device_type = classify_device(&services);

    MergedDevice {
        device: Device {
            id: DeviceId::new(),
            display_name,
            hostnames,
            names,
            lan_ips,
            netbird_ip,
            netbird_ipv6,
            mac_addresses,
            manufacturer,
            model,
            device_type,
            status,
            sources,
            services,
            first_seen_at,
            last_seen_at,
        },
        observations,
        evidence,
    }
}

/// Names an observation carries: `name.<method>` attributes plus its hostname.
pub fn observation_names(observation: &Observation) -> Vec<DeviceName> {
    let mut names: Vec<DeviceName> = observation
        .attributes
        .iter()
        .filter_map(|(key, value)| {
            let source = key.strip_prefix("name.")?;
            (!value.trim().is_empty()).then(|| DeviceName {
                name: value.trim().to_owned(),
                source: source.to_owned(),
            })
        })
        .collect();
    names.sort_by(|left, right| {
        name_rank(&left.source)
            .cmp(&name_rank(&right.source))
            .then_with(|| left.name.cmp(&right.name))
    });
    if let Some(hostname) = &observation.hostname {
        if !names.iter().any(|name| name.name.eq_ignore_ascii_case(hostname)) {
            let source = observation
                .attributes
                .get("hostname_source")
                .cloned()
                .unwrap_or_else(|| source_label(observation.source).to_owned());
            names.push(DeviceName {
                name: hostname.clone(),
                source,
            });
        }
    }
    names
}

/// Preferred hostname method: mDNS, NetBIOS, NetBird, reverse DNS.
fn name_rank(source: &str) -> u8 {
    match source {
        "mdns" => 0,
        "netbios" => 1,
        "netbird" => 2,
        "ptr" => 3,
        "ssdp" => 5,
        _ => 4,
    }
}

fn best_hostname(names: &[DeviceName]) -> Option<String> {
    names
        .iter()
        .filter(|name| name.source != "ssdp")
        .min_by_key(|name| name_rank(&name.source))
        .map(|name| name.name.clone())
}

fn source_label(source: DiscoverySourceKind) -> &'static str {
    match source {
        DiscoverySourceKind::Netbird => "netbird",
        DiscoverySourceKind::Neighbor => "neighbor",
        DiscoverySourceKind::Mdns => "mdns",
    }
}

fn status_from_observation(status: ObservationStatus) -> DeviceStatus {
    match status {
        ObservationStatus::Online => DeviceStatus::Online,
        ObservationStatus::Idle => DeviceStatus::Idle,
        // "Connecting" (NetBird peer that never answers, ARP INCOMPLETE) is
        // not evidence of a live host.
        ObservationStatus::Connecting => DeviceStatus::Offline,
        ObservationStatus::Offline => DeviceStatus::Offline,
        ObservationStatus::Unknown => DeviceStatus::Unknown,
    }
}

fn source_rank(source: DiscoverySourceKind) -> u8 {
    match source {
        DiscoverySourceKind::Netbird => 0,
        DiscoverySourceKind::Neighbor => 1,
        DiscoverySourceKind::Mdns => 2,
    }
}

fn push_unique<T: PartialEq>(values: &mut Vec<T>, value: T) {
    if !values.contains(&value) {
        values.push(value);
    }
}

fn push_unique_option<T: PartialEq>(values: &mut Vec<T>, value: Option<T>) {
    if let Some(value) = value {
        push_unique(values, value);
    }
}

fn classify_device(services: &[Service]) -> DeviceType {
    let types: HashSet<_> = services
        .iter()
        .map(|service| service.service_type.as_str())
        .collect();
    if types.contains("_ipp._tcp") || types.contains("_ipps._tcp") {
        DeviceType::Printer
    } else if types.contains("_googlecast._tcp") || types.contains("_airplay._tcp") {
        DeviceType::Television
    } else if types.contains("_smb._tcp") || types.contains("_afpovertcp._tcp") {
        DeviceType::Server
    } else if types.contains("_hap._tcp")
        || types.contains("_matter._tcp")
        || types.contains("_sonos._tcp")
    {
        DeviceType::Iot
    } else {
        DeviceType::Unknown
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{TimeZone, Utc};
    use std::{collections::HashMap, str::FromStr};

    fn observation(source: DiscoverySourceKind, hostname: &str, ip: &str) -> Observation {
        Observation {
            source,
            source_id: None,
            hostname: Some(hostname.into()),
            ip_addresses: vec![ip.parse().unwrap()],
            mac_address: None,
            manufacturer: None,
            model: None,
            status: ObservationStatus::Online,
            services: Vec::new(),
            observed_at: Utc.with_ymd_and_hms(2026, 1, 1, 0, 0, 0).unwrap(),
            attributes: HashMap::new(),
        }
    }

    #[test]
    fn hostname_and_overlapping_ip_merge() {
        let merged = merge_observations(vec![
            observation(
                DiscoverySourceKind::Neighbor,
                "server.internal",
                "192.0.2.1",
            ),
            observation(DiscoverySourceKind::Mdns, "SERVER.INTERNAL.", "192.0.2.1"),
        ]);
        assert_eq!(merged.len(), 1);
        assert!(matches!(
            merged[0].evidence[0],
            MatchReason::HostnameAndAddress { .. }
        ));
    }

    #[test]
    fn netbird_and_lan_observations_merge_with_shared_identity() {
        let merged = merge_observations(vec![
            observation(DiscoverySourceKind::Netbird, "server.internal", "192.0.2.1"),
            observation(
                DiscoverySourceKind::Neighbor,
                "SERVER.INTERNAL.",
                "192.0.2.1",
            ),
        ]);
        assert_eq!(merged.len(), 1);
        assert_eq!(
            merged[0].device.sources,
            vec![DiscoverySourceKind::Netbird, DiscoverySourceKind::Neighbor]
        );
        assert!(matches!(
            merged[0].evidence[0],
            MatchReason::HostnameAndAddress { .. }
        ));
    }

    #[test]
    fn locally_administered_mac_alone_does_not_merge() {
        let mut left = observation(DiscoverySourceKind::Neighbor, "one", "192.0.2.1");
        let mut right = observation(DiscoverySourceKind::Mdns, "two", "192.0.2.2");
        left.mac_address = Some(MacAddress::from_str("02:00:00:00:00:01").unwrap());
        right.mac_address = left.mac_address.clone();
        assert_eq!(merge_observations(vec![left, right]).len(), 2);
    }

    #[test]
    fn liveness_and_mdns_on_the_same_ip_merge() {
        let mut liveness = observation(DiscoverySourceKind::Neighbor, "x", "192.0.2.7");
        liveness.hostname = None;
        liveness
            .attributes
            .insert("name.netbios".into(), "DESKTOP-1".into());
        let mut mdns = observation(DiscoverySourceKind::Mdns, "tv.local", "192.0.2.7");
        mdns.services.push(Service {
            service_type: "_airplay._tcp".into(),
            name: Some("Salon TV".into()),
            protocol: inet_viewer_shared::TransportProtocol::Tcp,
            port: 7000,
            hostname: Some("tv.local".into()),
            txt: HashMap::new(),
        });
        let merged = merge_observations(vec![liveness, mdns]);
        assert_eq!(merged.len(), 1);
        let device = &merged[0].device;
        assert_eq!(device.display_name, "Salon TV");
        assert!(matches!(merged[0].evidence[0], MatchReason::SharedLanAddress(_)));
        assert!(device.names.contains(&DeviceName {
            name: "tv.local".into(),
            source: "mdns".into()
        }));
        assert!(device.names.iter().any(|name| name.source == "netbios"));
    }

    #[test]
    fn named_lan_hosts_of_one_source_on_one_ip_stay_apart() {
        let left = observation(DiscoverySourceKind::Mdns, "a.local", "192.0.2.8");
        let right = observation(DiscoverySourceKind::Mdns, "b.local", "192.0.2.8");
        assert_eq!(merge_observations(vec![left, right]).len(), 2);
    }

    #[test]
    fn best_hostname_prefers_mdns_then_netbios_over_ptr() {
        let mut liveness = observation(DiscoverySourceKind::Neighbor, "x", "192.0.2.9");
        liveness.hostname = Some("host.lan".into());
        liveness.attributes.insert("hostname_source".into(), "ptr".into());
        liveness.attributes.insert("name.netbios".into(), "NAS".into());
        let merged = merge_observations(vec![liveness]);
        assert_eq!(merged[0].device.display_name, "NAS");
    }

    #[test]
    fn best_status_wins() {
        let mut offline = observation(DiscoverySourceKind::Neighbor, "server", "192.0.2.1");
        offline.status = ObservationStatus::Offline;
        let online = observation(DiscoverySourceKind::Mdns, "server", "192.0.2.1");
        assert_eq!(
            merge_observations(vec![offline, online])[0].device.status,
            DeviceStatus::Online
        );
    }
}
