//! Hostname resolution for live LAN addresses, from inside the app process:
//! mDNS reverse PTR (unicast response), NetBIOS node status, SSDP
//! description XML and the system resolver (PTR). Cross-platform.
use std::{
    collections::{BTreeMap, HashMap, HashSet},
    net::{IpAddr, Ipv4Addr, SocketAddr},
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{TcpStream, UdpSocket},
    time,
};

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct HostNames {
    pub mdns: Option<String>,
    pub netbios: Option<String>,
    pub ssdp_friendly_name: Option<String>,
    pub ssdp_model: Option<String>,
    pub ssdp_manufacturer: Option<String>,
    pub ptr: Option<String>,
}

impl HostNames {
    /// Best hostname and its method: mDNS, NetBIOS, then reverse DNS.
    pub fn best_hostname(&self) -> Option<(String, &'static str)> {
        self.mdns
            .clone()
            .map(|name| (name, "mdns"))
            .or_else(|| self.netbios.clone().map(|name| (name, "netbios")))
            .or_else(|| self.ptr.clone().map(|name| (name, "ptr")))
    }

    /// `name.<method>` observation attributes.
    pub fn attributes(&self) -> Vec<(String, String)> {
        [
            ("name.mdns", &self.mdns),
            ("name.netbios", &self.netbios),
            ("name.ssdp", &self.ssdp_friendly_name),
            ("name.ptr", &self.ptr),
        ]
        .into_iter()
        .filter_map(|(key, value)| value.clone().map(|value| (key.to_owned(), value)))
        .collect()
    }
}

#[derive(Clone, Debug, Default)]
pub struct NameReport {
    pub names: BTreeMap<Ipv4Addr, HostNames>,
    pub mdns: usize,
    pub netbios: usize,
    pub ssdp: usize,
    pub ptr: usize,
    pub errors: Vec<String>,
    pub elapsed: Duration,
}

/// Resolves names for `addresses` with every method in parallel, bounded by
/// `timeout`.
pub async fn resolve_names(addresses: &[Ipv4Addr], timeout: Duration) -> NameReport {
    let started = Instant::now();
    let wait = timeout.saturating_sub(Duration::from_millis(300));
    let (mdns, netbios, ssdp, ptr) = tokio::join!(
        mdns_reverse(addresses, wait),
        netbios_names(addresses, wait),
        ssdp_names(addresses, wait),
        super::reverse_lookup(addresses, wait),
    );
    let mut report = NameReport::default();
    let names = &mut report.names;
    macro_rules! entry {
        ($address:expr) => {
            names.entry($address).or_default()
        };
    }
    let mut errors = Vec::new();
    let mut counts = [0usize; 4];
    match mdns {
        Ok(found) => {
            counts[0] = found.len();
            for (address, name) in found {
                entry!(address).mdns = Some(name);
            }
        }
        Err(error) => errors.push(format!("mdns: {error}")),
    }
    match netbios {
        Ok(found) => {
            counts[1] = found.len();
            for (address, name) in found {
                entry!(address).netbios = Some(name);
            }
        }
        Err(error) => errors.push(format!("netbios: {error}")),
    }
    match ssdp {
        Ok(found) => {
            counts[2] = found.len();
            for (address, device) in found {
                let item = entry!(address);
                item.ssdp_friendly_name = device.friendly_name;
                item.ssdp_model = device.model_name;
                item.ssdp_manufacturer = device.manufacturer;
            }
        }
        Err(error) => errors.push(format!("ssdp: {error}")),
    }
    counts[3] = ptr.len();
    for (address, name) in ptr {
        entry!(address).ptr = Some(name);
    }
    [report.mdns, report.netbios, report.ssdp, report.ptr] = counts;
    report.errors = errors;
    report.elapsed = started.elapsed();
    report
}

// ---------------------------------------------------------------- DNS ----

fn encode_name(name: &str, out: &mut Vec<u8>) {
    for label in name.trim_end_matches('.').split('.') {
        out.push(label.len() as u8);
        out.extend_from_slice(label.as_bytes());
    }
    out.push(0);
}

pub fn reverse_name(address: Ipv4Addr) -> String {
    let [a, b, c, d] = address.octets();
    format!("{d}.{c}.{b}.{a}.in-addr.arpa")
}

/// DNS query for the PTR record of `address`, with the mDNS unicast-response
/// (QU) bit set.
pub fn mdns_ptr_query(id: u16, address: Ipv4Addr) -> Vec<u8> {
    let mut packet = Vec::with_capacity(48);
    packet.extend_from_slice(&id.to_be_bytes());
    packet.extend_from_slice(&[0, 0, 0, 1, 0, 0, 0, 0, 0, 0]);
    encode_name(&reverse_name(address), &mut packet);
    packet.extend_from_slice(&12u16.to_be_bytes());
    packet.extend_from_slice(&0x8001u16.to_be_bytes());
    packet
}

/// Reads a (possibly compressed) DNS name at `offset`; returns it and the
/// offset after the name in the original position.
fn read_name(packet: &[u8], offset: usize) -> Option<(String, usize)> {
    let mut labels = Vec::new();
    let mut position = offset;
    let mut end = None;
    for _ in 0..64 {
        let length = *packet.get(position)? as usize;
        if length == 0 {
            return Some((labels.join("."), end.unwrap_or(position + 1)));
        }
        if length & 0xc0 == 0xc0 {
            let pointer = ((length & 0x3f) << 8) | *packet.get(position + 1)? as usize;
            end.get_or_insert(position + 2);
            position = pointer;
            continue;
        }
        let label = packet.get(position + 1..position + 1 + length)?;
        labels.push(String::from_utf8_lossy(label).into_owned());
        position += 1 + length;
    }
    None
}

/// Extracts `(reverse name -> PTR target)` answers from a DNS response.
pub fn parse_ptr_answers(packet: &[u8]) -> Vec<(Ipv4Addr, String)> {
    let mut found = Vec::new();
    if packet.len() < 12 || packet[2] & 0x80 == 0 {
        return found;
    }
    let count = |index: usize| u16::from_be_bytes([packet[index], packet[index + 1]]) as usize;
    let (questions, records) = (count(4), count(6) + count(8) + count(10));
    let mut offset = 12;
    for _ in 0..questions {
        let Some((_, next)) = read_name(packet, offset) else {
            return found;
        };
        offset = next + 4;
    }
    for _ in 0..records {
        let Some((name, next)) = read_name(packet, offset) else {
            break;
        };
        let Some(header) = packet.get(next..next + 10) else {
            break;
        };
        let kind = u16::from_be_bytes([header[0], header[1]]);
        let length = u16::from_be_bytes([header[8], header[9]]) as usize;
        let data = next + 10;
        if kind == 12 {
            if let (Some(address), Some((target, _))) =
                (address_from_reverse(&name), read_name(packet, data))
            {
                let target = target.trim_end_matches('.').to_ascii_lowercase();
                if !target.is_empty() {
                    found.push((address, target));
                }
            }
        }
        offset = data + length;
    }
    found
}

fn address_from_reverse(name: &str) -> Option<Ipv4Addr> {
    let lower = name.to_ascii_lowercase();
    let stem = lower.trim_end_matches('.').strip_suffix(".in-addr.arpa")?;
    let mut octets: Vec<u8> = stem
        .split('.')
        .map(str::parse)
        .collect::<Result<_, _>>()
        .ok()?;
    if octets.len() != 4 {
        return None;
    }
    octets.reverse();
    Some(Ipv4Addr::new(octets[0], octets[1], octets[2], octets[3]))
}

/// Sends the PTR query to each host's port 5353 and to the mDNS group;
/// responders answer the (non-5353) source port directly.
async fn mdns_reverse(
    addresses: &[Ipv4Addr],
    wait: Duration,
) -> std::io::Result<HashMap<Ipv4Addr, String>> {
    let socket = UdpSocket::bind("0.0.0.0:0").await?;
    let wanted: HashSet<_> = addresses.iter().copied().collect();
    let group = SocketAddr::from((Ipv4Addr::new(224, 0, 0, 251), 5353));
    for (index, address) in addresses.iter().enumerate() {
        let query = mdns_ptr_query(index as u16, *address);
        let _ = socket.send_to(&query, SocketAddr::from((*address, 5353))).await;
        let _ = socket.send_to(&query, group).await;
    }
    let mut found = HashMap::new();
    collect_udp(&socket, wait, |packet, _| {
        for (address, name) in parse_ptr_answers(packet) {
            if wanted.contains(&address) {
                found.entry(address).or_insert(name);
            }
        }
        found.len() == wanted.len()
    })
    .await;
    Ok(found)
}

async fn collect_udp(
    socket: &UdpSocket,
    wait: Duration,
    mut handle: impl FnMut(&[u8], SocketAddr) -> bool,
) {
    let deadline = time::Instant::now() + wait;
    let mut buffer = vec![0u8; 9000];
    loop {
        match time::timeout_at(deadline, socket.recv_from(&mut buffer)).await {
            Ok(Ok((length, from))) => {
                if handle(&buffer[..length], from) {
                    return;
                }
            }
            Ok(Err(_)) => continue,
            Err(_) => return,
        }
    }
}

// ------------------------------------------------------------ NetBIOS ----

/// NBSTAT node status request for the wildcard name `*`.
pub fn nbstat_request(id: u16) -> Vec<u8> {
    let mut packet = Vec::with_capacity(50);
    packet.extend_from_slice(&id.to_be_bytes());
    packet.extend_from_slice(&[0, 0, 0, 1, 0, 0, 0, 0, 0, 0]);
    packet.push(32);
    let mut name = [0u8; 16];
    name[0] = b'*';
    for byte in name {
        packet.push(b'A' + (byte >> 4));
        packet.push(b'A' + (byte & 0x0f));
    }
    packet.push(0);
    packet.extend_from_slice(&[0x00, 0x21, 0x00, 0x01]);
    packet
}

/// Workstation name (suffix 0x00, unique) from an NBSTAT response.
pub fn parse_nbstat_response(packet: &[u8]) -> Option<String> {
    if packet.len() < 12 || packet[2] & 0x80 == 0 {
        return None;
    }
    let (_, next) = read_name(packet, 12)?;
    let header = packet.get(next..next + 10)?;
    if u16::from_be_bytes([header[0], header[1]]) != 0x21 {
        return None;
    }
    let mut offset = next + 10;
    let count = *packet.get(offset)? as usize;
    offset += 1;
    let mut fallback = None;
    for index in 0..count {
        let entry = packet.get(offset + index * 18..offset + index * 18 + 18)?;
        let name = String::from_utf8_lossy(&entry[..15]).trim().to_owned();
        let suffix = entry[15];
        let group = entry[16] & 0x80 != 0;
        if name.is_empty() || group {
            continue;
        }
        if suffix == 0x00 {
            return Some(name);
        }
        if suffix == 0x20 {
            fallback.get_or_insert(name);
        }
    }
    fallback
}

async fn netbios_names(
    addresses: &[Ipv4Addr],
    wait: Duration,
) -> std::io::Result<HashMap<Ipv4Addr, String>> {
    let socket = UdpSocket::bind("0.0.0.0:0").await?;
    let wanted: HashSet<_> = addresses.iter().copied().collect();
    for (index, address) in addresses.iter().enumerate() {
        let _ = socket
            .send_to(&nbstat_request(index as u16), SocketAddr::from((*address, 137)))
            .await;
    }
    let mut found = HashMap::new();
    collect_udp(&socket, wait, |packet, from| {
        if let IpAddr::V4(source) = from.ip() {
            if wanted.contains(&source) {
                if let Some(name) = parse_nbstat_response(packet) {
                    found.insert(source, name);
                }
            }
        }
        found.len() == wanted.len()
    })
    .await;
    Ok(found)
}

// --------------------------------------------------------------- SSDP ----

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SsdpDevice {
    pub friendly_name: Option<String>,
    pub model_name: Option<String>,
    pub manufacturer: Option<String>,
}

pub const SSDP_SEARCH: &str = "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 1\r\nST: ssdp:all\r\n\r\n";

/// LOCATION header of an SSDP response.
pub fn parse_ssdp_location(response: &str) -> Option<String> {
    response.lines().find_map(|line| {
        let (key, value) = line.split_once(':')?;
        key.trim()
            .eq_ignore_ascii_case("location")
            .then(|| value.trim().to_owned())
            .filter(|value| !value.is_empty())
    })
}

fn xml_unescape(value: &str) -> String {
    value
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&apos;", "'")
        .replace("&amp;", "&")
}

fn xml_tag(xml: &str, tag: &str) -> Option<String> {
    let open = format!("<{tag}>");
    let start = xml.find(&open)? + open.len();
    let end = xml[start..].find("</")? + start;
    let value = xml_unescape(xml[start..end].trim());
    (!value.is_empty()).then_some(value)
}

/// friendlyName / modelName / manufacturer of the root UPnP device.
pub fn parse_ssdp_description(xml: &str) -> SsdpDevice {
    let device = xml.find("<device>").map_or(xml, |index| &xml[index..]);
    SsdpDevice {
        friendly_name: xml_tag(device, "friendlyName"),
        model_name: xml_tag(device, "modelName"),
        manufacturer: xml_tag(device, "manufacturer"),
    }
}

/// `http://host:port/path` -> (host, port, path).
fn split_http_url(url: &str) -> Option<(String, u16, String)> {
    let rest = url.strip_prefix("http://")?;
    let (authority, path) = rest.split_once('/').map_or((rest, ""), |(a, p)| (a, p));
    let (host, port) = match authority.rsplit_once(':') {
        Some((host, port)) => (host, port.parse().ok()?),
        None => (authority, 80),
    };
    Some((host.to_owned(), port, format!("/{path}")))
}

async fn http_get(address: Ipv4Addr, port: u16, path: &str, host: &str) -> Option<String> {
    let mut stream = TcpStream::connect(SocketAddr::from((address, port))).await.ok()?;
    let request = format!(
        "GET {path} HTTP/1.0\r\nHost: {host}:{port}\r\nUser-Agent: inet-viewer\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).await.ok()?;
    let mut body = Vec::new();
    let mut chunk = [0u8; 8192];
    while body.len() < 256 * 1024 {
        match stream.read(&mut chunk).await {
            Ok(0) | Err(_) => break,
            Ok(length) => body.extend_from_slice(&chunk[..length]),
        }
    }
    let text = String::from_utf8_lossy(&body).into_owned();
    Some(text.split_once("\r\n\r\n").map_or(text.clone(), |(_, body)| body.to_owned()))
}

async fn ssdp_names(
    addresses: &[Ipv4Addr],
    wait: Duration,
) -> std::io::Result<HashMap<Ipv4Addr, SsdpDevice>> {
    let socket = UdpSocket::bind("0.0.0.0:0").await?;
    let wanted: HashSet<_> = addresses.iter().copied().collect();
    let group = SocketAddr::from((Ipv4Addr::new(239, 255, 255, 250), 1900));
    for _ in 0..2 {
        socket.send_to(SSDP_SEARCH.as_bytes(), group).await?;
    }
    // Half the budget listens for answers, the rest fetches descriptions.
    let mut locations: HashMap<Ipv4Addr, String> = HashMap::new();
    collect_udp(&socket, wait / 2, |packet, from| {
        if let IpAddr::V4(source) = from.ip() {
            if wanted.contains(&source) {
                if let Some(location) = parse_ssdp_location(&String::from_utf8_lossy(packet)) {
                    locations.entry(source).or_insert(location);
                }
            }
        }
        false
    })
    .await;
    let fetch_wait = wait / 2;
    let fetches = locations.into_iter().map(|(address, location)| async move {
        // Only fetch from the host that answered (no redirects elsewhere).
        let (host, port, path) = split_http_url(&location)?;
        if host.parse::<Ipv4Addr>().ok() != Some(address) {
            return None;
        }
        let body = time::timeout(fetch_wait, http_get(address, port, &path, &host))
            .await
            .ok()
            .flatten()?;
        let device = parse_ssdp_description(&body);
        (device != SsdpDevice::default()).then_some((address, device))
    });
    Ok(futures_util::future::join_all(fetches)
        .await
        .into_iter()
        .flatten()
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ptr_query_has_qu_bit_and_reverse_name() {
        let query = mdns_ptr_query(7, Ipv4Addr::new(192, 168, 0, 20));
        let (name, next) = read_name(&query, 12).unwrap();
        assert_eq!(name, "20.0.168.192.in-addr.arpa");
        assert_eq!(&query[next..next + 4], &[0, 12, 0x80, 1]);
    }

    #[test]
    fn ptr_answer_with_compression_is_parsed() {
        let mut packet = vec![0, 0, 0x84, 0, 0, 0, 0, 1, 0, 0, 0, 0];
        encode_name("20.0.168.192.in-addr.arpa", &mut packet);
        packet.extend_from_slice(&[0, 12, 0x80, 1, 0, 0, 0, 120]);
        let mut target = Vec::new();
        encode_name("Salon-TV.local", &mut target);
        packet.extend_from_slice(&(target.len() as u16).to_be_bytes());
        packet.extend_from_slice(&target);
        assert_eq!(
            parse_ptr_answers(&packet),
            vec![(Ipv4Addr::new(192, 168, 0, 20), "salon-tv.local".into())]
        );
        // Compressed target pointing back at "local" inside the answer name.
        let mut compressed = vec![0, 0, 0x84, 0, 0, 0, 0, 1, 0, 0, 0, 0];
        encode_name("5.0.0.10.in-addr.arpa", &mut compressed);
        compressed.extend_from_slice(&[0, 12, 0, 1, 0, 0, 0, 120, 0, 6]);
        compressed.extend_from_slice(&[3, b'n', b'a', b's', 0xc0, 12]);
        let parsed = parse_ptr_answers(&compressed);
        assert_eq!(parsed[0].0, Ipv4Addr::new(10, 0, 0, 5));
        assert!(parsed[0].1.starts_with("nas.5.0.0.10"));
    }

    #[test]
    fn nbstat_request_encodes_wildcard() {
        let request = nbstat_request(1);
        assert_eq!(request[12], 32);
        assert_eq!(&request[13..15], b"CK");
        assert_eq!(&request[15..17], b"AA");
        assert_eq!(&request[request.len() - 4..], &[0, 0x21, 0, 1]);
    }

    #[test]
    fn nbstat_fixture_yields_workstation_name() {
        let packet = include_bytes!("../../../../fixtures/nbstat-response.bin");
        assert_eq!(parse_nbstat_response(packet).as_deref(), Some("DESKTOP-ABC123"));
    }

    #[test]
    fn ssdp_location_and_description_are_parsed() {
        let response = "HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\nLocation: http://192.168.0.40:49152/description.xml\r\nST: upnp:rootdevice\r\n\r\n";
        assert_eq!(
            parse_ssdp_location(response).as_deref(),
            Some("http://192.168.0.40:49152/description.xml")
        );
        assert_eq!(
            split_http_url("http://192.168.0.40:49152/description.xml"),
            Some(("192.168.0.40".into(), 49152, "/description.xml".into()))
        );
        let xml = include_str!("../../../../fixtures/ssdp-description.xml");
        let device = parse_ssdp_description(xml);
        assert_eq!(device.friendly_name.as_deref(), Some("Salon TV & Ses"));
        assert_eq!(device.model_name.as_deref(), Some("UE55TU7000"));
        assert_eq!(device.manufacturer.as_deref(), Some("Samsung Electronics"));
    }

    #[test]
    fn best_hostname_prefers_mdns_then_netbios_then_ptr() {
        let mut names = HostNames {
            ptr: Some("host.lan".into()),
            ..HostNames::default()
        };
        assert_eq!(names.best_hostname(), Some(("host.lan".into(), "ptr")));
        names.netbios = Some("NAS".into());
        assert_eq!(names.best_hostname(), Some(("NAS".into(), "netbios")));
        names.mdns = Some("nas.local".into());
        assert_eq!(names.best_hostname(), Some(("nas.local".into(), "mdns")));
    }
}
