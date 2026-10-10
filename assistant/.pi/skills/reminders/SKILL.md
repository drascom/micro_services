---
name: reminders
description: Add, list, update, complete or delete Apple Reminders (hatırlatıcı, yapılacaklar); works offline.
---
# Reminders

- Convert relative dates ("yarın 9'da") to local `due` using the local time given; date only = all-day.
- Omit `list` unless the user names one. To change an item, `list` first and use its `ref`.
- Delete: `delete` + ref only asks: ask "<title> silinsin mi?" (user's language) and stop. Use `confirm_token` only after the user's next reply confirms.
- `automation_denied`: user must allow Automation for Reminders in System Settings; do not retry.
