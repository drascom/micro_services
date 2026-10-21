---
name: skill-manager
description: Create, edit, inspect or validate Ayhan's app-owned harness skills when the user asks. Cannot change extension code or permissions.
---
# Skill management

Call Pi "the harness". Use `read`, `write`, `edit`, `test_skill`; changes only inside `.pi/skills/`. Preserve user-written rules.

A skill is a lowercase-hyphen directory with `SKILL.md` whose frontmatter has a matching `name` and a short `description` of when it applies. Keep instructions brief (context budget), use relative paths, describe only available tools. A skill cannot grant abilities or change permissions.

After saving run `test_skill` and fix problems; it checks structure, not behavior. Never run scripts, install packages, send messages or edit extensions; propose new executable behavior for separate authorization. Changes apply from the next request. Be concise, no emoji.
