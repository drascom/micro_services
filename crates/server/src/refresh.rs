//! Lightweight background status refresh of already-known devices.
//!
//! Unlike a full scan it never sweeps the subnet, browses mDNS or resolves
//! names: it only probes the stored LAN/NetBird IPv4 addresses (ICMP + short
//! TCP) and reads `netbird status`, then writes changed statuses.
use crate::{
    discovery::{probe_liveness, DiscoveryProvider, LivenessConfig},
    events::EventBus,
    repository::Repository,
};
use async_trait::async_trait;
use chrono::Utc;
use inet_viewer_shared::{Device, DeviceStatus, ObservationStatus};
use serde_json::json;
use std::{
    collections::{BTreeSet, HashMap},
    net::{IpAddr, Ipv4Addr},
    sync::{Arc, Mutex},
    time::Duration,
};

/// Consecutive failed probes before a LAN-only device is marked offline.
pub const OFFLINE_AFTER_MISSES: u8 = 2;

#[async_trait]
pub trait ReachabilityProbe: Send + Sync {
    /// Returns the subset of `targets` that answered.
    async fn alive(&self, targets: Vec<Ipv4Addr>) -> BTreeSet<Ipv4Addr>;
}

/// ICMP + short TCP liveness probe.
pub struct LivenessReachability(pub LivenessConfig);

impl Default for LivenessReachability {
    fn default() -> Self {
        Self(LivenessConfig {
            connect_timeout: Duration::from_millis(500),
            icmp_wait: Duration::from_millis(1000),
            ..LivenessConfig::default()
        })
    }
}

#[async_trait]
impl ReachabilityProbe for LivenessReachability {
    async fn alive(&self, targets: Vec<Ipv4Addr>) -> BTreeSet<Ipv4Addr> {
        probe_liveness(targets, &self.0).await.alive.into_keys().collect()
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct RefreshReport {
    pub probed: usize,
    pub changed: usize,
    pub netbird_ok: bool,
}

pub struct StatusRefresher {
    repository: Repository,
    events: EventBus,
    probe: Arc<dyn ReachabilityProbe>,
    netbird: Option<Arc<dyn DiscoveryProvider>>,
    misses: Mutex<HashMap<String, u8>>,
}

fn device_targets(device: &Device, lan_allowed: bool) -> Vec<Ipv4Addr> {
    let mut targets: Vec<Ipv4Addr> = Vec::new();
    if lan_allowed {
        targets.extend(device.lan_ips.iter().filter_map(|ip| match ip {
            IpAddr::V4(v4) => Some(*v4),
            IpAddr::V6(_) => None,
        }));
    }
    if let Some(IpAddr::V4(v4)) = device.netbird_ip {
        targets.push(v4);
    }
    targets.sort();
    targets.dedup();
    targets
}

fn status_from_netbird(status: ObservationStatus) -> DeviceStatus {
    match status {
        ObservationStatus::Online => DeviceStatus::Online,
        ObservationStatus::Idle => DeviceStatus::Idle,
        ObservationStatus::Connecting | ObservationStatus::Offline => DeviceStatus::Offline,
        ObservationStatus::Unknown => DeviceStatus::Unknown,
    }
}

impl StatusRefresher {
    pub fn new(
        repository: Repository,
        events: EventBus,
        probe: Arc<dyn ReachabilityProbe>,
        netbird: Option<Arc<dyn DiscoveryProvider>>,
    ) -> Self {
        Self {
            repository,
            events,
            probe,
            netbird,
            misses: Mutex::new(HashMap::new()),
        }
    }

    /// One silent refresh pass. `lan_allowed` gates LAN probing (macOS Local
    /// Network permission); NetBird status is read either way.
    pub async fn refresh(&self, lan_allowed: bool) -> anyhow::Result<RefreshReport> {
        let devices = self.repository.list_devices().await?;
        let mut report = RefreshReport::default();

        let mut netbird: HashMap<IpAddr, DeviceStatus> = HashMap::new();
        if let Some(provider) = &self.netbird {
            if let Ok(run) = provider.discover().await {
                report.netbird_ok = true;
                for observation in run.observations {
                    let status = status_from_netbird(observation.status);
                    for address in observation.ip_addresses {
                        netbird.insert(address, status);
                    }
                }
            }
        }

        let targets: BTreeSet<Ipv4Addr> = devices
            .iter()
            .flat_map(|device| device_targets(device, lan_allowed))
            .collect();
        report.probed = targets.len();
        let alive = if targets.is_empty() {
            BTreeSet::new()
        } else {
            self.probe.alive(targets.into_iter().collect()).await
        };

        let now = Utc::now();
        for device in &devices {
            let id = device.id.0.to_string();
            let targets = device_targets(device, lan_allowed);
            let reachable = targets.iter().any(|ip| alive.contains(ip));
            let peer = device.netbird_ip.and_then(|ip| netbird.get(&ip).copied());
            let next = if reachable || peer == Some(DeviceStatus::Online) {
                self.misses.lock().expect("misses").remove(&id);
                DeviceStatus::Online
            } else if peer == Some(DeviceStatus::Idle) {
                DeviceStatus::Idle
            } else if peer == Some(DeviceStatus::Offline) {
                DeviceStatus::Offline
            } else if !targets.is_empty() {
                let mut misses = self.misses.lock().expect("misses");
                let count = misses.entry(id.clone()).or_default();
                *count = count.saturating_add(1);
                if *count >= OFFLINE_AFTER_MISSES {
                    DeviceStatus::Offline
                } else {
                    device.status
                }
            } else {
                device.status
            };
            if next == device.status {
                continue;
            }
            let seen = (next == DeviceStatus::Online).then_some(now);
            self.repository.set_device_status(&id, next, seen).await?;
            let event_type = match next {
                DeviceStatus::Online => "device.online",
                DeviceStatus::Offline => "device.offline",
                _ => "device.updated",
            };
            let data = json!({"device_id":device.id,"changed_fields":["status","last_seen_at"],"source":"refresh"});
            self.repository
                .record_device_event(&id, event_type, &data)
                .await?;
            self.events.publish(event_type, data);
            report.changed += 1;
        }
        if report.changed > 0 {
            self.events
                .publish("snapshot.updated", json!({"source":"refresh","changed":report.changed}));
        }
        Ok(report)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        connect_database,
        discovery::{DiscoveryError, DiscoveryRun},
        merge::merge_observations,
    };
    use inet_viewer_shared::{DiscoverySourceKind, Observation};

    struct FakeProbe {
        alive: Mutex<BTreeSet<Ipv4Addr>>,
        calls: Mutex<Vec<Vec<Ipv4Addr>>>,
    }

    #[async_trait]
    impl ReachabilityProbe for FakeProbe {
        async fn alive(&self, targets: Vec<Ipv4Addr>) -> BTreeSet<Ipv4Addr> {
            self.calls.lock().unwrap().push(targets.clone());
            let alive = self.alive.lock().unwrap();
            targets.into_iter().filter(|ip| alive.contains(ip)).collect()
        }
    }

    struct FakeNetbird(ObservationStatus);

    #[async_trait]
    impl DiscoveryProvider for FakeNetbird {
        fn kind(&self) -> DiscoverySourceKind {
            DiscoverySourceKind::Netbird
        }
        async fn discover(&self) -> Result<DiscoveryRun, DiscoveryError> {
            Ok(DiscoveryRun {
                kind: DiscoverySourceKind::Netbird,
                observations: vec![observation(DiscoverySourceKind::Netbird, "peer", "100.64.0.5", self.0)],
                warnings: Vec::new(),
                started_at: Utc::now(),
                finished_at: Utc::now(),
            })
        }
    }

    fn observation(source: DiscoverySourceKind, host: &str, ip: &str, status: ObservationStatus) -> Observation {
        Observation {
            source,
            source_id: None,
            hostname: Some(host.into()),
            ip_addresses: vec![ip.parse().unwrap()],
            mac_address: None,
            manufacturer: None,
            model: None,
            status,
            services: Vec::new(),
            observed_at: Utc::now(),
            attributes: HashMap::new(),
        }
    }

    async fn repository() -> Repository {
        let repository = Repository::new(connect_database("sqlite::memory:").await.unwrap());
        let mut merged = merge_observations(vec![
            observation(DiscoverySourceKind::Neighbor, "tv", "192.168.0.20", ObservationStatus::Online),
            observation(DiscoverySourceKind::Netbird, "peer", "100.64.0.5", ObservationStatus::Online),
        ]);
        repository.save_merged(&mut merged).await.unwrap();
        repository
    }

    fn status_of(devices: &[Device], host: &str) -> DeviceStatus {
        devices
            .iter()
            .find(|device| device.hostnames.iter().any(|name| name.starts_with(host)) || device.display_name.starts_with(host))
            .map(|device| device.status)
            .expect("device")
    }

    #[tokio::test]
    async fn refresh_probes_only_known_addresses_and_writes_status_changes() {
        let repository = repository().await;
        let events = EventBus::default();
        let mut bus = events.subscribe();
        let probe = Arc::new(FakeProbe { alive: Mutex::new(BTreeSet::new()), calls: Mutex::new(Vec::new()) });
        let refresher = StatusRefresher::new(
            repository.clone(),
            events,
            probe.clone(),
            Some(Arc::new(FakeNetbird(ObservationStatus::Offline))),
        );

        let first = refresher.refresh(true).await.unwrap();
        // Only the two stored addresses are probed: no subnet sweep.
        assert_eq!(
            probe.calls.lock().unwrap().as_slice(),
            &[vec![Ipv4Addr::new(100, 64, 0, 5), Ipv4Addr::new(192, 168, 0, 20)]]
        );
        assert_eq!(first.changed, 1);
        let devices = repository.list_devices().await.unwrap();
        assert_eq!(status_of(&devices, "peer"), DeviceStatus::Offline);
        // One miss is not enough for a LAN-only device.
        assert_eq!(status_of(&devices, "tv"), DeviceStatus::Online);

        let second = refresher.refresh(true).await.unwrap();
        assert_eq!(second.changed, 1);
        let devices = repository.list_devices().await.unwrap();
        assert_eq!(status_of(&devices, "tv"), DeviceStatus::Offline);

        probe.alive.lock().unwrap().insert(Ipv4Addr::new(192, 168, 0, 20));
        let third = refresher.refresh(true).await.unwrap();
        assert_eq!(third.changed, 1);
        let devices = repository.list_devices().await.unwrap();
        assert_eq!(status_of(&devices, "tv"), DeviceStatus::Online);

        let mut kinds = Vec::new();
        while let Ok(event) = bus.try_recv() {
            kinds.push(event.event_type);
        }
        assert!(kinds.contains(&"device.offline".to_string()));
        assert!(kinds.contains(&"device.online".to_string()));
        assert_eq!(kinds.iter().filter(|kind| *kind == "snapshot.updated").count(), 3);
    }

    #[tokio::test]
    async fn unchanged_status_writes_nothing_and_lan_probe_respects_permission() {
        let repository = repository().await;
        let events = EventBus::default();
        let mut bus = events.subscribe();
        let probe = Arc::new(FakeProbe { alive: Mutex::new(BTreeSet::new()), calls: Mutex::new(Vec::new()) });
        let refresher = StatusRefresher::new(
            repository,
            events,
            probe.clone(),
            Some(Arc::new(FakeNetbird(ObservationStatus::Online))),
        );
        let report = refresher.refresh(false).await.unwrap();
        assert_eq!(report.changed, 0);
        assert!(report.netbird_ok);
        // Without Local Network permission only the NetBird address is probed.
        assert_eq!(probe.calls.lock().unwrap().as_slice(), &[vec![Ipv4Addr::new(100, 64, 0, 5)]]);
        assert!(bus.try_recv().is_err());
    }
}
