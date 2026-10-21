# AGENTS.md

Repository-wide guidance for coding agents working in `drascom/micro_services`.

## Repository model

- `main` is the repository index and shared-management branch; it does not host project implementation work.
- Each project has one dedicated branch named for that project (for example, `scan`, `diziflix`, `assistant`, `inet-viewer`, `villa-bridge`, or `cloudstream-mac`). Keep that project's implementation and project-specific documentation on its branch.
- Do not combine project branches or move project implementation into `main` unless the user explicitly asks.
- Before editing, verify the current branch and working tree. Preserve unrelated changes and do not overwrite work in progress.

## README requirements

- The root `README.md` on `main` is only the repository homepage: maintain a concise project inventory table with each project's name, a short description, and a direct link to that project's README on its branch.
- The root `README.md` on a project branch must describe only that branch's project, and must be written in English.
- Project README sections must appear in this order, using these headings: `Brief Introduction and Purpose`, `Quick Install`, `Developing Mode`, and `Source Citations`. Keep instructions specific to that project and cite verifiable sources; do not invent commands, dependencies, or citations.
- When adding or renaming a project, update the `main` inventory and its branch README. Keep shared root management files consistent across project branches when those files are intentionally changed.

## Project changes

- Check the target branch before making changes; never change another project's branch as a side effect of work on the current project.
- Keep project dependencies, configuration, and deployment instructions isolated to that project's branch and directory.
- Store secrets only in ignored local environment files; document required variables in `.env.example` and never commit credentials.
- Use each project's existing environment and test commands. Prefer `uv` over `pip` for Python projects when available.
- Update the project's root README when install, development, or source details change, then verify commands and links against the branch contents.
