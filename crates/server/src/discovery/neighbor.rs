use super::{warning, ParseOutput};
use chrono::{DateTime, Utc};
use inet_viewer_shared::{DiscoverySourceKind, Observation, ObservationStatus};
use std::{collections::HashMap, net::IpAddr, str::FromStr};

pub fn parse_ip_neigh(input: &str, observed_at: DateTime<Utc>) -> ParseOutput {
    let mut output = ParseOutput::default();
    for (index, raw) in input.lines().enumerate() {
        let line_number = index + 1;
        let line = raw.trim();
        if line.is_empty() {
            continue;
        }
        let fields: Vec<_> = line.split_whitespace().collect();
        let Some(ip) = fields
            .first()
            .and_then(|value| value.parse::<IpAddr>().ok())
        else {
            output
                .warnings
                .push(warning(line_number, "invalid neighbor IP address", raw));
            continue;
        };
        let Some(dev_index) = fields.iter().position(|field| *field == "dev") else {
            output
                .warnings
                .push(warning(line_number, "neighbor line has no interface", raw));
            continue;
        };
        let Some(interface) = fields.get(dev_index + 1) else {
            output
                .warnings
                .push(warning(line_number, "neighbor interface is missing", raw));
            continue;
        };
        let state = fields
            .last()
            .copied()
            .unwrap_or("UNKNOWN")
            .to_ascii_uppercase();
        let status = match state.as_str() {
            "REACHABLE" | "PERMANENT" | "NOARP" => ObservationStatus::Online,
            "STALE" | "DELAY" | "PROBE" => ObservationStatus::Idle,
            "INCOMPLETE" => ObservationStatus::Connecting,
            "FAILED" => ObservationStatus::Offline,
            _ => {
                output.warnings.push(warning(
                    line_number,
                    format!("unknown neighbor state {state}"),
                    raw,
                ));
                ObservationStatus::Unknown
            }
        };
        let mac_address = fields
            .iter()
            .position(|field| *field == "lladdr")
            .and_then(|position| fields.get(position + 1))
            .and_then(
                |value| match inet_viewer_shared::MacAddress::from_str(value) {
                    Ok(mac) => Some(mac),
                    Err(_) => {
                        output.warnings.push(warning(
                            line_number,
                            "invalid neighbor MAC address",
                            raw,
                        ));
                        None
                    }
                },
            );
        let mut attributes = HashMap::new();
        attributes.insert("interface".into(), (*interface).into());
        attributes.insert("neighbor_state".into(), state);
        if fields.contains(&"router") {
            attributes.insert("router".into(), "true".into());
        }
        output.observations.push(Observation {
            source: DiscoverySourceKind::Neighbor,
            source_id: mac_address.as_ref().map(ToString::to_string),
            hostname: None,
            ip_addresses: vec![ip],
            mac_address,
            manufacturer: None,
            model: None,
            status,
            services: Vec::new(),
            observed_at,
            attributes,
        });
    }
    output
}
