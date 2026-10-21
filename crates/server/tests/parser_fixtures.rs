use chrono::{TimeZone, Utc};
use inet_viewer_server::discovery::{
    network_from_values, parse_avahi_browse, parse_default_gateway, parse_default_interface,
    parse_handshake_age, parse_ip_neigh, parse_macos_arp, parse_netbird_status, parse_subnet_mask,
    resolve_netbird_statuses,
};
use std::{collections::BTreeMap, time::Duration};
use inet_viewer_shared::{ObservationStatus, TransportProtocol};
use std::net::Ipv4Addr;

fn observed_at() -> chrono::DateTime<Utc> {
    Utc.with_ymd_and_hms(2026, 10, 19, 12, 0, 0).unwrap()
}

#[test]
fn parses_masked_netbird_fixture_and_keeps_good_peers() {
    let parsed = parse_netbird_status(
        include_str!("../../../fixtures/netbird-status.txt"),
        observed_at(),
    );
    assert_eq!(parsed.observations.len(), 5);
    assert_eq!(
        parsed.observations[0].hostname.as_deref(),
        Some("peer-alpha.example.internal")
    );
    assert_eq!(parsed.observations[0].status, ObservationStatus::Online);
    assert_eq!(parsed.observations[0].ip_addresses.len(), 2);
    assert_eq!(parsed.observations[1].status, ObservationStatus::Connecting);
    assert_eq!(parsed.observations[2].status, ObservationStatus::Connecting);
    assert_eq!(parsed.observations[3].status, ObservationStatus::Idle);
    assert_eq!(
        parsed.observations[4].hostname.as_deref(),
        Some("local-peer.example.internal")
    );
    assert_eq!(parsed.observations[4].status, ObservationStatus::Unknown);
    assert_eq!(parsed.warnings.len(), 2);
    assert!(parsed
        .warnings
        .iter()
        .any(|warning| warning.input == "malformed peer line"));
}

#[test]
fn netbird_connecting_is_not_online_without_evidence() {
    let fixture = include_str!("../../../fixtures/netbird-status.txt");
    // No liveness answers: Connected -> online, Connecting without / with an
    // old handshake -> offline, Idle with a recent handshake -> online,
    // local peer stays unknown.
    let mut parsed = parse_netbird_status(fixture, observed_at());
    resolve_netbird_statuses(&mut parsed.observations, &BTreeMap::<Ipv4Addr, &str>::new());
    let statuses: Vec<_> = parsed.observations.iter().map(|item| item.status).collect();
    assert_eq!(
        statuses,
        [
            ObservationStatus::Online,
            ObservationStatus::Offline,
            ObservationStatus::Offline,
            ObservationStatus::Online,
            ObservationStatus::Unknown,
        ]
    );
    assert_eq!(parsed.observations[1].attributes["netbird_status"], "connecting");

    // Connecting peers that answer ICMP/TCP from the app are online.
    let mut parsed = parse_netbird_status(fixture, observed_at());
    let reachable = BTreeMap::from([
        (Ipv4Addr::new(100, 64, 10, 12), "icmp"),
        (Ipv4Addr::new(100, 64, 10, 13), "tcp:22"),
    ]);
    resolve_netbird_statuses(&mut parsed.observations, &reachable);
    assert_eq!(parsed.observations[1].status, ObservationStatus::Online);
    assert_eq!(parsed.observations[1].attributes["liveness"], "icmp");
    assert_eq!(parsed.observations[2].status, ObservationStatus::Online);
}

#[test]
fn netbird_handshake_ages_parse() {
    assert_eq!(parse_handshake_age("10 seconds ago"), Some(Duration::from_secs(10)));
    assert_eq!(parse_handshake_age("1 minute ago"), Some(Duration::from_secs(60)));
    assert_eq!(
        parse_handshake_age("5 hours, 12 minutes ago"),
        Some(Duration::from_secs(5 * 3600 + 12 * 60))
    );
    assert_eq!(parse_handshake_age("a minute ago"), Some(Duration::from_secs(60)));
    assert_eq!(parse_handshake_age("-"), None);
    assert_eq!(parse_handshake_age(""), None);
}

#[test]
fn parses_masked_neighbor_fixture_and_normalizes_states_and_mac() {
    let parsed = parse_ip_neigh(
        include_str!("../../../fixtures/ip-neigh.txt"),
        observed_at(),
    );
    assert_eq!(parsed.observations.len(), 4);
    assert_eq!(parsed.observations[0].status, ObservationStatus::Online);
    assert_eq!(parsed.observations[1].status, ObservationStatus::Idle);
    assert_eq!(
        parsed.observations[1]
            .mac_address
            .as_ref()
            .unwrap()
            .to_string(),
        "02:00:00:00:00:22"
    );
    assert_eq!(parsed.observations[3].status, ObservationStatus::Offline);
    assert_eq!(parsed.warnings.len(), 1);
}

#[test]
fn parses_masked_avahi_fixture_and_normalizes_services() {
    let parsed = parse_avahi_browse(
        include_str!("../../../fixtures/avahi-browse.txt"),
        observed_at(),
    );
    assert_eq!(parsed.observations.len(), 3);
    let printer = &parsed.observations[0];
    assert_eq!(printer.hostname.as_deref(), Some("printer-01.local"));
    assert_eq!(
        printer.mac_address.as_ref().unwrap().to_string(),
        "02:00:00:00:00:31"
    );
    assert_eq!(printer.manufacturer.as_deref(), Some("Example Corp"));
    assert_eq!(printer.services[0].service_type, "_ipp._tcp");
    assert_eq!(printer.services[0].protocol, TransportProtocol::Tcp);
    assert_eq!(
        parsed.observations[1].services[0].name.as_deref(),
        Some("Living Room")
    );
    assert_eq!(
        parsed.observations[1].hostname.as_deref(),
        Some("media-01.local")
    );
    assert_eq!(parsed.observations[2].services[0].service_type, "_smb._tcp");
    assert_eq!(parsed.warnings.len(), 2);
}

#[test]
fn unknown_lines_warn_without_discarding_valid_records() {
    let parsed = parse_ip_neigh(
        "192.0.2.1 dev eth0 lladdr 02:00:00:00:00:01 REACHABLE\nnoise\n192.0.2.2 dev eth0 FAILED\n",
        observed_at(),
    );
    assert_eq!(parsed.observations.len(), 2);
    assert_eq!(parsed.warnings.len(), 1);
    assert_eq!(parsed.warnings[0].line, 2);
}

#[test]
fn parses_realistic_macos_arp_route_and_ipconfig_fixtures() {
    let arp = parse_macos_arp(
        include_str!("../../../fixtures/macos-arp.txt"),
        observed_at(),
    );
    assert_eq!(arp.observations.len(), 2);
    assert_eq!(
        arp.observations[0].hostname.as_deref(),
        Some("gateway.local")
    );
    assert_eq!(
        arp.observations[1]
            .mac_address
            .as_ref()
            .unwrap()
            .to_string(),
        "a4:83:e7:44:55:66"
    );

    let route = include_str!("../../../fixtures/macos-route-default.txt");
    let interface = parse_default_interface(route).unwrap();
    let gateway = parse_default_gateway(route).unwrap();
    let mask = parse_subnet_mask("subnet_mask (ip): 255.255.255.0\n").unwrap();
    let network =
        network_from_values(&interface, Ipv4Addr::new(192, 168, 50, 42), mask, gateway).unwrap();
    assert_eq!(network.interface, "en0");
    assert_eq!(network.cidr, "192.168.50.0/24");
}
