---
name: mail-triage
description: Organize the Gmail inbox by importance (mail düzenle, temizle, gereksizleri sil, geri al) with mail_organize.
---
# Mail triage

- `propose` first; show counts and the 🔴/🟠 lines, ask "Uygulayayım mı?" and stop. `apply` with `confirm_token` only after the user's reply confirms.
- `purge_junk` moves `Gereksiz` mail to Trash the same way (ask first). `undo` reverts the last apply.
- `missing_labels`: tell the user to create that Gmail label. Report only what the tool returned.
