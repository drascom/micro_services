use super::{normalize_hostname, warning, ParseOutput};
use chrono::{DateTime, Utc};
use inet_viewer_shared::{
    DiscoverySourceKind, MacAddress, Observation, ObservationStatus, Service, TransportProtocol,
};
use std::{collections::HashMap, net::IpAddr, str::FromStr};

pub fn parse_avahi_browse(input: &str, observed_at: DateTime<Utc>) -> ParseOutput {
    let mut output = ParseOutput::default();
    for (index, raw) in input.lines().enumerate() {
        let line_number = index + 1;
        let line = raw.trim();
        if line.is_empty() || line.starts_with('+') || line.starts_with('-') {
            continue;
        }
        if !line.starts_with('=') {
            output
                .warnings
                .push(warning(line_number, "unknown Avahi record type", raw));
            continue;
        }
        let fields: Vec<_> = line.split(';').collect();
        if fields.len() < 9 {
            output
                .warnings
                .push(warning(line_number, "malformed resolved Avahi record", raw));
            continue;
        }
        let service_name = avahi_unescape(fields[3]);
        let service_type = normalize_service_type(&avahi_unescape(fields[4]));
        let hostname = normalize_hostname(&avahi_unescape(fields[6]));
        let Ok(ip) = fields[7].parse::<IpAddr>() else {
            output
                .warnings
                .push(warning(line_number, "invalid Avahi IP address", raw));
            continue;
        };
        let Ok(port) = fields[8].parse::<u16>() else {
            output
                .warnings
                .push(warning(line_number, "invalid Avahi service port", raw));
            continue;
        };
        let txt = parse_txt(fields.get(9).copied().unwrap_or_default());
        let mac_address = ["mac", "mac_address", "deviceid", "bt", "rpba"]
            .iter()
            .find_map(|key| {
                txt.get(*key)
                    .and_then(|value| MacAddress::from_str(value).ok())
            });
        let model = ["model", "md", "mdl", "usb_mdl", "ty"]
            .iter()
            .find_map(|key| txt.get(*key).filter(|value| !value.is_empty()).cloned());
        let manufacturer = ["manufacturer", "mfg", "usb_mfg", "brand", "vendor"]
            .iter()
            .find_map(|key| txt.get(*key).filter(|value| !value.is_empty()).cloned());
        let source_id = ["id", "uuid", "serial", "deviceid", "mac", "mac_address"]
            .iter()
            .find_map(|key| txt.get(*key).filter(|value| !value.is_empty()).cloned())
            .or_else(|| mac_address.as_ref().map(ToString::to_string));
        let protocol = if service_type.ends_with("._udp") {
            TransportProtocol::Udp
        } else {
            TransportProtocol::Tcp
        };
        let service = Service {
            service_type: service_type.clone(),
            name: (!service_name.is_empty()).then_some(service_name),
            protocol,
            port,
            hostname: hostname.clone(),
            txt: txt.clone(),
        };
        let mut attributes = HashMap::new();
        attributes.insert("interface".into(), fields[1].into());
        attributes.insert("address_family".into(), fields[2].to_ascii_lowercase());
        attributes.insert("domain".into(), fields[5].to_ascii_lowercase());
        output.observations.push(Observation {
            source: DiscoverySourceKind::Mdns,
            source_id,
            hostname,
            ip_addresses: vec![ip],
            mac_address,
            manufacturer,
            model,
            status: ObservationStatus::Online,
            services: vec![service],
            observed_at,
            attributes,
        });
    }
    output
}

fn normalize_service_type(value: &str) -> String {
    match value.trim().to_ascii_lowercase().as_str() {
        "web site" => "_http._tcp".into(),
        "secure web site" => "_https._tcp".into(),
        "internet printer" => "_ipp._tcp".into(),
        "secure internet printer" => "_ipps._tcp".into(),
        "unix printer" => "_printer._tcp".into(),
        "pdl printer" => "_pdl-datastream._tcp".into(),
        "apple file sharing" => "_afpovertcp._tcp".into(),
        "microsoft windows network" => "_smb._tcp".into(),
        "airplay remote video" => "_airplay._tcp".into(),
        "airtunes remote audio" => "_raop._tcp".into(),
        other => other.into(),
    }
}

fn avahi_unescape(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut result = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'\\'
            && index + 3 < bytes.len()
            && bytes[index + 1..index + 4].iter().all(u8::is_ascii_digit)
        {
            let number = (bytes[index + 1] - b'0') * 100
                + (bytes[index + 2] - b'0') * 10
                + (bytes[index + 3] - b'0');
            result.push(number);
            index += 4;
        } else {
            result.push(bytes[index]);
            index += 1;
        }
    }
    String::from_utf8_lossy(&result).into_owned()
}

fn parse_txt(value: &str) -> HashMap<String, String> {
    let mut result = HashMap::new();
    let mut current = String::new();
    let mut quoted = false;
    for character in value.chars().chain(std::iter::once(' ')) {
        match character {
            '"' => quoted = !quoted,
            c if c.is_whitespace() && !quoted => {
                if !current.is_empty() {
                    let decoded = avahi_unescape(&current);
                    let (key, value) = decoded.split_once('=').unwrap_or((&decoded, ""));
                    result.insert(key.to_ascii_lowercase(), value.to_owned());
                    current.clear();
                }
            }
            c => current.push(c),
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn avahi_decimal_escapes_are_decoded_as_utf8() {
        assert_eq!(avahi_unescape("Emin\\226\\128\\153s\\032Mac"), "Emin’s Mac");
    }
}
