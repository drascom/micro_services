# Personal Assistant

## Brief Introduction and Purpose

A local macOS personal assistant with a SwiftUI desktop interface, a TypeScript/Pi backend, speech input and output, and local-model fallback. This branch contains the assistant project; its detailed operating guide is in [`docs/GUIDE.md`](docs/GUIDE.md).

## Quick Install

From the repository root on the `assistant` branch:

```bash
scripts/setup.sh
```

The setup script installs the project's declared prerequisites and components. Review its options before running it if you want a dry run or want to defer large model downloads.

## Developing Mode

From the repository root:

```bash
npm run check
npm test
./run.sh serve
```

See the detailed project guide for configuration, native-app workflows, and additional test commands.

## Source Citations

- [Detailed project guide](docs/GUIDE.md)
- [Package manifest](package.json)
- [Declared component sources and versions](components.json)
