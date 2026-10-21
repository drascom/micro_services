//! In-process, cross-platform mDNS / DNS-SD discovery (pure Rust `mdns-sd`).
//! Runs inside the calling process so OS network privacy (macOS Local
//! Network) applies to the app itself.
use super::normalize_hostname;
use chrono::{DateTime, Utc};
use inet_viewer_shared::{
    DiscoverySourceKind, MacAddress, Observation, ObservationStatus, Service, TransportProtocol,
};
use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    net::IpAddr,
    str::FromStr,
    time::{Duration, Instant},
};

pub const MDNS_META_QUERY: &str = "_services._dns-sd._udp";

/// Service types browsed on every scan, in addition to every type the
/// meta-query reveals. Browsing uses a raw multicast socket (mdns-sd), so
/// macOS `NSBonjourServices` does not filter it; the Info.plist still lists
/// these as a common set (a test keeps them in sync).
pub const MDNS_BROWSE_TYPES: &[&str] = &[
    "_http._tcp",
    "_https._tcp",
    "_ssh._tcp",
    "_sftp-ssh._tcp",
    "_smb._tcp",
    "_afpovertcp._tcp",
    "_device-info._tcp",
    "_airplay._tcp",
    "_raop._tcp",
    "_googlecast._tcp",
    "_ipp._tcp",
    "_ipps._tcp",
    "_printer._tcp",
    "_pdl-datastream._tcp",
    "_scanner._tcp",
    "_uscan._tcp",
    "_hap._tcp",
    "_hap._udp",
    "_homekit._tcp",
    "_companion-link._tcp",
    "_sleep-proxy._udp",
    "_spotify-connect._tcp",
    "_matter._tcp",
    "_matterc._udp",
    "_sonos._tcp",
    "_rfb._tcp",
    "_workstation._tcp",
    "_adisk._tcp",
    "_daap._tcp",
    "_touch-able._tcp",
    "_mediaremotetv._tcp",
    "_amzn-wplay._tcp",
    "_androidtvremote2._tcp",
    "_esphomelib._tcp",
    "_home-assistant._tcp",
    "_mqtt._tcp",
    "_nfs._tcp",
    "_ftp._tcp",
    "_rdp._tcp",
    "_webdav._tcp",
];

#[derive(Clone, Debug)]
pub struct MdnsConfig {
    pub duration: Duration,
    pub browse_types: Vec<String>,
}

impl Default for MdnsConfig {
    fn default() -> Self {
        Self {
            duration: Duration::from_secs(5),
            browse_types: MDNS_BROWSE_TYPES.iter().map(|ty| (*ty).to_owned()).collect(),
        }
    }
}

/// One resolved DNS-SD instance, independent of the mdns-sd types.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct MdnsRecord {
    pub fullname: String,
    pub service_type: String,
    pub hostname: String,
    pub port: u16,
    pub addresses: Vec<IpAddr>,
    pub txt: Vec<(String, String)>,
}

#[derive(Clone, Debug, Default)]
pub struct MdnsReport {
    pub observations: Vec<Observation>,
    /// Every type seen (meta-query answers plus browsed types with results).
    pub types_found: BTreeSet<String>,
    pub browsed: usize,
    pub resolved: usize,
    /// Resolved instance count per browsed (base) type.
    pub resolved_by_type: BTreeMap<String, usize>,
    /// Types the meta-query advertised but no instance of which resolved.
    pub unresolved: BTreeSet<String>,
    pub error: Option<String>,
    pub elapsed: Duration,
}

/// `_http._tcp.local.` -> `_http._tcp`.
pub fn normalize_type(value: &str) -> String {
    value
        .trim()
        .trim_end_matches('.')
        .trim_end_matches(".local")
        .to_ascii_lowercase()
}

/// Subtype `_x._sub._matter._tcp` -> base type `_matter._tcp`.
pub fn base_type(value: &str) -> String {
    let ty = normalize_type(value);
    match ty.find("._sub.") {
        Some(index) => ty[index + "._sub.".len()..].to_owned(),
        None => ty,
    }
}

/// Instance label of a full DNS-SD name (`Living Room._airplay._tcp.local.`).
pub fn instance_name(fullname: &str, service_type: &str) -> String {
    let lower = fullname.to_ascii_lowercase();
    let suffix = format!(".{}", normalize_type(service_type));
    match lower.find(&suffix) {
        Some(index) => fullname[..index].replace("\\ ", " ").replace("\\.", "."),
        None => fullname.trim_end_matches('.').to_owned(),
    }
}

/// Converts a resolved instance into an mDNS observation.
pub fn observation_from_record(record: &MdnsRecord, observed_at: DateTime<Utc>) -> Observation {
    let service_type = normalize_type(&record.service_type);
    let txt: HashMap<String, String> = record
        .txt
        .iter()
        .map(|(key, value)| (key.to_ascii_lowercase(), value.clone()))
        .collect();
    let pick = |keys: &[&str]| {
        keys.iter()
            .find_map(|key| txt.get(*key).filter(|value| !value.trim().is_empty()).cloned())
    };
    let mac_address = ["mac", "mac_address", "deviceid", "bt"]
        .iter()
        .find_map(|key| txt.get(*key).and_then(|value| MacAddress::from_str(value).ok()));
    let model = pick(&["model", "md", "mdl", "usb_mdl", "ty", "am"]);
    let manufacturer = pick(&["manufacturer", "mfg", "usb_mfg", "brand", "vendor"]);
    let hostname = normalize_hostname(&record.hostname);
    let name = instance_name(&record.fullname, &record.service_type);
    let mut addresses = record.addresses.clone();
    addresses.sort_by_key(|address| (address.is_ipv6(), *address));
    addresses.dedup();
    let protocol = if service_type.ends_with("._udp") {
        TransportProtocol::Udp
    } else {
        TransportProtocol::Tcp
    };
    let mut attributes = HashMap::new();
    if let Some(hostname) = &hostname {
        attributes.insert("name.mdns".into(), hostname.clone());
    }
    attributes.insert("mdns_type".into(), service_type.clone());
    Observation {
        source: DiscoverySourceKind::Mdns,
        source_id: None,
        hostname: hostname.clone(),
        ip_addresses: addresses,
        mac_address,
        manufacturer,
        model,
        status: ObservationStatus::Online,
        services: vec![Service {
            service_type,
            name: (!name.is_empty()).then_some(name),
            protocol,
            port: record.port,
            hostname,
            txt,
        }],
        observed_at,
        attributes,
    }
}

/// Browses the meta-query plus `config.browse_types` (and every type the
/// meta-query reveals) for `config.duration`, resolving instances.
pub async fn browse_mdns(config: MdnsConfig) -> MdnsReport {
    tokio::task::spawn_blocking(move || browse_blocking(&config))
        .await
        .unwrap_or_else(|error| MdnsReport {
            error: Some(format!("task failed: {error}")),
            ..MdnsReport::default()
        })
}

fn browse_blocking(config: &MdnsConfig) -> MdnsReport {
    use mdns_sd::{ServiceDaemon, ServiceEvent};
    let started = Instant::now();
    let mut report = MdnsReport::default();
    let daemon = match ServiceDaemon::new() {
        Ok(daemon) => daemon,
        Err(error) => {
            report.error = Some(format!("daemon: {error}"));
            return report;
        }
    };
    let mut receivers = Vec::new();
    let mut browsing = BTreeSet::new();
    let mut browse = |ty: &str, receivers: &mut Vec<_>, report: &mut MdnsReport| {
        let ty = base_type(ty);
        if ty.is_empty() || !browsing.insert(ty.clone()) {
            return;
        }
        match daemon.browse(&format!("{ty}.local.")) {
            Ok(receiver) => {
                receivers.push((ty, receiver));
                report.browsed += 1;
            }
            Err(error) => {
                report.error.get_or_insert_with(|| format!("browse {ty}: {error}"));
            }
        }
    };
    browse(MDNS_META_QUERY, &mut receivers, &mut report);
    for ty in &config.browse_types {
        browse(ty, &mut receivers, &mut report);
    }

    let now = Utc::now();
    let mut seen = BTreeSet::new();
    let mut advertised = BTreeSet::new();
    let deadline = started + config.duration;
    while Instant::now() < deadline {
        let mut idle = true;
        let mut discovered = Vec::new();
        for (ty, receiver) in &receivers {
            while let Ok(event) = receiver.try_recv() {
                idle = false;
                match event {
                    ServiceEvent::ServiceFound(_, fullname) if ty == MDNS_META_QUERY => {
                        let found = base_type(&fullname);
                        advertised.insert(found.clone());
                        report.types_found.insert(found.clone());
                        discovered.push(found);
                    }
                    ServiceEvent::ServiceResolved(info) => {
                        if !seen.insert(info.fullname.to_ascii_lowercase()) {
                            continue;
                        }
                        let resolved_type = base_type(&info.ty_domain);
                        report.types_found.insert(resolved_type.clone());
                        *report.resolved_by_type.entry(resolved_type).or_default() += 1;
                        let record = MdnsRecord {
                            fullname: info.fullname.clone(),
                            service_type: info.ty_domain.clone(),
                            hostname: info.host.clone(),
                            port: info.port,
                            addresses: info.addresses.iter().map(|ip| ip.to_ip_addr()).collect(),
                            txt: info
                                .txt_properties
                                .iter()
                                .map(|prop| (prop.key().to_owned(), prop.val_str().to_owned()))
                                .collect(),
                        };
                        report.resolved += 1;
                        report.observations.push(observation_from_record(&record, now));
                    }
                    _ => {}
                }
            }
        }
        for ty in discovered {
            browse(&ty, &mut receivers, &mut report);
        }
        if idle {
            std::thread::sleep(Duration::from_millis(40));
        }
    }
    if let Ok(status) = daemon.shutdown() {
        let _ = status.recv_timeout(Duration::from_millis(500));
    }
    report.unresolved = advertised
        .into_iter()
        .filter(|ty| !report.resolved_by_type.contains_key(ty))
        .collect();
    report.elapsed = started.elapsed();
    report
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolved_record_becomes_mdns_observation() {
        let record = MdnsRecord {
            fullname: "Salon\\ TV._airplay._tcp.local.".into(),
            service_type: "_airplay._tcp.local.".into(),
            hostname: "Salon-TV.local.".into(),
            port: 7000,
            addresses: vec!["fe80::1".parse().unwrap(), "192.168.0.20".parse().unwrap()],
            txt: vec![
                ("model".into(), "AppleTV11,1".into()),
                ("deviceid".into(), "AA:BB:CC:DD:EE:FF".into()),
            ],
        };
        let observation = observation_from_record(&record, Utc::now());
        assert_eq!(observation.source, DiscoverySourceKind::Mdns);
        assert_eq!(observation.hostname.as_deref(), Some("salon-tv.local"));
        assert_eq!(observation.ip_addresses[0], "192.168.0.20".parse::<IpAddr>().unwrap());
        assert_eq!(observation.model.as_deref(), Some("AppleTV11,1"));
        assert_eq!(
            observation.mac_address.as_ref().map(ToString::to_string).as_deref(),
            Some("aa:bb:cc:dd:ee:ff")
        );
        let service = &observation.services[0];
        assert_eq!(service.service_type, "_airplay._tcp");
        assert_eq!(service.name.as_deref(), Some("Salon TV"));
        assert_eq!(service.port, 7000);
        assert_eq!(service.protocol, TransportProtocol::Tcp);
        assert_eq!(
            observation.attributes.get("name.mdns").map(String::as_str),
            Some("salon-tv.local")
        );
    }

    #[test]
    fn md_key_is_used_as_model_and_udp_types_are_udp() {
        let record = MdnsRecord {
            fullname: "Hub._hap._udp.local.".into(),
            service_type: "_hap._udp.local.".into(),
            hostname: "hub.local.".into(),
            port: 5683,
            addresses: vec!["192.168.0.30".parse().unwrap()],
            txt: vec![("md".into(), "Bridge 2".into())],
        };
        let observation = observation_from_record(&record, Utc::now());
        assert_eq!(observation.model.as_deref(), Some("Bridge 2"));
        assert_eq!(observation.services[0].protocol, TransportProtocol::Udp);
    }

    #[test]
    fn types_are_normalized() {
        assert_eq!(normalize_type("_HTTP._tcp.local."), "_http._tcp");
        assert_eq!(normalize_type("_ssh._tcp"), "_ssh._tcp");
        assert_eq!(base_type("_i0bff90ff._sub._matter._tcp.local."), "_matter._tcp");
        assert_eq!(base_type("_kdeconnect._udp"), "_kdeconnect._udp");
    }
}
