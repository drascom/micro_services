use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use chrono::{TimeZone, Utc};
use http_body_util::BodyExt;
use inet_viewer_server::{app, connect_database, merge::MergedDevice, repository::Repository};
use inet_viewer_shared::{Device, DeviceId, DeviceStatus, DeviceType, DiscoverySourceKind};
use tower::ServiceExt;

async fn seeded_app() -> axum::Router {
    let repository = Repository::new(connect_database("sqlite::memory:").await.unwrap());
    let now = Utc.with_ymd_and_hms(2026, 10, 19, 12, 0, 0).unwrap();
    let mut devices = vec![
        MergedDevice {
            device: Device {
                id: DeviceId::new(),
                display_name: "NAS Alpha".into(),
                hostnames: vec!["nas.local".into()],
                names: Vec::new(),
                lan_ips: vec!["192.168.0.10".parse().unwrap()],
                netbird_ip: None,
                netbird_ipv6: None,
                mac_addresses: vec![],
                manufacturer: Some("Synology".into()),
                model: None,
                device_type: DeviceType::Server,
                status: DeviceStatus::Online,
                sources: vec![DiscoverySourceKind::Neighbor],
                services: vec![],
                first_seen_at: now,
                last_seen_at: now,
            },
            observations: vec![],
            evidence: vec![],
        },
        MergedDevice {
            device: Device {
                id: DeviceId::new(),
                display_name: "Remote laptop".into(),
                hostnames: vec!["laptop.internal".into()],
                names: Vec::new(),
                lan_ips: vec![],
                netbird_ip: Some("100.111.1.2".parse().unwrap()),
                netbird_ipv6: None,
                mac_addresses: vec![],
                manufacturer: None,
                model: None,
                device_type: DeviceType::Computer,
                status: DeviceStatus::Offline,
                sources: vec![DiscoverySourceKind::Netbird],
                services: vec![],
                first_seen_at: now,
                last_seen_at: now,
            },
            observations: vec![],
            evidence: vec![],
        },
    ];
    repository.save_merged(&mut devices).await.unwrap();
    app(repository)
}

async fn json(router: axum::Router, uri: &str) -> (StatusCode, serde_json::Value) {
    let response = router
        .oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    (status, serde_json::from_slice(&bytes).unwrap())
}

#[tokio::test]
async fn devices_support_search_filters_and_sorting() {
    let router = seeded_app().await;
    let (status, body) = json(router.clone(), "/api/v1/devices?q=nas&status=online&source=neighbor&type=server&network=lan&sort=last_seen&order=desc").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["total"], 1);
    assert_eq!(body["devices"][0]["display_name"], "NAS Alpha");

    let (status, body) = json(router, "/api/v1/devices?status=not-real").await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"], "unknown status filter");
}

#[tokio::test]
async fn status_sources_and_device_detail_are_exposed() {
    let router = seeded_app().await;
    let (_, summary) = json(router.clone(), "/api/v1/status").await;
    assert_eq!(summary["total_devices"], 2);
    assert_eq!(summary["online_devices"], 1);
    assert_eq!(summary["offline_devices"], 1);
    assert_eq!(summary["netbird_devices"], 1);
    assert_eq!(summary["lan_only_devices"], 1);

    let (_, list) = json(router.clone(), "/api/v1/devices?q=remote").await;
    let id = list["devices"][0]["id"].as_str().unwrap();
    let (status, detail) = json(router.clone(), &format!("/api/v1/devices/{id}")).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(detail["display_name"], "Remote laptop");

    let (status, sources) = json(router, "/api/v1/sources").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(sources.as_array().unwrap().len(), 3);
}
