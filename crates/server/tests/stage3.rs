use async_trait::async_trait;
use chrono::Utc;
use inet_viewer_server::{
    connect_database,
    discovery::{
        CommandProvider, DiscoveryError, DiscoveryProvider, DiscoveryRun, DiscoveryRunner,
        ProviderHealth, ProviderSpec,
    },
    merge::merge_observations,
    repository::Repository,
};
use inet_viewer_shared::{DiscoverySourceKind, Observation, ObservationStatus};
use std::{collections::HashMap, path::PathBuf, sync::Arc, time::Duration};

struct FakeProvider {
    kind: DiscoverySourceKind,
    fail: bool,
}

#[async_trait]
impl DiscoveryProvider for FakeProvider {
    fn kind(&self) -> DiscoverySourceKind {
        self.kind
    }

    async fn discover(&self) -> Result<DiscoveryRun, DiscoveryError> {
        if self.fail {
            return Err(DiscoveryError::InvalidCommand(
                "intentional test failure".into(),
            ));
        }
        let now = Utc::now();
        Ok(DiscoveryRun {
            kind: self.kind,
            observations: vec![Observation {
                source: self.kind,
                source_id: Some("stable-id".into()),
                hostname: Some("test-device.internal".into()),
                ip_addresses: vec!["192.0.2.10".parse().unwrap()],
                mac_address: None,
                manufacturer: None,
                model: None,
                status: ObservationStatus::Online,
                services: Vec::new(),
                observed_at: now,
                attributes: HashMap::new(),
            }],
            warnings: Vec::new(),
            started_at: now,
            finished_at: now,
        })
    }
}

#[tokio::test]
async fn failed_source_does_not_discard_successful_source() {
    let runner = DiscoveryRunner::new(vec![
        Arc::new(FakeProvider {
            kind: DiscoverySourceKind::Neighbor,
            fail: false,
        }),
        Arc::new(FakeProvider {
            kind: DiscoverySourceKind::Netbird,
            fail: true,
        }),
    ]);
    let results = runner.run_all().await;
    assert_eq!(results.len(), 2);
    assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
    assert_eq!(results.iter().filter(|result| result.is_err()).count(), 1);
    let health = runner.health().await;
    assert!(health[&DiscoverySourceKind::Neighbor].healthy);
    assert!(!health[&DiscoverySourceKind::Netbird].healthy);
}

#[tokio::test]
async fn command_provider_enforces_timeout() {
    let sleep = ["/usr/bin/sleep", "/bin/sleep"]
        .into_iter()
        .find(|path| std::path::Path::new(path).is_file())
        .expect("system sleep command");
    let provider = CommandProvider::new(ProviderSpec {
        kind: DiscoverySourceKind::Neighbor,
        command: PathBuf::from(sleep),
        args: vec!["1".into()],
        timeout: Duration::from_millis(10),
        interval: Duration::from_secs(1),
    })
    .unwrap();
    assert!(matches!(
        provider.discover().await,
        Err(DiscoveryError::Timeout(_))
    ));
}

#[tokio::test]
async fn devices_and_source_health_survive_database_reopen() {
    let path =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/stage3-restart-test.sqlite");
    let _ = std::fs::remove_file(&path);
    let url = format!("sqlite://{}", path.display());

    let pool = connect_database(&url).await.unwrap();
    let repository = Repository::new(pool.clone());
    let run = FakeProvider {
        kind: DiscoverySourceKind::Neighbor,
        fail: false,
    }
    .discover()
    .await
    .unwrap();
    let mut merged = merge_observations(run.observations);
    repository.save_merged(&mut merged).await.unwrap();
    let original_id = merged[0].device.id.clone();
    repository
        .save_health(&HashMap::from([(
            DiscoverySourceKind::Neighbor,
            ProviderHealth {
                healthy: true,
                last_success_at: Some(Utc::now()),
                last_error_at: None,
                last_error: None,
            },
        )]))
        .await
        .unwrap();
    pool.close().await;

    let reopened = connect_database(&url).await.unwrap();
    let repository = Repository::new(reopened.clone());
    let devices = repository.list_devices().await.unwrap();
    assert_eq!(devices.len(), 1);
    assert_eq!(devices[0].id, original_id);
    assert_eq!(devices[0].display_name, "test-device.internal");
    assert!(repository.load_health().await.unwrap()[&DiscoverySourceKind::Neighbor].healthy);
    reopened.close().await;
    let _ = std::fs::remove_file(path);
}

#[tokio::test]
async fn repeated_ip_only_neighbor_observations_reuse_the_device() {
    let repository = Repository::new(connect_database("sqlite::memory:").await.unwrap());

    for _ in 0..3 {
        let now = Utc::now();
        let observation = Observation {
            source: DiscoverySourceKind::Neighbor,
            source_id: None,
            hostname: None,
            ip_addresses: vec!["192.168.0.234".parse().unwrap()],
            mac_address: None,
            manufacturer: None,
            model: None,
            status: ObservationStatus::Online,
            services: Vec::new(),
            observed_at: now,
            attributes: HashMap::new(),
        };
        let mut merged = merge_observations(vec![observation]);
        repository.save_merged(&mut merged).await.unwrap();
    }

    assert_eq!(repository.list_devices().await.unwrap().len(), 1);
}

#[tokio::test]
async fn duplicate_repair_merges_repeated_ip_devices() {
    let pool = connect_database("sqlite::memory:").await.unwrap();
    let repository = Repository::new(pool.clone());
    for index in 0..3 {
        let id = inet_viewer_shared::DeviceId::new().0.to_string();
        sqlx::query("INSERT INTO devices (id, display_name, device_type, status, first_seen_at, last_seen_at) VALUES (?, ?, 'unknown', 'online', ?, ?)")
            .bind(&id)
            .bind("192.168.0.235")
            .bind(format!("2026-01-01T00:00:0{index}Z"))
            .bind(format!("2026-01-01T00:00:0{index}Z"))
            .execute(&pool)
            .await
            .unwrap();
        sqlx::query("INSERT INTO device_addresses (device_id, kind, address) VALUES (?, 'lan', '192.168.0.235')")
            .bind(&id)
            .execute(&pool)
            .await
            .unwrap();
    }
    assert_eq!(repository.list_devices().await.unwrap().len(), 3);
    assert_eq!(repository.repair_duplicate_devices().await.unwrap(), 2);
    assert_eq!(repository.list_devices().await.unwrap().len(), 1);
}
