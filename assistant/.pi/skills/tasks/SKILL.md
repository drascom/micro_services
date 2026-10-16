---
name: tasks
description: Scheduled assistant tasks (görev) to create, list, change, pause, resume, run now or remove.
---
# Tasks

- create: `schedule` = the user's time words; `prompt` = what to do, self-contained. Tell the returned schedule ("Her gün 09:00, sonraki yarın 09:00"), ask to save, stop. Call again with confirm_token only after the user agrees.
- `invalid_schedule`: ask the user for the time again; never guess.
- Tasks run unattended with read-only tools; they cannot send, change or delete anything.
