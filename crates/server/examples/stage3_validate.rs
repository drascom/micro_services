use inet_viewer_server::{
    connect_database,
    discovery::{CommandProvider, DiscoveryProvider, DiscoveryRunner, ProviderSpec},
    merge::merge_observations,
    repository::Repository,
};
use std::{path::PathBuf, sync::Arc};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let specs = [
        ProviderSpec::netbird(find_command("netbird")?),
        ProviderSpec::neighbor(find_command("ip")?),
        ProviderSpec::mdns(find_command("avahi-browse")?),
    ];
    let providers: Vec<Arc<dyn DiscoveryProvider>> = specs
        .into_iter()
        .map(CommandProvider::new)
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .map(|provider| Arc::new(provider) as Arc<dyn DiscoveryProvider>)
        .collect();
    let runner = DiscoveryRunner::new(providers);
    let results = runner.run_all().await;
    let mut observations = Vec::new();
    for result in results {
        match result {
            Ok(run) => {
                println!(
                    "{:?}: {} observations, {} warnings",
                    run.kind,
                    run.observations.len(),
                    run.warnings.len()
                );
                observations.extend(run.observations);
            }
            Err((kind, error)) => println!("{kind:?}: ERROR: {error}"),
        }
    }
    let mut devices = merge_observations(observations);
    let database_url = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "sqlite://data/stage3-validation.sqlite".into());
    let repository = Repository::new(connect_database(&database_url).await?);
    repository.save_merged(&mut devices).await?;
    repository.save_health(&runner.health().await).await?;
    println!(
        "persisted {} merged devices; database now contains {} devices",
        devices.len(),
        repository.list_devices().await?.len()
    );
    Ok(())
}

fn find_command(name: &str) -> anyhow::Result<PathBuf> {
    ["/usr/bin", "/usr/local/bin"]
        .into_iter()
        .map(|directory| PathBuf::from(directory).join(name))
        .find(|path| path.is_file())
        .ok_or_else(|| anyhow::anyhow!("{name} not found in trusted command directories"))
}
