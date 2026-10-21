mod liveness;
mod macos;
mod mdns;
mod mdns_browse;
mod names;
mod neighbor;
mod netbird;
mod ports;
mod progress;
mod provider;

use inet_viewer_shared::Observation;
use std::net::IpAddr;

pub use macos::{
    detect_macos_network, is_physical_lan_interface, network_from_values, parse_default_gateway,
    parse_default_interface, parse_macos_arp, filter_macos_lan_observations, parse_subnet_mask, physical_interface_candidates,
    MacOsActiveArpProvider, MacOsArpProvider, MacOsBonjourProvider, MacOsNetwork,
};
pub use liveness::{
    probe_liveness, probe_liveness_with_progress, reverse_lookup, LivenessConfig, LivenessMethod, LivenessReport, DEFAULT_PORTS,
};
pub use mdns::parse_avahi_browse;
pub use mdns_browse::{
    base_type, browse_mdns, instance_name, normalize_type, observation_from_record, MdnsConfig, MdnsRecord,
    MdnsReport, MDNS_BROWSE_TYPES, MDNS_META_QUERY,
};
pub use names::{
    mdns_ptr_query, nbstat_request, parse_nbstat_response, parse_ptr_answers,
    parse_ssdp_description, parse_ssdp_location, resolve_names, HostNames, NameReport, SsdpDevice,
};
pub use ports::{
    build_port_scan, device_scan_targets, port_service_name, scan_tcp_ports, PortScanConfig,
    PortScanReport, TOP_TCP_PORTS,
};
pub use neighbor::parse_ip_neigh;
pub use netbird::{
    netbird_effective_status, parse_handshake_age, parse_netbird_status, resolve_netbird_statuses,
    RECENT_HANDSHAKE,
};
pub use progress::{ProgressSink, ProgressUpdate};
pub use provider::{
    ActiveNeighborProvider, CommandProvider, DiscoveryError, DiscoveryProvider, DiscoveryRun,
    DiscoveryRunner, NetbirdProvider, ProviderHealth, ProviderSpec, Scheduler,
};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ParseWarning {
    pub line: usize,
    pub message: String,
    pub input: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ParseOutput {
    pub observations: Vec<Observation>,
    pub warnings: Vec<ParseWarning>,
}

fn warning(line: usize, message: impl Into<String>, input: &str) -> ParseWarning {
    ParseWarning {
        line,
        message: message.into(),
        input: input.trim().to_owned(),
    }
}

fn normalize_hostname(value: &str) -> Option<String> {
    let normalized = value.trim().trim_end_matches('.').to_ascii_lowercase();
    (!normalized.is_empty() && normalized != "-").then_some(normalized)
}

fn push_ip(addresses: &mut Vec<IpAddr>, value: &str) -> bool {
    let value = value.trim().trim_matches(['[', ']']);
    let address = value.split_once('/').map_or(value, |(address, _)| address);
    match address.parse() {
        Ok(address) => {
            if !addresses.contains(&address) {
                addresses.push(address);
            }
            true
        }
        Err(_) => false,
    }
}
