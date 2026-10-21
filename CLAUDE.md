# CLAUDE.md

Repository-wide guidance for agents working in `drascom/micro_services`.

## Repository model

- `main` holds only the project index (`README.md`) and these shared rule files (`CLAUDE.md`, `AGENTS.md`, `.gitignore`); no project code.
- Each project lives on its own branch (`scan`, `diziflix`, `assistant`, `inet-viewer`, `villa-bridge`, `cloudstream-mac`) with its files at the branch root — no `<project>/` subdirectory. Each project branch has its own `README.md`, `CLAUDE.md`, `AGENTS.md` and `.gitignore`; project run/test commands are in that branch's `CLAUDE.md`.
- Branches are never merged into `main` or into each other unless the user explicitly asks.
- Local layout: one git worktree per branch at `~/Documents/work/micro_services/<branch>/` (this branch at `.../main/`); on hermes `~/work/micro_services/<branch>/`.
- Before editing, verify the current branch and working tree. Preserve unrelated changes and do not overwrite work in progress.

## README requirements

- The root `README.md` on `main` is only the repository homepage. Keep a concise table listing every project, a short description, and a direct link to its README on its project branch.
- The root `README.md` on each project branch must cover only that project and must be in English.
- Use these headings in this exact order: `Brief Introduction and Purpose`, `Quick Install`, `Developing Mode`, `Source Citations`. Keep all commands and citations verifiable from the project sources; do not guess.
- When adding or renaming a project, update the homepage index and that project's branch README. Project branches do not carry these shared files.

## Safe project work

- Work only on the branch for the requested project. Do not change another project's branch as a side effect.
- Keep project dependencies, configuration, and deployment details isolated to that project.
- Secrets belong only in ignored local environment files. Document required variables in `.env.example`; never commit credentials.
- Follow each project's existing environment and test commands; prefer `uv` over `pip` for Python where available.
- Verify README links and commands against the target branch before committing.
