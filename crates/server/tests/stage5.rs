use async_trait::async_trait;
use axum::{
    body::Body,
    http::{Method, Request, StatusCode},
};
use chrono::Utc;
use http_body_util::BodyExt;
use inet_viewer_server::{
    app_with_services, connect_database,
    discovery::{DiscoveryError, DiscoveryProvider, DiscoveryRun, DiscoveryRunner},
    events::EventBus,
    repository::Repository,
    scan::ScanService,
};
use inet_viewer_shared::{DiscoverySourceKind, Observation, ObservationStatus};
use std::{collections::HashMap, sync::Arc, time::Duration};
use tower::ServiceExt;

struct FakeProvider;

#[async_trait]
impl DiscoveryProvider for FakeProvider {
    fn kind(&self) -> DiscoverySourceKind {
        DiscoverySourceKind::Neighbor
    }

    async fn discover(&self) -> Result<DiscoveryRun, DiscoveryError> {
        let now = Utc::now();
        Ok(DiscoveryRun {
            kind: DiscoverySourceKind::Neighbor,
            observations: vec![Observation {
                source: DiscoverySourceKind::Neighbor,
                source_id: Some("stage5-device".into()),
                hostname: Some("live-device.local".into()),
                ip_addresses: vec!["192.0.2.55".parse().unwrap()],
                mac_address: None,
                manufacturer: None,
                model: None,
                status: ObservationStatus::Online,
                services: vec![],
                observed_at: now,
                attributes: HashMap::new(),
            }],
            warnings: vec![],
            started_at: now,
            finished_at: now,
        })
    }
}

async fn response_json(response: axum::response::Response) -> serde_json::Value {
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}

#[tokio::test]
async fn manual_scan_is_persisted_rate_limited_and_emits_ordered_events() {
    let repository = Repository::new(connect_database("sqlite::memory:").await.unwrap());
    let events = EventBus::default();
    let mut receiver = events.subscribe();
    let scans = ScanService::new(
        repository.clone(),
        DiscoveryRunner::new(vec![Arc::new(FakeProvider)]),
        events.clone(),
        Duration::from_secs(60),
    );
    let router = app_with_services(repository.clone(), events, scans);

    let response = router
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::POST)
                .uri("/api/v1/scans")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::ACCEPTED);
    let body = response_json(response).await;
    let scan_id = body["scan_id"].as_str().unwrap();

    // `scan.progress` events interleave; the remaining order is fixed.
    let mut ordered = Vec::new();
    let mut progress = 0;
    while ordered.len() < 4 {
        let event = tokio::time::timeout(Duration::from_secs(1), receiver.recv())
            .await
            .unwrap()
            .unwrap();
        if event.event_type == "scan.progress" {
            assert_eq!(event.data["scan_id"], scan_id);
            progress += 1;
            continue;
        }
        ordered.push(event.event_type);
    }
    assert!(progress >= 1, "a finished source reports progress");
    assert_eq!(
        ordered,
        ["scan.started", "device.discovered", "scan.completed", "snapshot.updated"]
    );

    let scan = repository.get_scan(scan_id).await.unwrap().unwrap();
    assert_eq!(scan.status, "completed");
    let devices = repository.list_devices().await.unwrap();
    assert_eq!(devices.len(), 1);
    let history = repository
        .list_device_events(&devices[0].id.0.to_string(), 15)
        .await
        .unwrap();
    assert_eq!(history[0].event_type, "device.discovered");

    let response = router
        .oneshot(
            Request::builder()
                .method(Method::POST)
                .uri("/api/v1/scans")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::TOO_MANY_REQUESTS);
}
