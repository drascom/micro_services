# Repository Guidelines

## Project Structure & Module Organization

Application code lives in `src/`. `index.ts` defines the HTTP API,
`direct-zigbee-source.ts` owns direct coordinator access, and `device-store.ts`
normalizes Zigbee data into UID-based device views. Home Assistant discovery,
favorites, connection settings, and Matterbridge integration each have focused
modules. The dependency-free dashboard has no build step and
is edited directly: markup in `public/index.html`, styles in
`public/css/panel.css`, logic in `public/js/*.js` (classic `<script src>`, one
shared scope, load order = the `<script src>` order in `index.html`, with
`99-bind.js` always last). A new panel file must be added to `index.html` and
`panelAssetRoutes` in `src/index.ts` together; `scripts/panel-graph.mjs` guards
that. The panel's look runs on two view systems selected by the `data-sky`
attribute on the root element: `fixed` (Light · Dark · System — flat ground, no
sky animation) and `live` (the "by the sun" mode — phased sky gradient, sun and
moon on a rotating arm, card ink derived from the time of day). `data-theme`
stays on its own axis, and every surface is written through one glass token
family (`--glass-*`), so never hard-code colors into individual rules.
Deployment units live with their host adapter (`apps/linux/systemd/`); safe
defaults are in `config/default.yaml`.

The brain is shared: `src/`, `public/` and `apps/runtime/`. `apps/android/` and
`apps/linux/` carry host-specific launching, packaging and lifecycle code only.
No capability a user can reach may live in a host adapter — if it does, the two
hosts fork and the feature silently exists on one of them. Anything a host
"prepares" (configuration, generated keys, seeded files) belongs in
`apps/runtime/first-run.cjs`, which both hosts run; the host passes `--data-dir`
and asks `/api/android/diagnostics` for the result. `scripts/check-host-adapters.mjs`
(part of `npm run check`) enforces the mechanically checkable part of this:
unknown runtime flags, shared config/state file names, device-model and home
protocol identifiers, and Android JS-bridge methods with no consumer in shared
code. It deliberately does not scan for domain words in general — user-visible
strings like "Connecting to the Zigbee coordinator" are legitimate in a host
splash screen, and such a rule would only produce noise.

## Build, Check, and Development Commands

- `npm install` installs Node.js dependencies.
- `npm run dev` starts the TypeScript server in watch mode.
- `npm run build` compiles `src/` into `dist/`.
- `npm run check` runs the structural gate (`scripts/check-graph.mjs`).
- `npm test` runs build then check.
- `npm start` runs the compiled production server.

Use Node.js 22 or newer. Run `npm test` before publishing changes.

## Coding Style & Naming Conventions

Use TypeScript with two-space indentation, semicolons, and ESM imports ending
in `.js`. Prefer small modules with explicit interfaces. Functions and
variables use `camelCase`; classes and interfaces use `PascalCase`. All
low-level device operations must use the immutable IEEE UID, never a friendly
name.

## Verification Guidelines

There is no unit-test suite; the `src/*.test.ts` and `apps/runtime/*.test.cjs`
files were deliberately removed. Verification is `npm test` (a clean `tsc`
build plus `npm run check`, the panel and runtime module-graph consistency
gate). Do not add test files back unless asked. The one requested exception is
`test/failover.test.mjs` (`npm run failover:test`): pure failover policy, replication and a
virtual-clock two-node simulation of the ownership handshake. Nothing run for verification
may toggle physical devices or require a live coordinator.

## Commit & Pull Request Guidelines

Use short imperative commit subjects, such as `Add Home Assistant discovery`.
Pull requests should explain behavior changes, affected protocols, validation
performed, and any restart or migration requirement. Include screenshots for
material dashboard changes.

## Security & Configuration

Never commit MQTT passwords, Home Assistant tokens, Zigbee network keys,
runtime favorites, or backup files. Keep `config/default.yaml` generic and use
environment overrides or deployment-local configuration for secrets.
