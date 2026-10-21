# CLAUDE.md

Repository-wide guidance for agents working in `drascom/micro_services`.

## Repository model

- `main` is the repository index and shared-management branch; project implementation belongs on a dedicated branch named for that project (`scan`, `diziflix`, `assistant`, `inet-viewer`, `villa-bridge`, `cloudstream-mac`).
- Keep each project's implementation and project-specific documentation on its own branch. Do not combine project branches or move implementation into `main` unless explicitly requested.
- Before editing, verify the current branch and working tree. Preserve unrelated work and avoid overwriting in-progress changes.

## README requirements

- The root `README.md` on `main` is only the repository homepage. Keep a concise table listing every project, a short description, and a direct link to its README on its project branch.
- The root `README.md` on each project branch must cover only that project and must be in English.
- Use these headings in this exact order: `Brief Introduction and Purpose`, `Quick Install`, `Developing Mode`, `Source Citations`. Keep all commands and citations verifiable from the project sources; do not guess.
- When adding or renaming a project, update the homepage index and that project's branch README. Keep shared management files consistent across branches when intentionally changing them.

## Safe project work

- Work only on the branch for the requested project. Do not change another project's branch as a side effect.
- Keep project dependencies, configuration, and deployment details isolated to that project.
- Secrets belong only in ignored local environment files. Document required variables in `.env.example`; never commit credentials.
- Follow each project's existing environment and test commands; prefer `uv` over `pip` for Python where available.
- Verify README links and commands against the target branch before committing.
