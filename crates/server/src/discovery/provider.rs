use super::{
    netbird::resolve_netbird_statuses, parse_avahi_browse, parse_ip_neigh, parse_netbird_status,
    probe_liveness, LivenessConfig, ParseWarning, ProgressSink, ProgressUpdate,
};
use async_trait::async_trait;
use chrono::{DateTime, Utc};
use futures_util::{stream::FuturesUnordered, StreamExt};
use inet_viewer_shared::{DiscoverySourceKind, Observation};
use std::{
    collections::HashMap, net::Ipv4Addr, path::PathBuf, process::Stdio, sync::Arc, time::Duration,
};
use tokio::{process::Command, sync::RwLock, task::JoinHandle, time};

#[derive(Debug)]
pub enum DiscoveryError {
    InvalidCommand(String),
    Spawn(std::io::Error),
    Timeout(Duration),
    Exit { code: Option<i32>, stderr: String },
    OutputNotUtf8,
}

impl std::fmt::Display for DiscoveryError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidCommand(message) => write!(f, "invalid command: {message}"),
            Self::Spawn(error) => write!(f, "could not execute command: {error}"),
            Self::Timeout(timeout) => write!(f, "command timed out after {timeout:?}"),
            Self::Exit { code, stderr } => {
                write!(f, "command exited with {code:?}: {}", stderr.trim())
            }
            Self::OutputNotUtf8 => f.write_str("command output was not UTF-8"),
        }
    }
}

impl std::error::Error for DiscoveryError {}

#[derive(Clone, Debug)]
pub struct DiscoveryRun {
    pub kind: DiscoverySourceKind,
    pub observations: Vec<Observation>,
    pub warnings: Vec<ParseWarning>,
    pub started_at: DateTime<Utc>,
    pub finished_at: DateTime<Utc>,
}

#[async_trait]
pub trait DiscoveryProvider: Send + Sync {
    fn kind(&self) -> DiscoverySourceKind;
    async fn discover(&self) -> Result<DiscoveryRun, DiscoveryError>;

    /// Same as [`discover`](Self::discover) while reporting progress; providers
    /// without finer-grained steps just run `discover`.
    async fn discover_with_progress(
        &self,
        progress: ProgressSink,
    ) -> Result<DiscoveryRun, DiscoveryError> {
        let _ = progress;
        self.discover().await
    }
}

#[derive(Clone, Debug)]
pub struct ProviderSpec {
    pub kind: DiscoverySourceKind,
    pub command: PathBuf,
    pub args: Vec<String>,
    pub timeout: Duration,
    pub interval: Duration,
}

impl ProviderSpec {
    pub fn netbird(command: impl Into<PathBuf>) -> Self {
        Self {
            kind: DiscoverySourceKind::Netbird,
            command: command.into(),
            args: vec!["status".into(), "--detail".into()],
            timeout: Duration::from_secs(10),
            interval: Duration::from_secs(15),
        }
    }

    pub fn neighbor(command: impl Into<PathBuf>) -> Self {
        Self {
            kind: DiscoverySourceKind::Neighbor,
            command: command.into(),
            args: vec!["neigh".into(), "show".into()],
            timeout: Duration::from_secs(5),
            interval: Duration::from_secs(30),
        }
    }

    pub fn mdns(command: impl Into<PathBuf>) -> Self {
        Self {
            kind: DiscoverySourceKind::Mdns,
            command: command.into(),
            args: vec![
                "--all".into(),
                "--resolve".into(),
                "--terminate".into(),
                "--parsable".into(),
            ],
            timeout: Duration::from_secs(20),
            interval: Duration::from_secs(60),
        }
    }

    pub fn validate(&self) -> Result<(), DiscoveryError> {
        if !self.command.is_absolute() {
            return Err(DiscoveryError::InvalidCommand(
                "path must be absolute".into(),
            ));
        }
        let metadata = std::fs::metadata(&self.command).map_err(DiscoveryError::Spawn)?;
        if !metadata.is_file() {
            return Err(DiscoveryError::InvalidCommand(
                "path is not a regular file".into(),
            ));
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::{MetadataExt, PermissionsExt};
            if metadata.permissions().mode() & 0o111 == 0 {
                return Err(DiscoveryError::InvalidCommand(
                    "file is not executable".into(),
                ));
            }
            if metadata.mode() & 0o022 != 0 {
                return Err(DiscoveryError::InvalidCommand(
                    "executable is group/world writable".into(),
                ));
            }
        }
        Ok(())
    }
}

pub struct CommandProvider {
    spec: ProviderSpec,
}

impl CommandProvider {
    pub fn new(spec: ProviderSpec) -> Result<Self, DiscoveryError> {
        spec.validate()?;
        Ok(Self { spec })
    }

    pub fn spec(&self) -> &ProviderSpec {
        &self.spec
    }
}

#[async_trait]
impl DiscoveryProvider for CommandProvider {
    fn kind(&self) -> DiscoverySourceKind {
        self.spec.kind
    }

    async fn discover(&self) -> Result<DiscoveryRun, DiscoveryError> {
        let started_at = Utc::now();
        let mut command = Command::new(&self.spec.command);
        command
            .args(&self.spec.args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        let output = time::timeout(self.spec.timeout, command.output())
            .await
            .map_err(|_| DiscoveryError::Timeout(self.spec.timeout))?
            .map_err(DiscoveryError::Spawn)?;
        if !output.status.success() {
            return Err(DiscoveryError::Exit {
                code: output.status.code(),
                stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
            });
        }
        let stdout = String::from_utf8(output.stdout).map_err(|_| DiscoveryError::OutputNotUtf8)?;
        let observed_at = Utc::now();
        let parsed = match self.spec.kind {
            DiscoverySourceKind::Netbird => parse_netbird_status(&stdout, observed_at),
            DiscoverySourceKind::Neighbor => parse_ip_neigh(&stdout, observed_at),
            DiscoverySourceKind::Mdns => parse_avahi_browse(&stdout, observed_at),
        };
        Ok(DiscoveryRun {
            kind: self.spec.kind,
            observations: parsed.observations,
            warnings: parsed.warnings,
            started_at,
            finished_at: Utc::now(),
        })
    }
}

/// `netbird status --detail` plus an in-process ICMP/TCP check of every peer
/// NetBird does not report as `Connected`: an offline peer stays `Connecting`
/// or `Idle` forever, so that status alone never means online (see
/// [`resolve_netbird_statuses`]).
pub struct NetbirdProvider {
    command: CommandProvider,
    liveness: LivenessConfig,
}

impl NetbirdProvider {
    pub fn new(command: impl Into<PathBuf>) -> Result<Self, DiscoveryError> {
        Ok(Self {
            command: CommandProvider::new(ProviderSpec::netbird(command))?,
            liveness: LivenessConfig {
                icmp_wait: Duration::from_millis(1200),
                ..LivenessConfig::default()
            },
        })
    }
}

#[async_trait]
impl DiscoveryProvider for NetbirdProvider {
    fn kind(&self) -> DiscoverySourceKind {
        DiscoverySourceKind::Netbird
    }

    async fn discover(&self) -> Result<DiscoveryRun, DiscoveryError> {
        self.discover_with_progress(ProgressSink::noop(DiscoverySourceKind::Netbird))
            .await
    }

    async fn discover_with_progress(
        &self,
        progress: ProgressSink,
    ) -> Result<DiscoveryRun, DiscoveryError> {
        progress.report("NetBird", 0, 2);
        let mut run = self.command.discover().await?;
        let targets: Vec<Ipv4Addr> = run
            .observations
            .iter()
            .filter(|observation| observation.status != inet_viewer_shared::ObservationStatus::Online)
            .flat_map(|observation| observation.ip_addresses.iter())
            .filter_map(|address| match address {
                std::net::IpAddr::V4(v4) => Some(*v4),
                std::net::IpAddr::V6(_) => None,
            })
            .collect();
        progress.report("NetBird", 1, 2);
        let reachable = if targets.is_empty() {
            Default::default()
        } else {
            probe_liveness(targets, &self.liveness).await.alive
        };
        resolve_netbird_statuses(&mut run.observations, &reachable);
        run.finished_at = Utc::now();
        Ok(run)
    }
}

pub struct ActiveNeighborProvider {
    neighbor: CommandProvider,
    ping_command: PathBuf,
    targets: Vec<Ipv4Addr>,
}

impl ActiveNeighborProvider {
    pub fn new(
        ip_command: impl Into<PathBuf>,
        ping_command: impl Into<PathBuf>,
        cidr: &str,
    ) -> Result<Self, DiscoveryError> {
        let neighbor = CommandProvider::new(ProviderSpec::neighbor(ip_command))?;
        let ping_command = ping_command.into();
        ProviderSpec {
            kind: DiscoverySourceKind::Neighbor,
            command: ping_command.clone(),
            args: Vec::new(),
            timeout: Duration::from_secs(1),
            interval: Duration::from_secs(60),
        }
        .validate()?;
        Ok(Self {
            neighbor,
            ping_command,
            targets: private_cidr_targets(cidr)?,
        })
    }
}

#[async_trait]
impl DiscoveryProvider for ActiveNeighborProvider {
    fn kind(&self) -> DiscoverySourceKind {
        DiscoverySourceKind::Neighbor
    }

    async fn discover(&self) -> Result<DiscoveryRun, DiscoveryError> {
        const CONCURRENCY: usize = 8;
        for batch in self.targets.chunks(CONCURRENCY) {
            let mut probes = FuturesUnordered::new();
            for address in batch {
                let mut command = Command::new(&self.ping_command);
                command
                    .args(["-c", "1", "-W", "1", &address.to_string()])
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .kill_on_drop(true);
                probes.push(async move {
                    let _ = time::timeout(Duration::from_secs(2), command.status()).await;
                });
            }
            while probes.next().await.is_some() {}
        }
        self.neighbor.discover().await
    }
}

fn private_cidr_targets(cidr: &str) -> Result<Vec<Ipv4Addr>, DiscoveryError> {
    let invalid = |message: &str| DiscoveryError::InvalidCommand(message.into());
    let (address, prefix) = cidr
        .split_once('/')
        .ok_or_else(|| invalid("LAN CIDR must use IPv4 CIDR notation"))?;
    let address: Ipv4Addr = address
        .parse()
        .map_err(|_| invalid("LAN CIDR address is not valid IPv4"))?;
    let prefix: u32 = prefix
        .parse()
        .map_err(|_| invalid("LAN CIDR prefix is not valid"))?;
    if !(24..=32).contains(&prefix) {
        return Err(invalid("LAN CIDR must be between /24 and /32"));
    }
    if !address.is_private() {
        return Err(invalid("LAN CIDR must be private IPv4 space"));
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

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ProviderHealth {
    pub healthy: bool,
    pub last_success_at: Option<DateTime<Utc>>,
    pub last_error_at: Option<DateTime<Utc>>,
    pub last_error: Option<String>,
}

#[derive(Clone, Default)]
pub struct DiscoveryRunner {
    providers: Vec<Arc<dyn DiscoveryProvider>>,
    health: Arc<RwLock<HashMap<DiscoverySourceKind, ProviderHealth>>>,
}

impl DiscoveryRunner {
    pub fn new(providers: Vec<Arc<dyn DiscoveryProvider>>) -> Self {
        Self {
            providers,
            health: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    pub fn kinds(&self) -> Vec<DiscoverySourceKind> {
        self.providers.iter().map(|provider| provider.kind()).collect()
    }

    pub async fn run_all(&self) -> Vec<Result<DiscoveryRun, (DiscoverySourceKind, String)>> {
        self.run_all_with_progress(None).await
    }

    /// Runs every provider in parallel; `progress` receives each provider's
    /// updates and a final `finished` update per provider.
    pub async fn run_all_with_progress(
        &self,
        progress: Option<Arc<dyn Fn(ProgressUpdate) + Send + Sync>>,
    ) -> Vec<Result<DiscoveryRun, (DiscoverySourceKind, String)>> {
        let mut tasks = Vec::with_capacity(self.providers.len());
        for provider in &self.providers {
            let provider = Arc::clone(provider);
            let health = Arc::clone(&self.health);
            let progress = progress.clone();
            tasks.push(tokio::spawn(async move {
                let kind = provider.kind();
                let sink = match progress {
                    Some(callback) => ProgressSink::new(kind, callback),
                    None => ProgressSink::noop(kind),
                };
                let result = provider.discover_with_progress(sink.clone()).await;
                sink.finish();
                let now = Utc::now();
                let mut health = health.write().await;
                let entry = health.entry(kind).or_default();
                match &result {
                    Ok(_) => {
                        entry.healthy = true;
                        entry.last_success_at = Some(now);
                        entry.last_error = None;
                    }
                    Err(error) => {
                        entry.healthy = false;
                        entry.last_error_at = Some(now);
                        entry.last_error = Some(error.to_string());
                    }
                }
                result.map_err(|error| (kind, error.to_string()))
            }));
        }
        let mut results = Vec::with_capacity(tasks.len());
        for task in tasks {
            if let Ok(result) = task.await {
                results.push(result);
            }
        }
        results
    }

    pub async fn health(&self) -> HashMap<DiscoverySourceKind, ProviderHealth> {
        self.health.read().await.clone()
    }
}

pub struct Scheduler {
    handles: Vec<JoinHandle<()>>,
}

impl Scheduler {
    pub fn start<F, Fut>(
        providers: Vec<(Arc<dyn DiscoveryProvider>, Duration)>,
        callback: F,
    ) -> Self
    where
        F: Fn(Result<DiscoveryRun, (DiscoverySourceKind, String)>) -> Fut + Send + Sync + 'static,
        Fut: std::future::Future<Output = ()> + Send + 'static,
    {
        let callback = Arc::new(callback);
        let handles = providers
            .into_iter()
            .map(|(provider, interval)| {
                let callback = Arc::clone(&callback);
                tokio::spawn(async move {
                    let mut ticker = time::interval(interval);
                    ticker.set_missed_tick_behavior(time::MissedTickBehavior::Skip);
                    loop {
                        ticker.tick().await;
                        let kind = provider.kind();
                        let result = provider
                            .discover()
                            .await
                            .map_err(|error| (kind, error.to_string()));
                        callback(result).await;
                    }
                })
            })
            .collect();
        Self { handles }
    }
}

impl Drop for Scheduler {
    fn drop(&mut self) {
        for handle in &self.handles {
            handle.abort();
        }
    }
}
