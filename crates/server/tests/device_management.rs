use axum::{
    body::Body,
    http::{Method, Request, StatusCode},
};
use chrono::Utc;
use http_body_util::BodyExt;
use inet_viewer_server::{app, connect_database, merge::MergedDevice, repository::Repository};
use inet_viewer_shared::{Device, DeviceId, DeviceStatus, DeviceType};
use tower::ServiceExt;

fn merged_device(id: DeviceId, name: &str, device_type: DeviceType) -> MergedDevice {
    let now = Utc::now();
    MergedDevice {
        device: Device {
            id,
            display_name: name.into(),
            hostnames: Vec::new(),
            names: Vec::new(),
            lan_ips: Vec::new(),
            netbird_ip: None,
            netbird_ipv6: None,
            mac_addresses: Vec::new(),
            manufacturer: None,
            model: None,
            device_type,
            status: DeviceStatus::Online,
            sources: Vec::new(),
            services: Vec::new(),
            first_seen_at: now,
            last_seen_at: now,
        },
        observations: Vec::new(),
        evidence: Vec::new(),
    }
}

async fn setup() -> (Repository, axum::Router, String) {
    let repository = Repository::new(connect_database("sqlite::memory:").await.unwrap());
    let id = DeviceId::new();
    let id_text = id.0.to_string();
    repository
        .save_merged(&mut [merged_device(id, "discovered name", DeviceType::Unknown)])
        .await
        .unwrap();
    let router = app(repository.clone());
    (repository, router, id_text)
}

async fn json(response: axum::response::Response) -> (StatusCode, serde_json::Value) {
    let status = response.status();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    (status, serde_json::from_slice(&bytes).unwrap())
}

#[tokio::test]
async fn device_events_default_to_15_and_validate_the_limit() {
    let (repository, router, id) = setup().await;
    for index in 0..20 {
        repository
            .record_device_event(&id, "device.updated", &serde_json::json!({"index": index}))
            .await
            .unwrap();
    }

    let response = router
        .clone()
        .oneshot(
            Request::builder()
                .uri(format!("/api/v1/devices/{id}/events"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let (status, body) = json(response).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body.as_array().unwrap().len(), 15);

    for limit in ["0", "invalid", "101"] {
        let response = router
            .clone()
            .oneshot(
                Request::builder()
                    .uri(format!("/api/v1/devices/{id}/events?limit={limit}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        let (status, body) = json(response).await;
        assert_eq!(status, StatusCode::BAD_REQUEST);
        assert_eq!(body["error"], "limit must be an integer between 1 and 100");
    }
}

#[tokio::test]
async fn patch_overrides_name_and_type_and_discovery_does_not_erase_them() {
    let (repository, router, id) = setup().await;
    let response = router
        .clone()
        .oneshot(
            Request::builder()
                .method(Method::PATCH)
                .uri(format!("/api/v1/devices/{id}"))
                .header("content-type", "application/json")
                .body(Body::from(
                    r#"{"display_name":"  Office NAS  ","device_type":"server"}"#,
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    let (status, body) = json(response).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["display_name"], "Office NAS");
    assert_eq!(body["device_type"], "server");

    let persisted_id = DeviceId(id.parse().unwrap());
    repository
        .save_merged(&mut [merged_device(
            persisted_id,
            "new discovery name",
            DeviceType::Printer,
        )])
        .await
        .unwrap();
    let device = repository.get_device(&id).await.unwrap().unwrap();
    assert_eq!(device.display_name, "Office NAS");
    assert_eq!(device.device_type, DeviceType::Server);

    let response = router
        .oneshot(
            Request::builder()
                .method(Method::PATCH)
                .uri(format!("/api/v1/devices/{id}"))
                .header("content-type", "application/json")
                .body(Body::from(r#"{"display_name":"   "}"#))
                .unwrap(),
        )
        .await
        .unwrap();
    let (status, body) = json(response).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["display_name"], "new discovery name");
    assert_eq!(body["device_type"], "server");
}

#[tokio::test]
async fn patch_rejects_invalid_or_overlong_names() {
    let (_, router, id) = setup().await;
    for payload in [
        serde_json::json!({"display_name": "x".repeat(129)}),
        serde_json::json!({"device_type": "spaceship"}),
    ] {
        let response = router
            .clone()
            .oneshot(
                Request::builder()
                    .method(Method::PATCH)
                    .uri(format!("/api/v1/devices/{id}"))
                    .header("content-type", "application/json")
                    .body(Body::from(payload.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    }
}
