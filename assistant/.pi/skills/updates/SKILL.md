---
name: updates
description: Updates of the assistant's own components (llama.cpp, Pi, bw, whisper, TTS): list, install, roll back (güncelleme).
---
# Updates

- `updates` list: per item "name current → latest" and its one-line summary; mention breaking/security/affects_our_flags.
- Install: `updates` apply (ids optional) gives confirm_token: list the items (size for models), ask and stop. Call again with confirm_token only after the user agrees. Rollback the same way.
- It runs in the background after the reply; never say it is done.
