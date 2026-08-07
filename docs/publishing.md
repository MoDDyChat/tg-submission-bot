# tg-submission-bot Publishing System

## Publication scheduling system

**Architecture:** APScheduler (MemoryJobStore) + PostgreSQL as source of truth.

- Publish time is stored in UTC (converted from the moderator's local timezone on input)
- On scheduling: a record in `publications` is created/updated + an APScheduler job with `trigger="date"` (`replace_existing=True`)
- On rescheduling: the old APScheduler job is cancelled first (`cancel_scheduled`), then the publication is updated in the DB, then a new job is created. This eliminates the race condition between the old job and the DB update
- On unscheduling: the APScheduler job is cancelled (`cancel_scheduled`), the publication is deleted, status → pending
- On bot restart:
  1. `SELECT * FROM publications WHERE published_at IS NULL`
  2. `publish_at > NOW()` → the APScheduler job is recreated
  3. `publish_at <= NOW()` → immediate publication (missed during downtime), if the overdue time doesn't exceed 24 hours
  4. All datetimes are normalized to UTC before comparison
- Publications overdue by more than 24 hours are skipped with a warning in the logs and require manual review by a moderator

---

## Slot occupancy in the schedule wizard

`services/schedule_occupancy.py` shows moderators which time slots are already taken while they're picking a date/hour/minute in the schedule calendar, so they don't have to cross-check the General-topic schedule post separately.

- **Query:** `db/queries/publications.py::get_scheduled_times_between(session, start, end, exclude_submission_id=None)` returns `(publish_at, submission_id)` pairs for the half-open UTC interval `[start, end)`. It only considers **live** scheduled publications — `published_at IS NULL` and `dead_at IS NULL` — so already-published and dead publications (see below) never count as occupancy. The query also joins `submissions` and only counts rows whose `submissions.status == "scheduled"`, mirroring the publisher's own guard (`services/publisher.py:106,151`, which skips any publication whose submission is no longer `scheduled`), so a publication left behind by a submission that moved out of `scheduled` never shows as occupancy. `exclude_submission_id` drops the currently-edited submission's own slot, which matters on reschedule.
- **`get_month_occupancy(session, year, month, tz, exclude_submission_id=None) -> dict[int, int]`:** builds the local-month boundary (`datetime(year, month, 1, tzinfo=tz)` through the first of the next month, with a December→January rollover), converts both ends to UTC, queries, then buckets each row's `publish_at` back into `tz` and counts posts per day-of-month.
- **`get_day_occupancy(session, year, month, day, tz, exclude_submission_id=None) -> DayOccupancy`:** same pattern for a single local day (`[00:00, +1 day)`). Returns a `DayOccupancy` with `hours: set[int]`, `minute_slots: set[tuple[int, int]]` (minute floored to the 5-minute picker grid: `minute - minute % 5`), and `entries: list[tuple[datetime, int]]` (local datetime + submission_id, already ordered by `publish_at`).
- Any `publish_at` that comes back tz-naive from the driver is treated as UTC (`replace(tzinfo=timezone.utc)`) before conversion to local time.
- The local timezone always comes from `config.timezone` (via `ZoneInfo`), matching how `publish_at` itself is converted on input.

**Keyboard markers (`keyboards/calendar.py`):**
- `calendar_kb(..., busy_days: dict[int, int] | None = None)` — a day with a positive count gets a negative circled number appended to its button label (`15 ❷`, `20 ⓬`). Button labels are plain text — Telegram parses no markup there — so the emphasis has to come from the glyph itself; `_busy_marker` covers 1–20 (`❶`–`❿` from Dingbats, then `⓫`–`⓴`) and falls back to plain parentheses (`20 (21)`) above that, since Unicode has no circled numbers past 20. The `callback_data` is unchanged.
- `hours_kb(..., busy_hours: set[int] | None = None)` — a busy hour is prefixed with `🔸` (`🔸12`); still clickable.
- `minutes_kb(..., busy_minutes: set[int] | None = None)` — a busy 5-minute slot is prefixed with `🔸` (`🔸18:30`); still clickable.
- All three parameters default to `None`/falsy, so calls that don't pass occupancy data render exactly as before.

**Text block on the hour/minute screens (`handlers/moderator/schedule.py`):** `_busy_text(base, occupancy)` inserts a busy-slot list between the date line and the "pick hour/minute" prompt of `msg.PICK_HOUR`/`msg.PICK_MINUTE`. Header is `msg.PICK_BUSY_HEADER`, each entry is `msg.PICK_BUSY_LINE.format(time=..., sub_id=...)`. The list is capped at `_BUSY_LIST_CAP = 20` entries, with a trailing `…` if there are more; an empty `occupancy.entries` skips the block entirely (screen text is unchanged from the plain `PICK_HOUR`/`PICK_MINUTE` format).

---

## Dead publications

`recover_scheduled_jobs` (called once on every bot startup) is also where an overdue publication gets marked **dead** instead of recovered:

- For each unpublished publication with `publish_at <= now`, if the overdue time exceeds 24 hours, no APScheduler job is (re)created — the bot instead calls `mark_publication_dead(session, pub.id)`, which does `UPDATE publications SET dead_at = now() WHERE id = :id AND dead_at IS NULL`.
- The `dead_at IS NULL` guard makes the UPDATE idempotent and its result (`rowcount > 0`) tells the caller whether this call was the one that *first* marked it dead. Only newly-marked publications are collected into the admin DM (`ADMIN_NOTIFY_DEAD_PUBLICATIONS`, one message listing all of them) — so a publication that was already dead from a previous restart never re-notifies admins.
- `dead_at IS NULL` means alive; a non-NULL `dead_at` means the publication was overdue by more than 24h with no job scheduled for it. `services/dashboard.py` counts dead publications separately from the `scheduled` bucket (`scheduled - dead`), and `topics_queue._build_schedule_lines` pulls dead publications out of the normal day-block layout into a dedicated block at the top of the schedule post (`SCHEDULE_DEAD_HEADER` + one `SCHEDULE_DEAD_LINE` per item), since they carry their own overdue date rather than an upcoming one.
- **Rejecting a scheduled post:** the reject buttons stay on the card while a post is `scheduled`, so a terminal transition can happen without going through **Снять с расписания** first. Both reject handlers therefore call `handlers/moderator/_helpers.py::_drop_pending_publication` before flipping the status — it deletes the `publications` row and returns its id; the handler commits, only then calls `cancel_scheduled(pub_id)` (job cancellation is not rollback-able, and a crash in that window merely leaves a job firing into a missing publication, which the publisher logs and skips) and re-renders the schedule post. Without it the row would survive with `published_at IS NULL`: the publisher's status guard keeps it from ever reaching the channel, but it would stay in the schedule post forever, get its job recreated on every restart and eventually be marked dead (a false `ADMIN_NOTIFY_DEAD_PUBLICATIONS` DM, plus a negative `scheduled` count in the dashboard, which computes `scheduled - dead` off the submission statuses).
- **Recovering a dead publication:** rescheduling it through the normal **Изменить время** (Change time) flow calls `update_publication_time`, which sets a new `publish_at` **and** resets `dead_at` back to `NULL` in the same UPDATE — the publication becomes a normal scheduled job again and disappears from the dead block on the next schedule render.

---

## Publishing (publisher.py)

1. **Idempotency:** checks `published_at` — if already published, skips
2. Loads the publication/submission with media or text-only content and the user's data
3. Checks that the publication still exists and the submission is still in `scheduled` status; cancelled, unscheduled, or stale jobs are not published
4. Assembles the final caption: `compose_caption(sub.tags, edited_caption or sub.caption)` — tags on top joined by ` | `, description below
5. Sends to the channel **with retry** (up to 3 attempts):
  - No media → `send_message`
   - 1 media item → `send_photo/video/animation/document`
   - Multiple → `send_media_group` (all `message_id`s are saved in `channel_message_ids`)
   - `TelegramRetryAfter` → waits `retry_after` seconds, retries
   - `TelegramNetworkError` → retries after 5 sec
   - Other errors → `PublishFailedError`, no retry
6. **Phase 1 (critical):** Updates the DB: `mark_published()` + status → published → `commit()`
7. If the DB commit fails after a successful channel send, the publisher first retries the write in a new DB session; if that also fails, it tries to delete the already-sent channel messages as a compensating action
8. If the compensating deletion also fails, an ambiguous-state error is raised — requires manual review by a moderator
9. **Phase 2 (best-effort):** Finalizes the card in the forum topic (`finalize_submission_card`) — removes the edit button and updates the status; errors are logged, not blocking
10. **Phase 3 (best-effort):** Notifies the viewer: "Твой пост опубликован" ("Your post has been published")
11. **Phase 4 (best-effort):** Updates the queue board (`render_queue(bot, session)`) — removes the published post from the pending list
12. All exceptions (not just `MSBotError`) are caught and logged in the scheduler

---

## Domain exceptions (core/exceptions.py)

| Exception | Raised in | Caught in |
|------------|--------------|-------------|
| `SubmissionNotFoundError` | `publisher.py` | `scheduler.py` |
| `SubmissionCancelledError` | `publisher.py` | `scheduler.py` |
| `SubmissionStatusError` | `publisher.py` | `scheduler.py` |
| `PublicationNotFoundError` | `publisher.py` | `scheduler.py` |
| `PublishFailedError` | `publisher.py` | `scheduler.py` |
| `PublishStateUnknownError` | `publisher.py` | `scheduler.py`, `publish_now.py` |
| `UserNotReachableError` | — | — |

Handlers don't raise domain exceptions — they handle situations inline and respond to the user.

---

## Error handling

- **Retry in publisher:** up to 3 attempts on `TelegramRetryAfter` (waits `retry_after`) and `TelegramNetworkError` (waits 5 sec); this also covers Telegram flood control
- **Viewer blocked the bot:** caught during notification (`publisher.py`), logged as a warning
- **Viewer cancelled the post during moderation:** status is checked at every FSM step; terminal statuses: `cancelled`, `published`, `rejected`
- **Bot restart:** all scheduled jobs are restored from publications; FSM state is lost with MemoryStorage (preserved with RedisStorage — `REDIS_URL`); "Close" works correctly via `callback.message.delete()`
- **Graceful shutdown:** the bot waits for in-flight media groups to finish (up to 30 sec) and calls `scheduler.shutdown(wait=True)`
- **Moderator forum topic errors:**
  - `topics.post_submission_card` — propagates the exception to the caller; the viewer sees an error message; card IDs (`topic_card_message_id`, `topic_media_message_ids`) are written only after a successful send
  - `topics.update_submission_card` / `topics.finalize_submission_card` — best-effort operations; log a warning; don't break the main flow
  - `topics.delete_submission_card` clears `topic_card_message_id` / `topic_media_message_ids` only if all messages were actually deleted; on partial failure the IDs are kept for retry and diagnostics
- **Transactions:** DbSessionMiddleware auto-rolls back on `BaseException` (including `CancelledError`)
- **Publishing:** split into a critical phase (DB commit) and best-effort phases (finalizing the forum topic card, notifying the viewer)
- **Idempotency:** `publish_post()` checks `published_at` before republishing
- **Stale scheduler jobs:** if the publication has already been deleted or the submission is no longer `scheduled`, the job is logged as skipped and publishes nothing
- **Compensation after partial failure:** if writing the publication result to the DB fails, the publisher tries to delete the already-sent channel messages; if that also fails, an ambiguous-state error is logged and manual review is needed
- **Unhandled exceptions:** caught by `sys.excepthook` and logged at CRITICAL level
