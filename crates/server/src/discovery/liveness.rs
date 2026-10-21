//! ARP-independent, cross-platform host liveness: unprivileged ICMP echo
//! (where the OS allows it) plus TCP connects to common ports. Runs inside the
//! calling process, so OS network privacy applies to the app itself.
use futures_util::{stream, StreamExt};
use std::{
    collections::{BTreeMap, HashMap},
    io::ErrorKind,
    net::{IpAddr, Ipv4Addr, SocketAddr},
    time::{Duration, Instant},
};
use tokio::{net::TcpStream, time};

/// Ports that most LAN devices answer on (accept or refuse).
pub const DEFAULT_PORTS: &[u16] = &[80, 443, 22, 445, 139, 53, 8080, 62078, 7000, 554];

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum LivenessMethod {
    Icmp,
    Tcp(u16),
}

impl std::fmt::Display for LivenessMethod {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Icmp => f.write_str("icmp"),
            Self::Tcp(port) => write!(f, "tcp:{port}"),
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct LivenessReport {
    pub alive: BTreeMap<Ipv4Addr, LivenessMethod>,
    pub icmp_status: String,
    pub icmp_alive: usize,
    pub tcp_alive: usize,
    pub tcp_errors: BTreeMap<String, usize>,
    pub elapsed: Duration,
}

#[derive(Clone, Debug)]
pub struct LivenessConfig {
    pub ports: Vec<u16>,
    pub connect_timeout: Duration,
    /// Hosts probed at once; each opens up to `ports.len()` sockets.
    pub host_concurrency: usize,
    pub icmp_wait: Duration,
}

impl Default for LivenessConfig {
    fn default() -> Self {
        Self {
            ports: DEFAULT_PORTS.to_vec(),
            connect_timeout: Duration::from_millis(700),
            host_concurrency: 16,
            icmp_wait: Duration::from_millis(1500),
        }
    }
}

/// connect() OK or refused means a host answered; anything else does not.
pub fn connect_result_is_alive(result: &std::io::Result<()>) -> bool {
    match result {
        Ok(()) => true,
        Err(error) => {
            error.kind() == ErrorKind::ConnectionRefused
                || error.raw_os_error() == Some(libc::ECONNREFUSED)
                || error.raw_os_error() == Some(libc::ECONNRESET)
        }
    }
}

fn error_label(error: &std::io::Error) -> String {
    match error.raw_os_error() {
        Some(code) if code == libc::EHOSTDOWN => "EHOSTDOWN".into(),
        Some(code) if code == libc::EHOSTUNREACH => "EHOSTUNREACH".into(),
        Some(code) => format!("errno{code}"),
        None => format!("{:?}", error.kind()),
    }
}

async fn probe_host(
    address: Ipv4Addr,
    ports: &[u16],
    timeout: Duration,
) -> (Option<u16>, Vec<String>) {
    let mut attempts = stream::iter(ports.iter().copied())
        .map(|port| async move {
            let result =
                match time::timeout(timeout, TcpStream::connect(SocketAddr::from((address, port))))
                    .await
                {
                    Ok(Ok(_)) => Ok(()),
                    Ok(Err(error)) => Err(error),
                    Err(_) => Err(std::io::Error::from(ErrorKind::TimedOut)),
                };
            (port, result)
        })
        .buffer_unordered(ports.len().max(1));
    let mut errors = Vec::new();
    while let Some((port, result)) = attempts.next().await {
        if connect_result_is_alive(&result) {
            return (Some(port), errors);
        }
        if let Err(error) = &result {
            errors.push(error_label(error));
        }
    }
    (None, errors)
}

fn icmp_checksum(data: &[u8]) -> u16 {
    let mut sum = 0u32;
    for chunk in data.chunks(2) {
        let word = u16::from_be_bytes([chunk[0], *chunk.get(1).unwrap_or(&0)]);
        sum = sum.wrapping_add(u32::from(word));
    }
    while sum >> 16 != 0 {
        sum = (sum & 0xffff) + (sum >> 16);
    }
    !(sum as u16)
}

pub fn icmp_echo_request(identifier: u16, sequence: u16) -> Vec<u8> {
    let mut packet = vec![8, 0, 0, 0];
    packet.extend_from_slice(&identifier.to_be_bytes());
    packet.extend_from_slice(&sequence.to_be_bytes());
    packet.extend_from_slice(b"inet-viewer");
    let checksum = icmp_checksum(&packet);
    packet[2..4].copy_from_slice(&checksum.to_be_bytes());
    packet
}

/// True for an ICMP echo reply, with or without a leading IPv4 header
/// (macOS includes it on datagram ICMP sockets, Linux does not).
pub fn is_echo_reply(packet: &[u8]) -> bool {
    let offset = match packet.first() {
        Some(first) if first >> 4 == 4 && packet.len() >= 28 => usize::from(first & 0x0f) * 4,
        _ => 0,
    };
    packet.get(offset) == Some(&0)
}

/// Unprivileged ICMP sweep. Returns responding hosts or why it could not run.
fn icmp_sweep(targets: &[Ipv4Addr], wait: Duration) -> Result<Vec<Ipv4Addr>, String> {
    use socket2::{Domain, Protocol, Socket, Type};
    use std::mem::MaybeUninit;
    let socket = Socket::new(Domain::IPV4, Type::DGRAM, Some(Protocol::ICMPV4))
        .map_err(|error| format!("unavailable({})", error_label(&error)))?;
    socket
        .set_read_timeout(Some(Duration::from_millis(100)))
        .map_err(|error| format!("unavailable({})", error_label(&error)))?;
    let identifier = (std::process::id() & 0xffff) as u16;
    let mut send_errors = 0;
    for (sequence, target) in targets.iter().enumerate() {
        let packet = icmp_echo_request(identifier, sequence as u16);
        if socket
            .send_to(&packet, &SocketAddr::from((*target, 0)).into())
            .is_err()
        {
            send_errors += 1;
        }
    }
    if send_errors == targets.len() && !targets.is_empty() {
        return Err("send_failed".into());
    }
    let wanted: std::collections::HashSet<_> = targets.iter().copied().collect();
    let mut alive = Vec::new();
    let deadline = Instant::now() + wait;
    let mut buffer = [MaybeUninit::<u8>::uninit(); 1500];
    while Instant::now() < deadline {
        let Ok((length, from)) = socket.recv_from(&mut buffer) else {
            continue;
        };
        // SAFETY: recv_from initialised the first `length` bytes.
        let packet: Vec<u8> = buffer[..length]
            .iter()
            .map(|byte| unsafe { byte.assume_init() })
            .collect();
        let Some(IpAddr::V4(source)) = from.as_socket().map(|address| address.ip()) else {
            continue;
        };
        if is_echo_reply(&packet) && wanted.contains(&source) && !alive.contains(&source) {
            alive.push(source);
        }
    }
    Ok(alive)
}

pub async fn probe_liveness(targets: Vec<Ipv4Addr>, config: &LivenessConfig) -> LivenessReport {
    probe_liveness_with_progress(targets, config, |_, _| {}).await
}

/// [`probe_liveness`] that calls `on_progress(done, total)` as hosts are
/// settled (ICMP replies first, then each TCP-probed host).
pub async fn probe_liveness_with_progress(
    targets: Vec<Ipv4Addr>,
    config: &LivenessConfig,
    on_progress: impl Fn(usize, usize),
) -> LivenessReport {
    let started = Instant::now();
    let total = targets.len();
    on_progress(0, total);
    let mut report = LivenessReport::default();
    let icmp_targets = targets.clone();
    let wait = config.icmp_wait;
    match tokio::task::spawn_blocking(move || icmp_sweep(&icmp_targets, wait)).await {
        Ok(Ok(hosts)) => {
            report.icmp_status = "ok".into();
            report.icmp_alive = hosts.len();
            for host in hosts {
                report.alive.insert(host, LivenessMethod::Icmp);
            }
        }
        Ok(Err(status)) => report.icmp_status = status,
        Err(error) => report.icmp_status = format!("task_failed({error})"),
    }

    let remaining: Vec<_> = targets
        .into_iter()
        .filter(|target| !report.alive.contains_key(target))
        .collect();
    let ports = config.ports.clone();
    let timeout = config.connect_timeout;
    let mut done = report.alive.len();
    on_progress(done, total);
    let mut probes = stream::iter(remaining)
        .map(|address| {
            let ports = ports.clone();
            async move { (address, probe_host(address, &ports, timeout).await) }
        })
        .buffer_unordered(config.host_concurrency.max(1));
    let mut results = Vec::new();
    while let Some(result) = probes.next().await {
        done += 1;
        on_progress(done, total);
        results.push(result);
    }
    for (address, (port, errors)) in results {
        for error in errors {
            *report.tcp_errors.entry(error).or_default() += 1;
        }
        if let Some(port) = port {
            report.tcp_alive += 1;
            report.alive.insert(address, LivenessMethod::Tcp(port));
        }
    }
    report.elapsed = started.elapsed();
    report
}

/// Reverse DNS (PTR via the system resolver) for each address, bounded by
/// `timeout` overall; unanswered lookups are left out.
pub async fn reverse_lookup(addresses: &[Ipv4Addr], timeout: Duration) -> HashMap<Ipv4Addr, String> {
    let lookups = addresses.iter().copied().map(|address| async move {
        let name = tokio::task::spawn_blocking(move || reverse_name(address))
            .await
            .ok()
            .flatten();
        (address, name)
    });
    let collected = time::timeout(timeout, futures_util::future::join_all(lookups)).await;
    collected
        .unwrap_or_default()
        .into_iter()
        .filter_map(|(address, name)| name.map(|name| (address, name)))
        .collect()
}

#[cfg(unix)]
fn reverse_name(address: Ipv4Addr) -> Option<String> {
    let socket = socket2::SockAddr::from(SocketAddr::from((address, 0)));
    let mut host = [0 as libc::c_char; 1025];
    // SAFETY: valid sockaddr and output buffer sizes are passed.
    let rc = unsafe {
        libc::getnameinfo(
            socket.as_ptr().cast(),
            socket.len(),
            host.as_mut_ptr(),
            host.len() as libc::socklen_t,
            std::ptr::null_mut(),
            0,
            libc::NI_NAMEREQD,
        )
    };
    if rc != 0 {
        return None;
    }
    // SAFETY: getnameinfo wrote a NUL-terminated string.
    let name = unsafe { std::ffi::CStr::from_ptr(host.as_ptr()) }
        .to_string_lossy()
        .trim_end_matches('.')
        .to_ascii_lowercase();
    (!name.is_empty() && name.parse::<IpAddr>().is_err()).then_some(name)
}

#[cfg(not(unix))]
fn reverse_name(_address: Ipv4Addr) -> Option<String> {
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn refused_connects_count_as_alive() {
        assert!(connect_result_is_alive(&Ok(())));
        assert!(connect_result_is_alive(&Err(std::io::Error::from(
            ErrorKind::ConnectionRefused
        ))));
        assert!(!connect_result_is_alive(&Err(std::io::Error::from_raw_os_error(
            libc::EHOSTDOWN
        ))));
        assert!(!connect_result_is_alive(&Err(std::io::Error::from_raw_os_error(
            libc::EHOSTUNREACH
        ))));
        assert!(!connect_result_is_alive(&Err(std::io::Error::from(
            ErrorKind::TimedOut
        ))));
    }

    #[test]
    fn echo_request_has_valid_checksum() {
        let packet = icmp_echo_request(0x1234, 7);
        assert_eq!(packet[0], 8);
        assert_eq!(icmp_checksum(&packet), 0);
    }

    #[test]
    fn echo_reply_detected_with_and_without_ip_header() {
        let mut bare = icmp_echo_request(1, 1);
        bare[0] = 0;
        assert!(is_echo_reply(&bare));
        let mut with_header = vec![0x45u8; 1];
        with_header.extend_from_slice(&[0u8; 19]);
        with_header.extend_from_slice(&bare);
        assert!(is_echo_reply(&with_header));
        assert!(!is_echo_reply(&icmp_echo_request(1, 1)));
    }

    #[tokio::test]
    async fn local_listener_is_alive_and_closed_port_is_alive_by_refusal() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let (found, _) =
            probe_host(Ipv4Addr::LOCALHOST, &[port], Duration::from_millis(500)).await;
        assert_eq!(found, Some(port));
    }
}
