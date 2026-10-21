use inet_viewer_server::discovery::{ActiveNeighborProvider, DiscoveryProvider};
use std::{fs, net::IpAddr, os::unix::fs::PermissionsExt, path::PathBuf};
use uuid::Uuid;

fn executable(name: &str, body: &str) -> PathBuf {
    let path = std::env::temp_dir().join(format!("inet-viewer-{name}-{}", Uuid::new_v4()));
    fs::write(&path, body).unwrap();
    let mut permissions = fs::metadata(&path).unwrap().permissions();
    permissions.set_mode(0o700);
    fs::set_permissions(&path, permissions).unwrap();
    path
}

#[tokio::test]
async fn active_neighbor_discovery_primes_the_cache_inside_configured_cidr() {
    let marker = std::env::temp_dir().join(format!("inet-viewer-probed-{}", Uuid::new_v4()));
    let ping = executable(
        "ping",
        &format!(
            "#!/bin/sh\n[ \"$5\" = \"192.168.0.124\" ] && touch '{}'\nexit 0\n",
            marker.display()
        ),
    );
    let ip = executable(
        "ip",
        &format!(
            "#!/bin/sh\n[ -f '{}' ] && printf '%s\\n' '192.168.0.124 dev enp1s0 lladdr 84:39:be:69:08:66 REACHABLE'\n",
            marker.display()
        ),
    );

    let provider =
        ActiveNeighborProvider::new(ip.clone(), ping.clone(), "192.168.0.120/29").unwrap();
    let run = provider.discover().await.unwrap();

    assert!(run.observations.iter().any(
        |observation| observation.ip_addresses == ["192.168.0.124".parse::<IpAddr>().unwrap()]
    ));
    let _ = fs::remove_file(marker);
    let _ = fs::remove_file(ip);
    let _ = fs::remove_file(ping);
}

#[test]
fn active_neighbor_discovery_rejects_broad_or_non_private_cidrs() {
    let command = PathBuf::from("/bin/true");
    assert!(
        ActiveNeighborProvider::new(command.clone(), command.clone(), "192.168.0.0/16").is_err()
    );
    assert!(ActiveNeighborProvider::new(command.clone(), command, "203.0.113.0/24").is_err());
}
