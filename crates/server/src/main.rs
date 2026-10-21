use inet_viewer_server::{
    discovery::{
        ActiveNeighborProvider, CommandProvider, DiscoveryProvider, DiscoveryRunner, ProviderSpec,
    },
    events::EventBus,
    scan::ScanService,
};
use std::{collections::HashSet, env, net::SocketAddr, sync::Arc, time::Duration};
use tokio::net::TcpListener;
use tracing::info;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();
    let database_url =
        env::var("DATABASE_URL").unwrap_or_else(|_| "sqlite://data/inet-viewer.sqlite".into());
    let pool = inet_viewer_server::connect_database(&database_url).await?;
    let repository = inet_viewer_server::repository::Repository::new(pool);
    if env::args().any(|argument| argument == "--repair-duplicates") {
        let repaired = repository.repair_duplicate_devices().await?;
        println!("repaired {repaired} duplicate devices");
        return Ok(());
    }
    let mut providers = [
        ProviderSpec::netbird("/usr/bin/netbird"),
        ProviderSpec::mdns("/usr/bin/avahi-browse"),
    ]
    .into_iter()
    .filter_map(|spec| match CommandProvider::new(spec) {
        Ok(provider) => Some(Arc::new(provider) as Arc<dyn DiscoveryProvider>),
        Err(error) => {
            tracing::warn!(%error, "discovery provider disabled");
            None
        }
    })
    .collect::<Vec<_>>();
    let neighbor: Arc<dyn DiscoveryProvider> = match env::var("INET_VIEWER_LAN_CIDR") {
        Ok(cidr) => Arc::new(ActiveNeighborProvider::new(
            "/usr/bin/ip",
            "/usr/bin/ping",
            &cidr,
        )?),
        Err(_) => Arc::new(CommandProvider::new(ProviderSpec::neighbor("/usr/bin/ip"))?),
    };
    providers.push(neighbor);
    let events = EventBus::default();
    let scans = ScanService::new(
        repository.clone(),
        DiscoveryRunner::new(providers),
        events.clone(),
        Duration::from_secs(5),
    );
    let scheduled_scans = scans.clone();
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_secs(60));
        loop {
            interval.tick().await;
            let _ = scheduled_scans.trigger().await;
        }
    });
    let allow_wildcard = env::var("INET_VIEWER_ALLOW_WILDCARD").as_deref() == Ok("true");
    let addresses = parse_listen_addresses(
        &env::var("INET_VIEWER_LISTEN").unwrap_or_else(|_| "127.0.0.1:8080".into()),
        allow_wildcard,
    )?;
    let app = inet_viewer_server::app_with_services(repository, events, scans);
    let mut listeners = Vec::with_capacity(addresses.len());
    for address in addresses {
        let listener = TcpListener::bind(address).await?;
        info!(%address, "inet-viewer listening");
        listeners.push(listener);
    }

    let (shutdown_tx, shutdown_rx) = tokio::sync::watch::channel(false);
    let mut servers = tokio::task::JoinSet::new();
    for listener in listeners {
        let app = app.clone();
        let mut shutdown = shutdown_rx.clone();
        servers.spawn(async move {
            axum::serve(listener, app)
                .with_graceful_shutdown(async move {
                    while !*shutdown.borrow() && shutdown.changed().await.is_ok() {}
                })
                .await
        });
    }

    tokio::select! {
        _ = shutdown_signal() => {}
        result = servers.join_next() => {
            match result {
                Some(Ok(Ok(()))) | None => {}
                Some(Ok(Err(error))) => return Err(error.into()),
                Some(Err(error)) => return Err(error.into()),
            }
        }
    }
    let _ = shutdown_tx.send(true);
    while let Some(result) = servers.join_next().await {
        result??;
    }
    Ok(())
}

fn parse_listen_addresses(value: &str, allow_wildcard: bool) -> anyhow::Result<Vec<SocketAddr>> {
    let mut seen = HashSet::new();
    let mut addresses = Vec::new();
    for raw in value.split(',') {
        let raw = raw.trim();
        if raw.is_empty() {
            anyhow::bail!("INET_VIEWER_LISTEN contains an empty address");
        }
        let address: SocketAddr = raw.parse()?;
        if address.ip().is_unspecified() && !allow_wildcard {
            anyhow::bail!(
                "wildcard listen address {address} requires INET_VIEWER_ALLOW_WILDCARD=true"
            );
        }
        if seen.insert(address) {
            addresses.push(address);
        }
    }
    if addresses.is_empty() {
        anyhow::bail!("INET_VIEWER_LISTEN must contain at least one address");
    }
    Ok(addresses)
}

async fn shutdown_signal() {
    #[cfg(unix)]
    {
        let mut terminate =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
                .expect("failed to install SIGTERM handler");
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {}
            _ = terminate.recv() => {}
        }
    }
    #[cfg(not(unix))]
    let _ = tokio::signal::ctrl_c().await;
}

#[cfg(test)]
mod tests {
    use super::parse_listen_addresses;

    #[test]
    fn parses_distinct_explicit_addresses() {
        let addresses = parse_listen_addresses(
            "192.168.0.96:8080, 100.111.37.77:8080,192.168.0.96:8080",
            false,
        )
        .unwrap();
        assert_eq!(addresses.len(), 2);
    }

    #[test]
    fn rejects_wildcard_without_explicit_opt_in() {
        let error = parse_listen_addresses("0.0.0.0:8080", false).unwrap_err();
        assert!(error
            .to_string()
            .contains("INET_VIEWER_ALLOW_WILDCARD=true"));
        assert!(parse_listen_addresses("0.0.0.0:8080", true).is_ok());
    }
}
