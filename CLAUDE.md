> Branch `villa-bridge` of `drascom/micro_services` holds only this project, at the branch root; other projects live on their own branches and nothing is merged into `main`.
> Local checkout: `~/Documents/work/micro_services/villa-bridge/` (git worktree). Keep the root README in English with the headings Brief Introduction and Purpose, Quick Install, Developing Mode, Source Citations.

# CLAUDE.md — villa-bridge

See `AGENTS.md` in this directory for structure and conventions.

- Install: `npm ci` (Node.js 22+)
- Run (dev): `npm run dev` → http://localhost:8091 (reads `config/default.yaml`, shadow mode)
- Build: `npm run build`; production: `npm start`
- Test: `npm test` (build + `scripts/check-graph.mjs`); failover: `npm run failover:test`
- Android: `npm run android:build`, `npm run android:install`
- Linux host check: `npm run linux:doctor`
