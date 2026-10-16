# Assistant tasks

Scheduled tasks the assistant runs on its own (Admin → **Görevler**, switch `tasks_enabled`, default off).

## Store
- `data/tasks.sqlite` (node:sqlite, Linux-ready): `tasks` + `runs` ledger, `UNIQUE(task_id, slot)` so one slot never runs twice.
- Code: `src/schedule.ts` (parser), `src/task-store.ts` (store/ledger), `src/tasks.ts` (scheduler, service, migration),
  `src/announce.ts` (idle queue), `.pi/extensions/tasks` (`tasks` tool), `.pi/skills/tasks`.

## Schedules (local time, deterministic)
| Kind | Examples |
|---|---|
| once | `in 30m`, `30 dakika sonra`, `yarın 09:00`, `tomorrow 9am`, `saat 15:00`, `2026-10-20 09:00` |
| interval (≥15 min) | `every 2h`, `her 2 saatte bir`, `30 dakikada bir`, `hourly` |
| cron | `her gün 09:00`, `hafta içi 9'da`, `her pazartesi`, `weekdays at 9am`, `ayın 1'i`, `0 9 * * 1-5` |

No time on a recurring rule = 09:00 (akşam 20:00, gece 22:00). Unreadable → error text back to the model.

## Chat
- "görev …" / "her sabah 9'da … yap" → `tasks` route (whatever Jev picked), only the `tasks` tool is sent.
- `create` returns the parsed schedule ("Her gün 09:00, sonraki yarın 09:00") + confirm token; saved only after the
  user's next reply agrees. Also `list`, `update`, `pause`, `resume`, `run`, `remove` (by name or id).

## Runs
- 60 s tick in the request lane: never during a user request; a user request cancels a run (slot re-armed, runs again).
- `next_run_at` advanced when the slot is claimed. Missed slots (sleep/off) → one catch-up run, then the next regular slot.
  A run left `running` by a crash is re-armed on start. Resume re-arms from now (no stale fire).
- Fresh isolated request: no history, no memory block, no memory extraction, no tasks tool (no task from a task).
- Unattended tools (read-only): `read`, web search/read/fetch, mail read, `mail_organize propose`, `reminders list`,
  file search/read. Blocked: mail changes, vault, file moves, updates, memory writes, tasks, other reminder actions.
- `[SILENT]` reply → stored, not delivered. Errors always delivered; from the 3rd failure in a row a pause is suggested.

## Delivery
- macOS notification + idle announcement queue (`/announce/wait`, shared with updates): task errors → update results
  → task results → update offer. Same result of a task not repeated within 24 h; results older than 12 h are not spoken.
- Asked first (bystanders), `ProactiveGate` in `src/announce.ts`: idle → soft chime + warm pulsing ring (count badge
  if >1), no speech; in a conversation (user spoke in the last 2 min) → spoken "Sana söylemem gereken N şey var,
  anlatayım mı?" after the current reply. Ring click or a short yes (evet, söyle, anlat, tamam, dinliyorum…) says all
  waiting messages; a short no (sonra, şimdi değil, hayır…) snoozes. At most one more prompt after the snooze interval;
  a new message may prompt again. Admin Görevler → Bildirim: chime on/off, snooze minutes (`announce_chime`,
  `announce_snooze_minutes`). Update restarts bypass it (no speech).
- Admin Görevler: list, next run, last result, pause/resume, run now, delete.

## Migration
Once (when Reminders and tasks are on): open items of the Reminders list `tasks_list` are copied (read-only, reminders
unchanged; `✅`/`📧` skipped). Future dated → active one-time task; undated/overdue/failed → paused. Done flag in `meta`.
The old Reminders scheduler and `data/tasks-state.json` are no longer used. Normal reminders stay in Reminders.

## Later (P5/P6)
Heartbeat checklist (separate, off by default): plug in as another `AnnouncementSource` + a system task. Retry ladder, continuity.
