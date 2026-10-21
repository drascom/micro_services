use axum::Router;
use sqlx::SqlitePool;
use std::path::PathBuf;
use tower_http::services::{ServeDir, ServeFile};

pub mod api;
pub mod discovery;
pub mod events;
pub mod merge;
pub mod refresh;
pub mod repository;
pub mod scan;

pub fn app(repository: repository::Repository) -> Router {
    let events = events::EventBus::default();
    let scans = scan::ScanService::new(
        repository.clone(),
        discovery::DiscoveryRunner::default(),
        events.clone(),
        std::time::Duration::from_secs(2),
    );
    app_with_services(repository, events, scans)
}

pub fn app_with_services(
    repository: repository::Repository,
    events: events::EventBus,
    scans: scan::ScanService,
) -> Router {
    let static_dir = std::env::var_os("INET_VIEWER_STATIC_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("crates/web/dist"));
    app_with_static_dir(repository, events, scans, static_dir)
}

pub fn app_with_static_dir(
    repository: repository::Repository,
    events: events::EventBus,
    scans: scan::ScanService,
    static_dir: PathBuf,
) -> Router {
    let index = static_dir.join("index.html");
    api::router(repository, events, scans)
        .fallback_service(ServeDir::new(static_dir).not_found_service(ServeFile::new(index)))
}

pub async fn connect_database(url: &str) -> anyhow::Result<SqlitePool> {
    let options = url
        .parse::<sqlx::sqlite::SqliteConnectOptions>()?
        .create_if_missing(true);
    let pool = SqlitePool::connect_with(options).await?;
    sqlx::migrate!("../../migrations").run(&pool).await?;
    Ok(pool)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::repository::Repository;
    use axum::{
        body::Body,
        http::{Request, StatusCode},
    };
    use http_body_util::BodyExt;
    use tower::ServiceExt;

    #[tokio::test]
    async fn health_endpoint_responds() {
        let repository = Repository::new(connect_database("sqlite::memory:").await.unwrap());
        let response = app(repository)
            .oneshot(
                Request::builder()
                    .uri("/api/v1/status")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = response.into_body().collect().await.unwrap().to_bytes();
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&body).unwrap(),
            serde_json::json!({
                "status":"ok",
                "total_devices":0,
                "online_devices":0,
                "offline_devices":0,
                "netbird_devices":0,
                "lan_only_devices":0,
                "mdns_devices":0,
                "unhealthy_sources":0
            })
        );
    }

    #[tokio::test]
    async fn migrations_apply_to_memory_database() {
        let pool = connect_database("sqlite::memory:").await.unwrap();
        let tables: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='devices'",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(tables, 1);
    }
}
