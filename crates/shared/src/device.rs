use std::{collections::HashMap, fmt, net::IpAddr, str::FromStr};

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct DeviceId(pub Uuid);

impl DeviceId {
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }
}

impl Default for DeviceId {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct MacAddress(String);

impl FromStr for MacAddress {
    type Err = &'static str;
    fn from_str(value: &str) -> Result<Self, Self::Err> {
        let value = value.trim();
        // macOS `arp` drops leading zeros per octet ("0:1a:2b:c:4d:5e").
        let parts: Vec<&str> = value.split([':', '-']).collect();
        let hex: String = if parts.len() == 6 {
            if parts
                .iter()
                .any(|part| part.is_empty() || part.len() > 2)
            {
                return Err("invalid MAC address");
            }
            parts.iter().map(|part| format!("{part:0>2}")).collect()
        } else if parts.len() == 1 {
            value.to_owned()
        } else {
            return Err("invalid MAC address");
        };
        if hex.len() != 12 || !hex.chars().all(|c| c.is_ascii_hexdigit()) {
            return Err("invalid MAC address");
        }
        let normalized = (0..6)
            .map(|i| hex[i * 2..i * 2 + 2].to_ascii_lowercase())
            .collect::<Vec<_>>()
            .join(":");
        Ok(Self(normalized))
    }
}

impl fmt::Display for MacAddress {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeviceType {
    Server,
    Computer,
    Phone,
    Tablet,
    Television,
    Printer,
    Network,
    Iot,
    #[default]
    Unknown,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeviceStatus {
    #[default]
    Unknown,
    Offline,
    Connecting,
    Idle,
    Online,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DiscoverySourceKind {
    Netbird,
    Neighbor,
    Mdns,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TransportProtocol {
    Tcp,
    Udp,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Service {
    pub service_type: String,
    pub name: Option<String>,
    pub protocol: TransportProtocol,
    pub port: u16,
    pub hostname: Option<String>,
    #[serde(default)]
    pub txt: HashMap<String, String>,
}

/// A name for a device and the method that produced it
/// (`mdns`, `netbios`, `ssdp`, `ptr`, `netbird`, ...).
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeviceName {
    pub name: String,
    pub source: String,
}

/// One open TCP port found by a user-triggered port scan.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct OpenPort {
    pub address: IpAddr,
    pub port: u16,
    pub protocol: TransportProtocol,
    pub service: Option<String>,
    /// `tcp` (connect scan) or `mdns` (port advertised over mDNS).
    pub source: String,
}

/// Last stored port scan of a device.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PortScan {
    pub scanned_at: DateTime<Utc>,
    #[serde(default)]
    pub addresses: Vec<IpAddr>,
    pub ports_checked: usize,
    pub elapsed_ms: u64,
    #[serde(default)]
    pub open_ports: Vec<OpenPort>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Device {
    pub id: DeviceId,
    pub display_name: String,
    #[serde(default)]
    pub hostnames: Vec<String>,
    #[serde(default)]
    pub names: Vec<DeviceName>,
    #[serde(default)]
    pub lan_ips: Vec<IpAddr>,
    pub netbird_ip: Option<IpAddr>,
    pub netbird_ipv6: Option<IpAddr>,
    #[serde(default)]
    pub mac_addresses: Vec<MacAddress>,
    pub manufacturer: Option<String>,
    pub model: Option<String>,
    pub device_type: DeviceType,
    pub status: DeviceStatus,
    #[serde(default)]
    pub sources: Vec<DiscoverySourceKind>,
    #[serde(default)]
    pub services: Vec<Service>,
    pub first_seen_at: DateTime<Utc>,
    pub last_seen_at: DateTime<Utc>,
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn mac_addresses_are_normalized() {
        assert_eq!(
            "AA-BB-CC-DD-EE-FF"
                .parse::<MacAddress>()
                .unwrap()
                .to_string(),
            "aa:bb:cc:dd:ee:ff"
        );
        assert!("bad".parse::<MacAddress>().is_err());
    }

    #[test]
    fn macos_short_octets_are_padded() {
        assert_eq!(
            "8c:9a:8f:e:49:ae".parse::<MacAddress>().unwrap().to_string(),
            "8c:9a:8f:0e:49:ae"
        );
        assert_eq!(
            "0:11:32:1c:b2:23".parse::<MacAddress>().unwrap().to_string(),
            "00:11:32:1c:b2:23"
        );
        assert_eq!(
            "aabbccddeeff".parse::<MacAddress>().unwrap().to_string(),
            "aa:bb:cc:dd:ee:ff"
        );
        assert!("1:2:3:4:5".parse::<MacAddress>().is_err());
        assert!("1:2:3:4:5:abc".parse::<MacAddress>().is_err());
    }
}
