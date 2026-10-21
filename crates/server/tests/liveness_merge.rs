use chrono::Utc;
use inet_viewer_server::{connect_database, merge::merge_observations, repository::Repository};
use inet_viewer_shared::{DiscoverySourceKind, Observation, ObservationStatus};
use std::collections::HashMap;

fn liveness_only(ip: &str) -> Observation {
    Observation {
        source: DiscoverySourceKind::Neighbor,
        source_id: None,
        hostname: None,
        ip_addresses: vec![ip.parse().unwrap()],
        mac_address: None,
        manufacturer: None,
        model: None,
        status: ObservationStatus::Online,
        services: Vec::new(),
        observed_at: Utc::now(),
        attributes: HashMap::from([("liveness".into(), "tcp:80".into())]),
    }
}

#[tokio::test]
async fn mac_less_lan_observations_create_devices_and_are_reused() {
    let repository = Repository::new(connect_database("sqlite::memory:").await.unwrap());
    for _ in 0..2 {
        let mut merged =
            merge_observations(vec![liveness_only("192.168.0.4"), liveness_only("192.168.0.9")]);
        repository.save_merged(&mut merged).await.unwrap();
    }
    let devices = repository.list_devices().await.unwrap();
    assert_eq!(devices.len(), 2);
    assert!(devices
        .iter()
        .all(|device| device.sources == vec![DiscoverySourceKind::Neighbor]));
}

#[tokio::test]
async fn named_scan_adopts_liveness_device_and_keeps_names_and_ports() {
    use inet_viewer_server::discovery::{observation_from_record, MdnsRecord};
    use inet_viewer_shared::{OpenPort, PortScan, TransportProtocol};
    let repository = Repository::new(connect_database("sqlite::memory:").await.unwrap());
    let mut first = merge_observations(vec![liveness_only("192.168.0.20")]);
    repository.save_merged(&mut first).await.unwrap();
    let id = repository.list_devices().await.unwrap()[0].id.0.to_string();

    let mut named = liveness_only("192.168.0.20");
    named.hostname = Some("DESKTOP-1".into());
    named.attributes.insert("hostname_source".into(), "netbios".into());
    named.attributes.insert("name.netbios".into(), "DESKTOP-1".into());
    let mdns = observation_from_record(
        &MdnsRecord {
            fullname: "Salon._airplay._tcp.local.".into(),
            service_type: "_airplay._tcp.local.".into(),
            hostname: "salon.local.".into(),
            port: 7000,
            addresses: vec!["192.168.0.20".parse().unwrap()],
            txt: Vec::new(),
        },
        Utc::now(),
    );
    let mut second = merge_observations(vec![named, mdns]);
    assert_eq!(second.len(), 1);
    repository.save_merged(&mut second).await.unwrap();

    let devices = repository.list_devices().await.unwrap();
    assert_eq!(devices.len(), 1);
    let device = &devices[0];
    assert_eq!(device.id.0.to_string(), id);
    assert_eq!(device.display_name, "Salon");
    assert_eq!(device.services[0].port, 7000);
    let sources: Vec<_> = device.names.iter().map(|name| name.source.as_str()).collect();
    assert_eq!(sources, vec!["mdns", "netbios"]);

    let scan = PortScan {
        scanned_at: Utc::now(),
        addresses: vec!["192.168.0.20".parse().unwrap()],
        ports_checked: 100,
        elapsed_ms: 900,
        open_ports: vec![OpenPort {
            address: "192.168.0.20".parse().unwrap(),
            port: 7000,
            protocol: TransportProtocol::Tcp,
            service: Some("_airplay._tcp".into()),
            source: "mdns".into(),
        }],
    };
    assert_eq!(repository.load_port_scan(&id).await.unwrap(), None);
    repository.save_port_scan(&id, &scan).await.unwrap();
    assert_eq!(repository.load_port_scan(&id).await.unwrap(), Some(scan));
}
