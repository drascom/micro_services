use crate::{
    discovery::{DiscoveryRunner, ProgressUpdate},
    events::EventBus,
    merge::merge_observations,
    repository::{Repository, ScanRecord},
};
use chrono::Utc;
use inet_viewer_shared::{Device, DiscoverySourceKind};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, HashMap},
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::Mutex;
use uuid::Uuid;

#[derive(Clone)]
pub struct ScanService {
    repository: Repository,
    runner: DiscoveryRunner,
    events: EventBus,
    gate: Arc<Mutex<ScanGate>>,
    minimum_interval: Duration,
    progress: Arc<std::sync::Mutex<Option<Value>>>,
}

/// Minimum spacing of `scan.progress` events (phase changes always go out).
pub const PROGRESS_THROTTLE: Duration = Duration::from_millis(150);

#[derive(Clone, Debug, Default)]
struct SourceProgress {
    phase: String,
    done: usize,
    total: usize,
    finished: bool,
}

/// Aggregates per-source progress into one overall figure.
pub struct ProgressTracker {
    scan_id: String,
    sources: BTreeMap<u8, (DiscoverySourceKind, SourceProgress)>,
    last_emit: Option<Instant>,
}

fn source_order(kind: DiscoverySourceKind) -> u8 {
    match kind {
        DiscoverySourceKind::Netbird => 0,
        DiscoverySourceKind::Neighbor => 1,
        DiscoverySourceKind::Mdns => 2,
    }
}

/// LAN liveness is by far the longest step, so it weighs more.
fn source_weight(kind: DiscoverySourceKind) -> f64 {
    match kind {
        DiscoverySourceKind::Neighbor => 3.0,
        DiscoverySourceKind::Netbird | DiscoverySourceKind::Mdns => 1.0,
    }
}

fn source_name(kind: DiscoverySourceKind) -> &'static str {
    match kind {
        DiscoverySourceKind::Netbird => "netbird",
        DiscoverySourceKind::Neighbor => "neighbor",
        DiscoverySourceKind::Mdns => "mdns",
    }
}

impl ProgressTracker {
    pub fn new(scan_id: impl Into<String>, kinds: &[DiscoverySourceKind]) -> Self {
        Self {
            scan_id: scan_id.into(),
            sources: kinds
                .iter()
                .map(|kind| (source_order(*kind), (*kind, SourceProgress::default())))
                .collect(),
            last_emit: None,
        }
    }

    /// Applies an update; returns the event payload when it should be sent now.
    pub fn apply(&mut self, update: ProgressUpdate, now: Instant) -> Option<Value> {
        let entry = &mut self
            .sources
            .entry(source_order(update.source))
            .or_insert_with(|| (update.source, SourceProgress::default()))
            .1;
        let phase_changed = entry.phase != update.phase || entry.finished != update.finished;
        if !update.phase.is_empty() {
            entry.phase = update.phase;
        }
        entry.done = update.done;
        entry.total = update.total;
        entry.finished |= update.finished;
        let due = self
            .last_emit
            .is_none_or(|last| now.duration_since(last) >= PROGRESS_THROTTLE);
        (phase_changed || due).then(|| {
            self.last_emit = Some(now);
            self.snapshot()
        })
    }

    pub fn percent(&self) -> f64 {
        let (sum, weight) = self.sources.values().fold((0.0, 0.0), |(sum, weight), (kind, item)| {
            let fraction = if item.finished {
                1.0
            } else if item.total > 0 {
                item.done as f64 / item.total as f64
            } else {
                0.0
            };
            let w = source_weight(*kind);
            (sum + fraction * w, weight + w)
        });
        if weight == 0.0 { 0.0 } else { (sum / weight * 100.0).clamp(0.0, 100.0) }
    }

    /// Short text of the running phases, e.g. "LAN: 120/253 · mDNS".
    pub fn label(&self) -> String {
        let running: Vec<String> = self
            .sources
            .values()
            .filter(|(_, item)| !item.finished && !item.phase.is_empty())
            .map(|(_, item)| {
                if item.total > 1 {
                    format!("{}: {}/{}", item.phase, item.done, item.total)
                } else {
                    item.phase.clone()
                }
            })
            .collect();
        if running.is_empty() && self.sources.values().any(|(_, item)| item.finished) {
            "Birleştiriliyor".into()
        } else if running.is_empty() {
            "Başlıyor".into()
        } else {
            running.join(" · ")
        }
    }

    pub fn snapshot(&self) -> Value {
        // `phase/done/total` follow the longest-running unfinished source.
        let main = self
            .sources
            .values()
            .filter(|(_, item)| !item.finished)
            .max_by(|a, b| source_weight(a.0).total_cmp(&source_weight(b.0)))
            .map(|(_, item)| item.clone())
            .unwrap_or_default();
        json!({
            "scan_id": self.scan_id,
            "phase": self.label(),
            "done": main.done,
            "total": main.total,
            "percent": (self.percent() * 10.0).round() / 10.0,
            "sources": self.sources.values().map(|(kind, item)| json!({
                "source": source_name(*kind),
                "phase": item.phase,
                "done": item.done,
                "total": item.total,
                "finished": item.finished,
            })).collect::<Vec<_>>(),
        })
    }
}

#[derive(Default)]
struct ScanGate {
    running: bool,
    last_started: Option<Instant>,
}

#[derive(Debug, PartialEq, Eq)]
pub enum StartError {
    AlreadyRunning,
    RateLimited,
}

impl ScanService {
    pub fn new(
        repository: Repository,
        runner: DiscoveryRunner,
        events: EventBus,
        minimum_interval: Duration,
    ) -> Self {
        Self {
            repository,
            runner,
            events,
            gate: Arc::new(Mutex::new(ScanGate::default())),
            minimum_interval,
            progress: Arc::new(std::sync::Mutex::new(None)),
        }
    }

    /// Last `scan.*` state of the running scan (`None` when idle).
    pub fn progress(&self) -> Option<Value> {
        self.progress.lock().ok().and_then(|item| item.clone())
    }

    fn set_progress(&self, kind: &str, data: &Value) {
        if let Ok(mut slot) = self.progress.lock() {
            *slot = (kind == "scan.progress" || kind == "scan.started").then(|| data.clone());
        }
        self.events.publish(kind, data.clone());
    }

    /// Whether a full scan is currently running.
    pub async fn is_running(&self) -> bool {
        self.gate.lock().await.running
    }

    pub async fn trigger(&self) -> Result<String, StartError> {
        let mut gate = self.gate.lock().await;
        if gate.running {
            return Err(StartError::AlreadyRunning);
        }
        if gate
            .last_started
            .is_some_and(|last| last.elapsed() < self.minimum_interval)
        {
            return Err(StartError::RateLimited);
        }
        gate.running = true;
        gate.last_started = Some(Instant::now());
        drop(gate);

        let scan_id = Uuid::new_v4().to_string();
        let service = self.clone();
        let spawned_id = scan_id.clone();
        tokio::spawn(async move {
            service.execute(spawned_id).await;
            service.gate.lock().await.running = false;
        });
        Ok(scan_id)
    }

    async fn execute(&self, scan_id: String) {
        let started_at = Utc::now();
        if let Err(error) = self.repository.start_scan(&scan_id, started_at).await {
            tracing::error!(%error, %scan_id, "could not persist scan start");
        }
        let tracker = Arc::new(std::sync::Mutex::new(ProgressTracker::new(
            scan_id.clone(),
            &self.runner.kinds(),
        )));
        let started = tracker.lock().expect("fresh tracker").snapshot();
        self.set_progress("scan.started", &started);

        let before = self.repository.list_devices().await.unwrap_or_default();
        let service = self.clone();
        let progress_tracker = Arc::clone(&tracker);
        let callback: Arc<dyn Fn(ProgressUpdate) + Send + Sync> = Arc::new(move |update| {
            let payload = progress_tracker
                .lock()
                .ok()
                .and_then(|mut tracker| tracker.apply(update, Instant::now()));
            if let Some(payload) = payload {
                service.set_progress("scan.progress", &payload);
            }
        });
        let results = self.runner.run_all_with_progress(Some(callback)).await;
        let total_sources = results.len();
        let mut observations = Vec::new();
        let mut errors = Vec::new();
        for result in results {
            match result {
                Ok(run) => observations.extend(run.observations),
                Err((source, error)) => errors.push(format!("{source:?}: {error}")),
            }
        }

        let outcome = async {
            if total_sources > 0 && errors.len() == total_sources {
                anyhow::bail!("all discovery sources failed: {}", errors.join("; "));
            }
            if !observations.is_empty() {
                let mut merged = merge_observations(observations);
                self.repository.save_merged(&mut merged).await?;
            }
            self.repository
                .save_health(&self.runner.health().await)
                .await?;
            let after = self.repository.list_devices().await?;
            self.publish_device_changes(&before, &after).await?;
            Ok::<usize, anyhow::Error>(after.len())
        }
        .await;

        match outcome {
            Ok(device_count) => {
                let message = (!errors.is_empty()).then(|| errors.join("; "));
                let _ = self
                    .repository
                    .finish_scan(&scan_id, "completed", message.as_deref())
                    .await;
                self.set_progress(
                    "scan.completed",
                    &json!({"scan_id":scan_id,"device_count":device_count,"source_errors":errors,"percent":100.0}),
                );
                self.events
                    .publish("snapshot.updated", json!({"scan_id":scan_id}));
            }
            Err(error) => {
                let message = error.to_string();
                let _ = self
                    .repository
                    .finish_scan(&scan_id, "failed", Some(&message))
                    .await;
                self.set_progress("scan.failed", &json!({"scan_id":scan_id,"error":message}));
            }
        }
    }

    async fn publish_device_changes(
        &self,
        before: &[Device],
        after: &[Device],
    ) -> anyhow::Result<()> {
        let old = before
            .iter()
            .map(|device| (device.id.0, device))
            .collect::<HashMap<_, _>>();
        for device in after {
            let (event_type, changed_fields) = match old.get(&device.id.0) {
                None => ("device.discovered", vec!["device"]),
                Some(previous) if previous.status != device.status => {
                    let kind = match format!("{:?}", device.status).as_str() {
                        "Online" => "device.online",
                        "Offline" => "device.offline",
                        _ => "device.updated",
                    };
                    (kind, vec!["status", "last_seen_at"])
                }
                Some(previous) if *previous != device => ("device.updated", vec!["device"]),
                Some(_) => continue,
            };
            let data = json!({"device_id":device.id,"changed_fields":changed_fields});
            self.repository
                .record_device_event(&device.id.0.to_string(), event_type, &data)
                .await?;
            self.events.publish(event_type, data);
        }
        Ok(())
    }

    pub fn events(&self) -> &EventBus {
        &self.events
    }

    pub async fn get(&self, id: &str) -> anyhow::Result<Option<ScanRecord>> {
        self.repository.get_scan(id).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn update(source: DiscoverySourceKind, phase: &str, done: usize, total: usize) -> ProgressUpdate {
        ProgressUpdate { source, phase: phase.into(), done, total, finished: false }
    }

    #[test]
    fn progress_is_weighted_throttled_and_labelled() {
        let kinds = [DiscoverySourceKind::Neighbor, DiscoverySourceKind::Mdns, DiscoverySourceKind::Netbird];
        let mut tracker = ProgressTracker::new("s", &kinds);
        assert_eq!(tracker.label(), "Başlıyor");
        let start = Instant::now();
        assert!(tracker.apply(update(DiscoverySourceKind::Neighbor, "LAN", 0, 200), start).is_some());
        // Same phase within the throttle window: not sent.
        assert!(tracker.apply(update(DiscoverySourceKind::Neighbor, "LAN", 100, 200), start + Duration::from_millis(20)).is_none());
        // New phase of another source: sent immediately.
        let payload = tracker.apply(update(DiscoverySourceKind::Mdns, "mDNS", 0, 1), start + Duration::from_millis(30)).unwrap();
        assert_eq!(payload["phase"], "LAN: 100/200 · mDNS");
        assert_eq!(payload["done"], 100);
        assert_eq!(payload["total"], 200);
        // LAN half done (weight 3 of 5) = 30 %.
        assert!((tracker.percent() - 30.0).abs() < 0.01);
        assert!(tracker.apply(update(DiscoverySourceKind::Neighbor, "LAN", 150, 200), start + Duration::from_millis(200)).is_some());
        for kind in kinds {
            tracker.apply(ProgressUpdate { source: kind, phase: String::new(), done: 1, total: 1, finished: true }, start + Duration::from_secs(1));
        }
        assert_eq!(tracker.percent(), 100.0);
        assert_eq!(tracker.label(), "Birleştiriliyor");
    }
}
