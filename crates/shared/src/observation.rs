use crate::{DiscoverySourceKind, MacAddress, Service};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::{collections::HashMap, net::IpAddr};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ObservationStatus {
    Online,
    Idle,
    Connecting,
    Offline,
    Unknown,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Observation {
    pub source: DiscoverySourceKind,
    pub source_id: Option<String>,
    pub hostname: Option<String>,
    #[serde(default)]
    pub ip_addresses: Vec<IpAddr>,
    pub mac_address: Option<MacAddress>,
    pub manufacturer: Option<String>,
    pub model: Option<String>,
    pub status: ObservationStatus,
    #[serde(default)]
    pub services: Vec<Service>,
    pub observed_at: DateTime<Utc>,
    #[serde(default)]
    pub attributes: HashMap<String, String>,
}
