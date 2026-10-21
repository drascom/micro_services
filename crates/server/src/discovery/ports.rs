//! User-triggered TCP connect port scan of one device (cross-platform).
use chrono::{DateTime, Utc};
use futures_util::{stream, StreamExt};
use inet_viewer_shared::{Device, OpenPort, PortScan, TransportProtocol};
use std::{
    net::{IpAddr, SocketAddr},
    time::{Duration, Instant},
};
use tokio::{net::TcpStream, time};

/// Common TCP ports (top list plus typical LAN/IoT/Apple ports).
pub const TOP_TCP_PORTS: &[u16] = &[
    20, 21, 22, 23, 25, 53, 80, 81, 88, 110, 111, 119, 135, 139, 143, 161, 179, 199, 389, 427,
    443, 444, 445, 465, 515, 548, 554, 587, 631, 636, 873, 990, 993, 995, 1025, 1080, 1194, 1433,
    1521, 1723, 1883, 1900, 2049, 2082, 2083, 2181, 2375, 2376, 3000, 3128, 3260, 3306, 3389,
    3689, 4000, 4443, 4840, 5000, 5001, 5060, 5222, 5432, 5555, 5601, 5683, 5800, 5900, 5901,
    6000, 6379, 6443, 6667, 7000, 7100, 7443, 8000, 8001, 8008, 8009, 8060, 8080, 8081, 8086,
    8088, 8123, 8200, 8443, 8554, 8883, 8888, 9000, 9090, 9100, 9200, 9443, 9999, 10000, 32400,
    49152, 62078,
];

/// Well-known service name of a TCP port.
pub fn port_service_name(port: u16) -> Option<&'static str> {
    Some(match port {
        20 => "ftp-data",
        21 => "ftp",
        22 => "ssh",
        23 => "telnet",
        25 => "smtp",
        53 => "dns",
        80 | 81 | 8000 | 8001 | 8080 | 8081 | 8888 => "http",
        88 => "kerberos",
        110 => "pop3",
        111 => "rpcbind",
        119 => "nntp",
        135 => "msrpc",
        139 => "netbios-ssn",
        143 => "imap",
        161 => "snmp",
        179 => "bgp",
        199 => "smux",
        389 => "ldap",
        427 => "svrloc",
        443 | 444 | 4443 | 7443 | 8443 | 9443 => "https",
        445 => "smb",
        465 => "smtps",
        515 => "lpd",
        548 => "afp",
        554 | 8554 => "rtsp",
        587 => "submission",
        631 => "ipp",
        636 => "ldaps",
        873 => "rsync",
        990 => "ftps",
        993 => "imaps",
        995 => "pop3s",
        1025 => "nfs-or-iis",
        1080 => "socks",
        1194 => "openvpn",
        1433 => "mssql",
        1521 => "oracle",
        1723 => "pptp",
        1883 => "mqtt",
        1900 => "upnp",
        2049 => "nfs",
        2082 | 2083 => "cpanel",
        2181 => "zookeeper",
        2375 | 2376 => "docker",
        3000 => "http-dev",
        3128 => "squid",
        3260 => "iscsi",
        3306 => "mysql",
        3389 => "rdp",
        3689 => "daap",
        4000 => "http-alt",
        4840 => "opc-ua",
        5000 => "upnp/airplay",
        5001 => "synology-https",
        5060 => "sip",
        5222 => "xmpp",
        5432 => "postgresql",
        5555 => "adb",
        5601 => "kibana",
        5683 => "coap",
        5800 => "vnc-http",
        5900 | 5901 => "vnc",
        6000 => "x11",
        6379 => "redis",
        6443 => "kubernetes",
        6667 => "irc",
        7000 => "airplay",
        7100 => "font-service",
        8008 => "googlecast-http",
        8009 => "googlecast",
        8060 => "roku",
        8086 => "influxdb",
        8088 => "http-alt",
        8123 => "home-assistant",
        8200 => "dlna",
        8883 => "mqtts",
        9000 => "http-alt",
        9090 => "prometheus",
        9100 => "jetdirect",
        9200 => "elasticsearch",
        9999 => "http-alt",
        10000 => "webmin",
        32400 => "plex",
        49152 => "upnp",
        62078 => "iphone-sync",
        _ => return None,
    })
}

#[derive(Clone, Debug)]
pub struct PortScanConfig {
    pub connect_timeout: Duration,
    /// Sockets open at once (keeps well below the fd limit).
    pub concurrency: usize,
}

impl Default for PortScanConfig {
    fn default() -> Self {
        Self {
            connect_timeout: Duration::from_millis(800),
            concurrency: 64,
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct PortScanReport {
    pub open: Vec<(IpAddr, u16)>,
    pub checked: usize,
    pub elapsed: Duration,
}

/// TCP connect scan; a port is open only when connect() succeeds.
pub async fn scan_tcp_ports(
    addresses: &[IpAddr],
    ports: &[u16],
    config: &PortScanConfig,
) -> PortScanReport {
    let started = Instant::now();
    let targets: Vec<(IpAddr, u16)> = addresses
        .iter()
        .flat_map(|address| ports.iter().map(move |port| (*address, *port)))
        .collect();
    let checked = targets.len();
    let timeout = config.connect_timeout;
    let mut open: Vec<(IpAddr, u16)> = stream::iter(targets)
        .map(|(address, port)| async move {
            let connected = matches!(
                time::timeout(timeout, TcpStream::connect(SocketAddr::new(address, port))).await,
                Ok(Ok(_))
            );
            connected.then_some((address, port))
        })
        .buffer_unordered(config.concurrency.max(1))
        .filter_map(|result| async move { result })
        .collect()
        .await;
    open.sort();
    PortScanReport {
        open,
        checked,
        elapsed: started.elapsed(),
    }
}

/// Addresses (LAN IPv4 first, then NetBIRD) and ports (top list plus TCP
/// ports the device advertises over mDNS) to scan for `device`.
pub fn device_scan_targets(device: &Device) -> (Vec<IpAddr>, Vec<u16>) {
    let mut addresses: Vec<IpAddr> = device
        .lan_ips
        .iter()
        .filter(|address| address.is_ipv4())
        .copied()
        .collect();
    addresses.extend(device.netbird_ip);
    addresses.dedup();
    let mut ports = TOP_TCP_PORTS.to_vec();
    for service in &device.services {
        if service.protocol == TransportProtocol::Tcp && service.port != 0 {
            ports.push(service.port);
        }
    }
    ports.sort_unstable();
    ports.dedup();
    (addresses, ports)
}

/// Builds the stored result: open ports named from mDNS services first,
/// then the static table.
pub fn build_port_scan(
    device: &Device,
    addresses: Vec<IpAddr>,
    report: &PortScanReport,
    scanned_at: DateTime<Utc>,
) -> PortScan {
    let open_ports = report
        .open
        .iter()
        .map(|(address, port)| {
            let advertised = device.services.iter().find(|service| {
                service.protocol == TransportProtocol::Tcp && service.port == *port
            });
            OpenPort {
                address: *address,
                port: *port,
                protocol: TransportProtocol::Tcp,
                service: advertised
                    .map(|service| service.service_type.clone())
                    .or_else(|| port_service_name(*port).map(str::to_owned)),
                source: if advertised.is_some() { "mdns" } else { "tcp" }.to_owned(),
            }
        })
        .collect();
    PortScan {
        scanned_at,
        addresses,
        ports_checked: report.checked,
        elapsed_ms: report.elapsed.as_millis() as u64,
        open_ports,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mdns_ports_are_scanned_and_named() {
        let mut device: Device = serde_json::from_value(serde_json::json!({
            "id": "11111111-1111-4111-8111-111111111111",
            "display_name": "tv",
            "lan_ips": ["192.168.0.20", "fe80::1"],
            "netbird_ip": "100.64.0.2",
            "netbird_ipv6": null,
            "manufacturer": null,
            "model": null,
            "device_type": "unknown",
            "status": "online",
            "first_seen_at": "2026-01-01T00:00:00Z",
            "last_seen_at": "2026-01-01T00:00:00Z"
        }))
        .unwrap();
        device.services.push(inet_viewer_shared::Service {
            service_type: "_custom._tcp".into(),
            name: None,
            protocol: TransportProtocol::Tcp,
            port: 41000,
            hostname: None,
            txt: Default::default(),
        });
        let (addresses, ports) = device_scan_targets(&device);
        assert_eq!(
            addresses,
            vec!["192.168.0.20".parse::<IpAddr>().unwrap(), "100.64.0.2".parse().unwrap()]
        );
        assert!(ports.contains(&41000) && ports.contains(&22));
        let address = addresses[0];
        let report = PortScanReport {
            open: vec![(address, 22), (address, 41000), (address, 40999)],
            checked: ports.len(),
            elapsed: Duration::from_millis(5),
        };
        let scan = build_port_scan(&device, addresses, &report, Utc::now());
        assert_eq!(scan.open_ports[0].service.as_deref(), Some("ssh"));
        assert_eq!(scan.open_ports[0].source, "tcp");
        assert_eq!(scan.open_ports[1].service.as_deref(), Some("_custom._tcp"));
        assert_eq!(scan.open_ports[1].source, "mdns");
        assert_eq!(scan.open_ports[2].service, None);
    }

    #[test]
    fn port_list_is_unique_and_names_cover_it() {
        let mut ports = TOP_TCP_PORTS.to_vec();
        ports.sort_unstable();
        ports.dedup();
        assert_eq!(ports.len(), TOP_TCP_PORTS.len());
        assert!((90..=110).contains(&ports.len()));
        assert!(!ports.contains(&5353));
        for port in [62078, 7000, 8008, 8009, 1883] {
            assert!(ports.contains(&port));
        }
        assert!(TOP_TCP_PORTS.iter().all(|port| port_service_name(*port).is_some()));
        assert_eq!(port_service_name(22), Some("ssh"));
        assert_eq!(port_service_name(62078), Some("iphone-sync"));
        assert_eq!(port_service_name(12345), None);
    }

    #[tokio::test]
    async fn open_listener_is_found_and_closed_port_is_not() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let open = listener.local_addr().unwrap().port();
        let closed = {
            let socket = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
            socket.local_addr().unwrap().port()
        };
        let address: IpAddr = "127.0.0.1".parse().unwrap();
        let report =
            scan_tcp_ports(&[address], &[open, closed], &PortScanConfig::default()).await;
        assert_eq!(report.open, vec![(address, open)]);
        assert_eq!(report.checked, 2);
    }
}
