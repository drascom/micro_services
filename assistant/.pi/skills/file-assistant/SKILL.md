---
name: file-assistant
description: Find, read or organize the user's files and folders (dosya, belge, klasör); offline.
---
# Files

- Find or answer: `file_search`, then `file_read` (path + chunk from the result) for the details. Say the file name; never invent content.
- Organize: `propose` first; speak 2-3 sentences (counts, main folders), ask "Uygulayayım mı?" and stop. `apply` with `confirm_token` only after the user's reply confirms. `undo` reverts the last apply. Files are only moved, never deleted.
- Report only what the tools returned.
