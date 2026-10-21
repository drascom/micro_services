> Branch `cloudstream-mac` of `drascom/micro_services` holds only this project, at the branch root; other projects live on their own branches and nothing is merged into `main`.
> Local checkout: `~/Documents/work/micro_services/cloudstream-mac/` (git worktree). Keep the root README in English with the headings Brief Introduction and Purpose, Quick Install, Developing Mode, Source Citations.

# CLAUDE.md — cloudstream-mac

- Emulator: `./run-emulator.sh` (AVD `cloudstream_tv`), `./run-tablet.sh` (AVD `nexora_phone`); needs openjdk@17 + Android cmdline tools under /opt/homebrew.
- Launchers: edit `launchers/*.applescript`, then `osacompile -o "<App>.app/Contents/Resources/Scripts/main.scpt" launchers/<name>.applescript` and `codesign -f -s - "<App>.app"`.
- Addon: `tr-livetv-addon/start.sh` (PORT=7788); check `curl localhost:7788/manifest.json`. No tests.
- APKs are gitignored; never commit them.
