use chrono::Utc;
use inet_viewer_server::discovery::{parse_avahi_browse, parse_ip_neigh, parse_netbird_status};
use std::{env, fs};

fn main() -> anyhow::Result<()> {
    let mut arguments = env::args().skip(1);
    let parser = arguments
        .next()
        .ok_or_else(|| anyhow::anyhow!("missing parser name"))?;
    let path = arguments
        .next()
        .ok_or_else(|| anyhow::anyhow!("missing input path"))?;
    if arguments.next().is_some() {
        anyhow::bail!("expected: validate_outputs <netbird|neighbor|mdns> <path>");
    }
    let input = fs::read_to_string(path)?;
    let parsed = match parser.as_str() {
        "netbird" => parse_netbird_status(&input, Utc::now()),
        "neighbor" => parse_ip_neigh(&input, Utc::now()),
        "mdns" => parse_avahi_browse(&input, Utc::now()),
        _ => anyhow::bail!("unknown parser: {parser}"),
    };
    println!(
        "parser={parser} observations={} warnings={}",
        parsed.observations.len(),
        parsed.warnings.len()
    );
    for warning in parsed.warnings {
        println!("warning line={}: {}", warning.line, warning.message);
    }
    Ok(())
}
