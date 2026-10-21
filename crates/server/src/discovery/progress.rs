//! Scan progress reported by discovery providers while they run.
use inet_viewer_shared::DiscoverySourceKind;
use std::sync::Arc;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProgressUpdate {
    pub source: DiscoverySourceKind,
    /// Short phase label, e.g. "LAN", "İsimler", "mDNS".
    pub phase: String,
    pub done: usize,
    pub total: usize,
    pub finished: bool,
}

type Callback = Arc<dyn Fn(ProgressUpdate) + Send + Sync>;

/// Handle a provider uses to report its own progress; a no-op when nobody listens.
#[derive(Clone)]
pub struct ProgressSink {
    source: DiscoverySourceKind,
    callback: Option<Callback>,
}

impl ProgressSink {
    pub fn noop(source: DiscoverySourceKind) -> Self {
        Self {
            source,
            callback: None,
        }
    }

    pub fn new(source: DiscoverySourceKind, callback: Callback) -> Self {
        Self {
            source,
            callback: Some(callback),
        }
    }

    pub fn report(&self, phase: &str, done: usize, total: usize) {
        self.send(phase, done, total, false);
    }

    pub(crate) fn finish(&self) {
        self.send("", 1, 1, true);
    }

    fn send(&self, phase: &str, done: usize, total: usize, finished: bool) {
        if let Some(callback) = &self.callback {
            callback(ProgressUpdate {
                source: self.source,
                phase: phase.to_owned(),
                done: done.min(total),
                total,
                finished,
            });
        }
    }
}
