use super::{DiscoveryError, DiscoveryProvider, DiscoveryRun, ParseOutput, ParseWarning};
use async_trait::async_trait;
use chrono::Utc;
use futures_util::{stream::FuturesUnordered, StreamExt};
use inet_viewer_shared::{DiscoverySourceKind, MacAddress, Observation, ObservationStatus};
use std::{
    collections::HashMap,
    net::{IpAddr, Ipv4Addr},
    path::{Path, PathBuf},
    process::Stdio,
    str::FromStr,
    time::Duration,
};
use tokio::{process::Command, time};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MacOsNetwork {
    pub interface: String,
    pub address: Ipv4Addr,
    pub cidr: String,
    pub gateway: Ipv4Addr,
}

pub fn is_physical_lan_interface(interface: &str) -> bool {
    matches!(interface, "en0" | "en1")
}

pub fn physical_interface_candidates(default_interface: &str) -> Vec<&str> {
    let mut candidates = Vec::with_capacity(2);
    if is_physical_lan_interface(default_interface) {
        candidates.push(default_interface);
    }
    for interface in ["en0", "en1"] {
        if !candidates.contains(&interface) {
            candidates.push(interface);
        }
    }
    candidates
}

pub fn parse_default_interface(input: &str) -> Option<String> {
    input.lines().find_map(|line| {
        let (key, value) = line.trim().split_once(':')?;
        (key.trim() == "interface")
            .then(|| value.trim().to_owned())
            .filter(|value| !value.is_empty())
    })
}

pub fn parse_default_gateway(input: &str) -> Option<Ipv4Addr> {
    input.lines().find_map(|line| {
        let (key, value) = line.trim().split_once(':')?;
        (key.trim() == "gateway")
            .then(|| value.trim().parse().ok())
            .flatten()
    })
}

pub fn parse_subnet_mask(input: &str) -> Option<Ipv4Addr> {
    let value = input.split_whitespace().next_back()?;
    if let Ok(mask) = value.parse() {
        return Some(mask);
    }
    let hex = value
        .strip_prefix("0x")
        .or_else(|| value.strip_prefix("0X"))?;
    u32::from_str_radix(hex, 16).ok().map(Ipv4Addr::from)
}

pub fn network_from_values(
    interface: &str,
    address: Ipv4Addr,
    mask: Ipv4Addr,
    gateway: Ipv4Addr,
) -> Result<MacOsNetwork, DiscoveryError> {
    if !is_physical_lan_interface(interface) || !address.is_private() || !gateway.is_private() {
        return Err(DiscoveryError::InvalidCommand(
            "no active physical macOS LAN interface with a private IPv4 route".into(),
        ));
    }
    let mask = u32::from(mask);
    let prefix = mask.leading_ones();
    let expected = if prefix == 0 {
        0
    } else {
        u32::MAX << (32 - prefix)
    };
    if mask != expected || !(24..=32).contains(&prefix) {
        return Err(DiscoveryError::InvalidCommand(
            "active macOS subnet must use a contiguous /24 through /32 mask".into(),
        ));
    }
    let network = Ipv4Addr::from(u32::from(address) & mask);
    Ok(MacOsNetwork {
        interface: interface.to_owned(),
        address,
        cidr: format!("{network}/{prefix}"),
        gateway,
    })
}

async fn command_text(command: &Path, args: &[&str]) -> Result<String, DiscoveryError> {
    let timeout = Duration::from_secs(5);
    let output = time::timeout(timeout, Command::new(command).args(args).output())
        .await
        .map_err(|_| DiscoveryError::Timeout(timeout))?
        .map_err(DiscoveryError::Spawn)?;
    if !output.status.success() {
        return Err(DiscoveryError::Exit {
            code: output.status.code(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        });
    }
    String::from_utf8(output.stdout).map_err(|_| DiscoveryError::OutputNotUtf8)
}

pub async fn detect_macos_network(
    route: &Path,
    ipconfig: &Path,
) -> Result<MacOsNetwork, DiscoveryError> {
    let route_output = command_text(route, &["-n", "get", "default"]).await?;
    let default_interface = parse_default_interface(&route_output).ok_or_else(|| {
        DiscoveryError::InvalidCommand("default route did not report an interface".into())
    })?;
    for interface in physical_interface_candidates(&default_interface) {
        let scoped_route = if interface == default_interface {
            route_output.clone()
        } else {
            match command_text(route, &["-n", "get", "-ifscope", interface, "default"]).await {
                Ok(output) => output,
                Err(_) => continue,
            }
        };
        let Some(gateway) = parse_default_gateway(&scoped_route) else {
            continue;
        };
        let Ok(address_output) = command_text(ipconfig, &["getifaddr", interface]).await else {
            continue;
        };
        let Ok(address) = address_output.trim().parse() else {
            continue;
        };
        let Ok(mask_output) =
            command_text(ipconfig, &["getoption", interface, "subnet_mask"]).await
        else {
            continue;
        };
        let Some(mask) = parse_subnet_mask(&mask_output) else {
            continue;
        };
        if let Ok(network) = network_from_values(interface, address, mask, gateway) {
            return Ok(network);
        }
    }
    Err(DiscoveryError::InvalidCommand(
        "no active physical macOS LAN interface with a private IPv4 route".into(),
    ))
}

/// Parse the stable `arp -an` format shipped by macOS.
pub fn parse_macos_arp(input: &str, observed_at: chrono::DateTime<Utc>) -> ParseOutput {
    let mut output = ParseOutput::default();
    for (index, raw) in input.lines().enumerate() {
        let Some(open) = raw.find('(') else { continue };
        let Some(close) = raw[open + 1..].find(')').map(|value| value + open + 1) else {
            continue;
        };
        let ip_text = &raw[open + 1..close];
        let Some(at) = raw.find(" at ") else { continue };
        let mac_text = raw[at + 4..].split_whitespace().next().unwrap_or_default();
        let (Ok(ip), Ok(mac)) = (ip_text.parse::<IpAddr>(), MacAddress::from_str(mac_text)) else {
            if mac_text != "(incomplete)" {
                output.warnings.push(ParseWarning {
                    line: index + 1,
                    message: "invalid macOS ARP row".into(),
                    input: raw.trim().into(),
                });
            }
            continue;
        };
        let hostname = raw[..open].trim().trim_end_matches('.');
        let mut attributes = HashMap::new();
        if let Some(interface) = raw
            .split_whitespace()
            .skip_while(|field| *field != "on")
            .nth(1)
        {
            attributes.insert("interface".to_owned(), interface.to_owned());
        }
        output.observations.push(Observation {
            source: DiscoverySourceKind::Neighbor,
            source_id: Some(mac.to_string()),
            hostname: (!hostname.is_empty() && hostname != "?").then(|| hostname.to_lowercase()),
            ip_addresses: vec![ip],
            mac_address: Some(mac),
            manufacturer: None,
            model: None,
            status: ObservationStatus::Online,
            services: Vec::new(),
            observed_at,
            attributes,
        });
    }
    output
}

/// Keeps ARP observations that belong to the active LAN: on its interface
/// (when known), inside its CIDR and with a unicast MAC. Drops broadcast and
/// multicast rows (`ff:ff:...`, `01:00:5e:...`) and other subnets.
pub fn filter_macos_lan_observations(
    observations: Vec<Observation>,
    network: &MacOsNetwork,
) -> Vec<Observation> {
    let Ok(targets) = macos_targets(&network.cidr) else {
        return Vec::new();
    };
    let (Some(first), Some(last)) = (targets.first().copied(), targets.last().copied()) else {
        return Vec::new();
    };
    observations
        .into_iter()
        .filter(|observation| {
            observation
                .attributes
                .get("interface")
                .is_none_or(|interface| *interface == network.interface)
        })
        .filter(|observation| {
            observation.ip_addresses.iter().any(|address| match address {
                IpAddr::V4(v4) => *v4 >= first && *v4 <= last,
                IpAddr::V6(_) => false,
            })
        })
        .filter(|observation| {
            observation.mac_address.as_ref().is_some_and(|mac| {
                let text = mac.to_string();
                let first_octet = u8::from_str_radix(&text[..2], 16).unwrap_or(1);
                first_octet & 1 == 0
            })
        })
        .collect()
}

fn validate_executable(command: &Path) -> Result<(), DiscoveryError> {
    if !command.is_absolute() || !command.is_file() {
        return Err(DiscoveryError::InvalidCommand(format!(
            "{} is not an executable file",
            command.display()
        )));
    }
    Ok(())
}

pub struct MacOsArpProvider {
    command: PathBuf,
}

impl MacOsArpProvider {
    pub fn new(command: impl Into<PathBuf>) -> Result<Self, DiscoveryError> {
        let command = command.into();
        validate_executable(&command)?;
        Ok(Self { command })
    }
}

pub struct MacOsActiveArpProvider {
    arp: MacOsArpProvider,
    ping_command: PathBuf,
    network: MacOsNetwork,
}

impl MacOsActiveArpProvider {
    pub fn new(
        arp_command: impl Into<PathBuf>,
        ping_command: impl Into<PathBuf>,
        network: MacOsNetwork,
    ) -> Result<Self, DiscoveryError> {
        let ping_command = ping_command.into();
        validate_executable(&ping_command)?;
        macos_targets(&network.cidr)?;
        Ok(Self {
            arp: MacOsArpProvider::new(arp_command)?,
            ping_command,
            network,
        })
    }

    pub fn network(&self) -> &MacOsNetwork {
        &self.network
    }
}

fn macos_targets(cidr: &str) -> Result<Vec<Ipv4Addr>, DiscoveryError> {
    let (address, prefix) = cidr
        .split_once('/')
        .ok_or_else(|| DiscoveryError::InvalidCommand("invalid macOS LAN CIDR".into()))?;
    let address: Ipv4Addr = address
        .parse()
        .map_err(|_| DiscoveryError::InvalidCommand("invalid macOS LAN address".into()))?;
    let prefix: u32 = prefix
        .parse()
        .map_err(|_| DiscoveryError::InvalidCommand("invalid macOS LAN prefix".into()))?;
    if !address.is_private() || !(24..=32).contains(&prefix) {
        return Err(DiscoveryError::InvalidCommand(
            "macOS LAN CIDR must be private and /24 through /32".into(),
        ));
    }
    let mask = u32::MAX << (32 - prefix);
    let network = u32::from(address) & mask;
    let broadcast = network | !mask;
    let (first, last) = if prefix == 32 {
        (network, network)
    } else {
        (network + 1, broadcast - 1)
    };
    Ok((first..=last).map(Ipv4Addr::from).collect())
}

/// Runs the Bonjour browser long enough to verify multicast DNS availability.
pub struct MacOsBonjourProvider {
    command: PathBuf,
}

impl MacOsBonjourProvider {
    pub fn new(command: impl Into<PathBuf>) -> Result<Self, DiscoveryError> {
        let command = command.into();
        validate_executable(&command)?;
        Ok(Self { command })
    }
}

#[async_trait]
impl DiscoveryProvider for MacOsBonjourProvider {
    fn kind(&self) -> DiscoverySourceKind {
        DiscoverySourceKind::Mdns
    }

    async fn discover(&self) -> Result<DiscoveryRun, DiscoveryError> {
        let started_at = Utc::now();
        let mut child = Command::new(&self.command)
            .args(["-B", "_services._dns-sd._udp", "local."])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .map_err(DiscoveryError::Spawn)?;
        tokio::select! {
            status = child.wait() => {
                let status = status.map_err(DiscoveryError::Spawn)?;
                return Err(DiscoveryError::Exit {
                    code: status.code(),
                    stderr: "dns-sd exited before the browse window completed".into(),
                });
            }
            _ = time::sleep(Duration::from_secs(2)) => {
                child.kill().await.map_err(DiscoveryError::Spawn)?;
                let _ = child.wait().await.map_err(DiscoveryError::Spawn)?;
            }
        }
        Ok(DiscoveryRun {
            kind: DiscoverySourceKind::Mdns,
            observations: Vec::new(),
            warnings: Vec::new(),
            started_at,
            finished_at: Utc::now(),
        })
    }
}

#[async_trait]
impl DiscoveryProvider for MacOsArpProvider {
    fn kind(&self) -> DiscoverySourceKind {
        DiscoverySourceKind::Neighbor
    }

    async fn discover(&self) -> Result<DiscoveryRun, DiscoveryError> {
        let started_at = Utc::now();
        let mut command = Command::new(&self.command);
        command
            .arg("-an")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        let timeout = Duration::from_secs(5);
        let result = time::timeout(timeout, command.output())
            .await
            .map_err(|_| DiscoveryError::Timeout(timeout))?
            .map_err(DiscoveryError::Spawn)?;
        if !result.status.success() {
            return Err(DiscoveryError::Exit {
                code: result.status.code(),
                stderr: String::from_utf8_lossy(&result.stderr).into_owned(),
            });
        }
        let stdout = String::from_utf8(result.stdout).map_err(|_| DiscoveryError::OutputNotUtf8)?;
        let parsed = parse_macos_arp(&stdout, Utc::now());
        Ok(DiscoveryRun {
            kind: DiscoverySourceKind::Neighbor,
            observations: parsed.observations,
            warnings: parsed.warnings,
            started_at,
            finished_at: Utc::now(),
        })
    }
}

#[async_trait]
impl DiscoveryProvider for MacOsActiveArpProvider {
    fn kind(&self) -> DiscoverySourceKind {
        DiscoverySourceKind::Neighbor
    }

    async fn discover(&self) -> Result<DiscoveryRun, DiscoveryError> {
        const CONCURRENCY: usize = 32;
        let targets = macos_targets(&self.network.cidr)?;
        for batch in targets.chunks(CONCURRENCY) {
            let mut probes = FuturesUnordered::new();
            for address in batch {
                let ping = self.ping_command.clone();
                let address = address.to_string();
                probes.push(async move {
                    let mut command = Command::new(ping);
                    command
                        .args(["-c", "1", "-W", "1000", &address])
                        .stdin(Stdio::null())
                        .stdout(Stdio::null())
                        .stderr(Stdio::null())
                        .kill_on_drop(true);
                    let _ = time::timeout(Duration::from_millis(1500), command.status()).await;
                });
            }
            while probes.next().await.is_some() {}
        }
        let mut run = self.arp.discover().await?;
        run.observations = filter_macos_lan_observations(run.observations, &self.network);
        Ok(run)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_macos_arp_rows_and_ignores_incomplete_entries() {
        let input = "router.local (192.168.1.1) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]\n? (192.168.1.50) at (incomplete) on en0 ifscope [ethernet]";
        let output = parse_macos_arp(input, Utc::now());
        assert_eq!(output.observations.len(), 1);
        assert_eq!(
            output.observations[0].hostname.as_deref(),
            Some("router.local")
        );
        assert_eq!(
            output.observations[0].ip_addresses[0].to_string(),
            "192.168.1.1"
        );
    }

    #[test]
    fn parses_and_filters_real_macos_arp_format() {
        let input = include_str!("../../../../fixtures/macos-arp-ifscope.txt");
        let parsed = parse_macos_arp(input, Utc::now());
        assert!(parsed.warnings.is_empty(), "{:?}", parsed.warnings);
        assert_eq!(parsed.observations.len(), 9);
        assert_eq!(
            parsed.observations[0].mac_address.as_ref().unwrap().to_string(),
            "8c:9a:8f:0e:49:ae"
        );
        let network = MacOsNetwork {
            interface: "en0".into(),
            address: Ipv4Addr::new(192, 168, 0, 103),
            cidr: "192.168.0.0/24".into(),
            gateway: Ipv4Addr::new(192, 168, 0, 1),
        };
        let kept = filter_macos_lan_observations(parsed.observations, &network);
        let addresses: Vec<String> = kept
            .iter()
            .map(|item| item.ip_addresses[0].to_string())
            .collect();
        assert_eq!(
            addresses,
            ["192.168.0.1", "192.168.0.2", "192.168.0.4", "192.168.0.40", "192.168.0.77"]
        );
    }

    #[test]
    fn parses_route_and_ipconfig_values_into_private_cidr() {
        let route = "   route to: default\ndestination: default\n  gateway: 192.168.50.1\n  interface: en0\n";
        assert_eq!(parse_default_interface(route).as_deref(), Some("en0"));
        assert_eq!(
            parse_default_gateway(route),
            Some(Ipv4Addr::new(192, 168, 50, 1))
        );
        assert_eq!(
            parse_subnet_mask("0xffffff00\n"),
            Some(Ipv4Addr::new(255, 255, 255, 0))
        );
        let network = network_from_values(
            "en0",
            Ipv4Addr::new(192, 168, 50, 23),
            Ipv4Addr::new(255, 255, 255, 0),
            Ipv4Addr::new(192, 168, 50, 1),
        )
        .unwrap();
        assert_eq!(network.cidr, "192.168.50.0/24");
    }

    #[test]
    fn rejects_public_or_overly_broad_networks() {
        assert!(network_from_values(
            "en0",
            Ipv4Addr::new(8, 8, 8, 8),
            Ipv4Addr::new(255, 255, 255, 0),
            Ipv4Addr::new(192, 168, 1, 1)
        )
        .is_err());
        assert!(network_from_values(
            "en0",
            Ipv4Addr::new(10, 0, 1, 2),
            Ipv4Addr::new(255, 255, 0, 0),
            Ipv4Addr::new(10, 0, 0, 1)
        )
        .is_err());
    }

    #[test]
    fn rejects_vpn_and_virtual_interfaces() {
        for interface in ["utun3", "lo0", "awdl0", "llw0", "wg0"] {
            assert!(network_from_values(
                interface,
                Ipv4Addr::new(192, 168, 50, 23),
                Ipv4Addr::new(255, 255, 255, 0),
                Ipv4Addr::new(192, 168, 50, 1),
            )
            .is_err());
        }
    }

    #[test]
    fn vpn_default_route_falls_back_to_physical_interfaces() {
        assert_eq!(physical_interface_candidates("utun3"), vec!["en0", "en1"]);
        assert_eq!(physical_interface_candidates("en1"), vec!["en1", "en0"]);
    }
}
