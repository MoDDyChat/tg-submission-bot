# tg-submission-bot pipelines

## Viewer pipeline

1. `/start` → the bot replies with the submission rules
2. The viewer sends text or supported content (photo/video/animation/document) → the description is saved to `caption` as-is, with HTML formatting preserved (bold, italic, links); `tags` stays empty — tags are managed only through the moderator's tag wizard
3. The bot confirms: «Пост #N принят» (Submission #N accepted) + a «Отменить предложение» (Cancel submission) button
4. **The post is published to the moderator forum group's topic**: each viewer has their own topic in `MODERATOR_GROUP_ID`; for a media post — media + a status card with an «Редактировать» (Edit) button; for text-only — just a text card
   - If posting to the topic fails, the user gets an error notification
5. Media group (album) support: 2-second buffering with an `asyncio.Lock` per group_id to correctly collect all items. If an album has captions on multiple files, only the first one's caption is used, the rest are ignored with a warning to the user
6. The «Отменить» (Cancel) button cancels the post with status pending; the post in the moderator channel moves to its final status and loses the edit button
7. On publication to the channel, the viewer gets a notification

**Limits:** an empty text message is not accepted; the final plain-text limit is 1024 characters for a media post and 4096 for text-only; an unsupported media type (sticker, voice message, etc.) — the bot replies «Этот тип медиа не поддерживается» (This media type is not supported). A banned user (`is_banned`) gets a block message and submissions are not accepted.

---

## Moderator group (forum topics)

A private Telegram group with forum support enabled (`MODERATOR_GROUP_ID`), where moderators see all incoming submissions. Each viewer has their own dedicated topic (created on first submission, recorded in `user_topics`).

**How it works:**
- On submission creation → `topics.post_submission_card()` sends the media (if any) + a status card with an «Редактировать» (Edit) button to the viewer's topic
- On caption/tags/status changes → `topics.update_submission_card()` updates the text card in the topic (via `edit_message_text`)
- On publish/reject/cancel → `topics.finalize_submission_card()` finalizes the card (removes the «Редактировать» (Edit) button); the post stays in the topic for history
- On topic creation → the author card is posted as the topic's opening message and pinned (`create_author_card_message`); the plain welcome text (`TOPIC_WELCOME_TEXT`) is only a fallback for when that call fails
- On bot startup, `cleanup_legacy_legend_pin()` removes the old standalone legend pin — the legend now lives inside the dashboard message (`general:stats`)

**Status in the topic title:** `[LABEL] - Name` reflects the user's aggregate status (`compute_topic_status_key`, priority: editing > pending > scheduled, then terminal statuses; published outranks the other terminal statuses). A status transition does not call Telegram directly: `request_topic_title_sync()` atomically increments `user_topics.title_sync_version` in the same transaction as the domain change. On rollback both records roll back together, so Telegram can never get ahead of the DB.

**Title outbox:** `topic_title_sync_job` runs every 10 s, first closing out accumulated no-op revisions without any Telegram calls, then applying at most one real `editForumTopic`. On success it records the sent status to `current_status_key` and advances `title_applied_version` to the captured revision. If a new change comes in while the request is in flight, `title_sync_version` stays ahead and the job isn't lost; on error, the applied version is left unchanged so the attempt gets retried. The worker is split into three phases, each DB phase being **its own short transaction**: `_claim_next_title_revision()` claims a revision and commits, `editForumTopic` is called outside a transaction, `mark_topic_title_sync_applied()` writes the result in a new one. A transaction must never be held open across a network call: a hung request would hold a row lock on `user_topics` and block every writer behind it (see `docs/architecture.md`). A tick never sleeps: each pass makes **at most one** `editForumTopic` call, and a transient error (`TelegramRetryAfter`, `TelegramNetworkError`) or any unexpected failure only pushes a module-level deadline (`_defer_title_edits`, minimum 10 s, `retry_after` on flood control) — the following ticks return immediately until it expires and the pending revision is retried then. Waiting out `retry_after` inside the tick would keep the job running for minutes, and with `max_instances=1` APScheduler would log `maximum number of running instances reached` on every interval. For the same reason an already-running pass makes the next tick exit instead of queueing on the lock. `TOPIC_NOT_MODIFIED` counts as success. A moderator manually renaming a topic sets a forced revision via `title_force_sync_version`, and the bot's own service message from its own change does not re-create the job. A migration sets a forced revision on all existing topics once, to safely clear any residual drift left over from the old direct-write mechanism, under the same 10 s limit.

**Title self-heal:** `topic_titles_reconcile_job` runs every 10 min and only compares the computed status against `current_status_key`, queuing any detected drift into the outbox; it never calls the Telegram API itself. A revision already pending in the outbox is not bumped again. There is no nightly blind re-apply of all active topics.

**Forum topic card format:**
```
{status_emoji} Пост #{sub_id}
Статус: {status_label}
[Публикация: {time}]    ← scheduled only
[Теги: #Tag1 | #Tag2]   ← if present
[Описание: {caption}]
```

**Card self-heal:** `submissions.card_rendered_hash` holds the sha256 of the last payload (text + keyboard) that Telegram confirmed. The hash is written only after a successful send/edit — a failure leaves the old value in place. `topic_cards_reconcile_job` re-renders candidate cards every 10 min, compares hashes, and fixes only mismatches, capped at 5 per pass with a 2 s pause between them. The selection query (`list_cards_for_reconcile`) picks up all active posts with a card, plus terminal ones no older than 48 h — a finalized post never changes again, so scanning the archive would be pointless. A `NULL` hash (rows from before migration `o3p4q5r6s7t8`) is treated as drift and repaired once. If the card is already gone from the topic, `topic_card_message_id` is cleared and Recover restores it later. The phases mirror the title outbox: `_collect_card_repairs()` computes drift in a read-only transaction and commits, `editMessageText` runs outside a transaction, `mark_card_rendered_if_unchanged()` / `clear_topic_card_ids()` each write in their own short transaction — neither the retry pause nor the delay between repairs holds a row lock on `submissions`. Writing the hash from reconcile is **conditional** (CAS on `card_rendered_hash`): the repair plan is built from the hash read before the slow Telegram call, and in that window the lock holder may have already repainted the card. If the hash has moved on, the write is not applied and the post isn't counted as repaired (the `repaired` counter only grows for writes that actually landed). `mark_card_rendered()` (and its conditional variant) pin `updated_at` to its current value so that `onupdate` doesn't shift the reconcile selection window.

**Card resilience:** `update_submission_card` and `finalize_submission_card` edit the card via `_edit_card_with_retry` — up to 3 attempts with retry on `TelegramRetryAfter` and `TelegramNetworkError` (wait capped at 30 s; a longer `retry_after` is propagated immediately). `message is not modified` counts as success and isn't logged as an error. The retry is necessary because the `EditMessageText` quota in the mod group is shared with the queue board (`topics_queue`), and under bursts the card would otherwise get stuck in its old state permanently.

**Queue board:** `_render_queue_inner` / `_render_schedule_inner` commit the read-only phase (queue rows, chunks, schedule text and their checksums) before the first Telegram call. `message is not modified` counts as success and **the checksum gets written** to `system_messages` — otherwise every following pass would hit the same no-op edit again. An extra chunk is removed from `system_messages` only if Telegram confirms the deletion or the message is already gone (`message to delete not found`); on any other error the row is left in place and the deletion is retried on the next pass.

**Author card:** `services/author_card.py` keeps one pinned, edited-in-place message per author — the **opening message of their forum topic**, key `user:<id>:card` in `system_messages` — showing submission stats (`get_author_stats`), ban state and the moderator note, with «Заметка», «Забанить/Разбанить» and «Написать» buttons (`author_card_kb`, hidden/shown per role and ban state). Creation happens only when the topic is created; the render path never sends, it only edits (see `docs/architecture.md`). It never renders synchronously from a handler: `request_author_card(user_id)` only adds the id to an in-memory dirty set, and a dedicated `author_card_render_job` (every 60s) drains up to 10 dirty cards per pass with a 0.5s delay between Telegram calls, coalescing any number of dirty markings raised between ticks. `author_card_reconcile_job` (every 10 min) walks `user_topics` in cursor-paginated batches of 20 and re-marks every author dirty — self-heal for a lost in-memory dirty set after a restart, since nothing else would otherwise notice. The `/user <id|@username>` command (moderator group only) posts a one-off copy of the target's card with a deep link back to their topic; username lookup only works for users who already have a `users` row (the Bot API can't resolve a bare `@username` to an id).

**Backfilling old topics:** `scripts/backfill_author_cards.py` converts the welcome message of a pre-existing topic into its author card. The `message_id` was never stored and the Bot API cannot read history, so the script derives a candidate window from `topic_id + 1` (a topic's `message_thread_id` is the `message_id` of its `forum_topic_created` service message), caps it by `--window` and by the lowest message id already known to belong to that topic (submission cards, their media, a standalone card), skips every known id, and **verifies** each remaining candidate by forwarding it to a private chat, reading the text back and deleting the copy. Only a message whose text matches the static parts of `TOPIC_WELCOME_TEXT`, that was sent by this bot, **and** that mentions the topic's author (`@username` or full name, raw or HTML-escaped) is edited — message ids in a forum are chat-global, so `topic_id + 1` can land on a neighbouring topic's welcome message, and the author check is what rules that out (`--allow-display-mismatch` opts out). Anything unproven is reported and skipped, and the run exits non-zero if any topic errored. Converted cards get `payload.opening`, so re-runs skip them, and any standalone card left over from the old scheme is unpinned and deleted. `--dry-run`, `--limit`, `--user-id`, `--delay` and `--probe-delay` keep a probe run to a single topic and the full run gentle on the API.

**Resilience:** errors sending to the forum propagate to the calling code (`post_submission_card` is a critical operation). In the viewer handler they're caught and an error message is shown. Posts without a `topic_card_message_id` — update/delete are no-ops.

---

## Moderator pipeline

1. `/start` from a moderator in DM opens the **moderator panel** with a `Управление` (Management) button
2. `Управление` (Management) exposes the global tools (the **Пресеты** (Presets) and **Забаненные** (Banned) sections are locked by a 10-minute edit-lock — only one moderator can edit at a time):
   - **Пресеты тегов** (Tag presets) → list of sections → add / rename / delete a section → inside a section, view items, add, change `label`, change `tag`, delete. Only an admin sees the **Recover постов** (Recover posts) button
   - **Забаненные пользователи** (Banned users) → list of blocked users, with unblock via a button
   - **Recover постов** (Recover posts) *(admins only)* → runs recovery of lost cards in the forum topic
3. The moderator sees new posts in the **moderator forum group** — each viewer has their own topic, where the media + a card with an «Редактировать» (Edit) button gets posted
4. Tapping «Редактировать» (Edit) → deep link `t.me/{bot}?start=review_{sub_id}` → opens a DM with the bot; an edit lock is taken (the post's status stays `pending`), the card in the topic is updated → a preview is shown with the media + action buttons
5. Action buttons (contextual based on status):
   - **Редактировать описание** (Edit description) → FSM: enter new text (length validation accounting for tags: up to 1024 characters for a media post and up to 4096 for text-only; counted as plain text) → saved as HTML (formatting preserved) → preview shown again + topic updated. Tags are NOT affected
   - **Редактировать теги** (Edit tags) → dynamic wizard: pages for all sections from the DB in order + a custom page (see docs/tags-system.md)
   - **🖼 Редактировать медиа** (Edit media) → FSM (`editing_media`): opens the attachment manager with a list of current media (number, type, ❌ delete button), an «➕ Добавить медиа» (Add media) button and «← Готово» (Done)
      - Deletion: the ❌ button next to an item → `delete_media()` + manager redraw. The last attachment can't be deleted
      - Adding: transitions to `adding_media` → the moderator sends media (single item or an album). For an album — buffering via `media_append.buffer_append_media_group()` (2 s wait, collecting all parts), composition validation (`validate_media_group_composition`) and an async lock per `sub_id`. For a single item — synchronous addition via `append_media_to_submission()`
      - When the composition changes (compared to the `media_sig_open` signature captured on open) — `repost_submission_card()` recreates the media in the forum topic, then `update_submission_card()` updates the card; `TOPIC_NOTIFY_MEDIA_CHANGED` is sent to the topic
      - **Posts older than 48 h:** Telegram forbids `deleteMessage` for messages older than 48 hours. The old media block can't be deleted — it's left orphaned in the topic. Editing your own messages has no age limit, so the old card is rewritten as `TOPIC_CARD_OUTDATED` (marked stale) and loses its «Редактировать» (Edit) button (`_mark_card_outdated`), and a fresh block is posted below it. A `message can't be deleted` error is treated as benign (INFO, no traceback); `delete_submission_card` doesn't count the operation as a failure
      - Composition is validated against Telegram's rules: photo+video are compatible; documents only go with documents; GIF (animation) is single-item only
   - **Опубликовать сейчас** (Publish now) *(for `pending` only)* → FSM: confirmation dialog with a preview → immediately creates a Publication + calls `publish_post()` directly; on error — a message to the moderator, state rolls back to `viewing_post`
   - **Запланировать публикацию** (Schedule publication) / **Изменить время** (Change time) → FSM: inline calendar → hour → minutes → confirmation; the time is converted to UTC before saving; rescheduling updates the existing publication
   - **Снять с расписания** (Unschedule) *(scheduled only)* → cancels the APScheduler job, deletes the publication, reverts the status to pending; clears FSM data (`schedule_message_id`, `prompt_message_id`)
   - **Связаться с автором** (Contact the author) → FSM: enter a message → forwarded to the viewer
   - **Отклонить** (Reject) → FSM: enter a rejection reason → status becomes rejected + viewer notification + the card in the forum topic is finalized
   - **Отклонить тихо** (Reject silently) → moves the post straight to `rejected`, finalizes the card in the forum topic, and does not notify the viewer
   - **Заблокировать автора** (Ban the author) → FSM: enter a reason → `is_banned=True` + `ban_reason` in the DB; unblocking is done via `Управление` (Management) → `Забаненные пользователи` (Banned users)
   - **Закрыть** (Close) → deletes all DM preview messages (media, buttons, prompt) and clears the FSM
6. `/cancel` — the moderator handler clears the FSM + deletes all preview messages from the DM, including the management message and any CRUD prompts

After editing the description: the prompt and the moderator's reply are deleted, the media is redrawn with the updated text. After the tags wizard: the wizard message is deleted, the preview is redrawn. After the media manager: if the composition changed, the card in the topic is recreated; otherwise the preview is simply shown again.

`/cancel` in the `editing_media` / `adding_media` state (category `"sub"`): cancels pending additions (calls `cancel_append_for_sub`), extends the lock, deletes sub-state messages, returns to `viewing_post` without releasing the lock. Media already recorded in the DB stays; an incomplete album is cancelled.

### Moderator FSM states (ModeratorReview)

```
viewing_post → editing_caption
             → editing_tags_presets → editing_tags_custom
             → editing_media → adding_media
             → entering_reject_reason
             → entering_ban_reason
             → picking_date → picking_hour → picking_minute → confirm_schedule
             → confirm_publish_now
```

**FSM data stored in state while viewing a post:**
- `sub_id` — ID of the current post
- `media_message_ids` — list of media message IDs (for deletion on navigation/re-render)
- `actions_message_id` — ID of the «Выберите действие» (Choose an action) message
- `prompt_message_id` — ID of the prompt while editing/rejecting/adding media
- `wizard_message_id` — ID of the tags wizard message
- `media_manager_message_id` — ID of the media manager message (for deletion on close)
- `media_added_message_ids` — list of media-added confirmation message IDs
- `media_sig_open` — sorted list of `media.id` at the time the manager was opened (for change detection)
- `management_message_id` — ID of the moderator home screen / management menu

**Additional FSM data during the tags wizard:**
- `wizard_caption` — the current description (for the preview)
- `wizard_sections` — a `section_key -> list[tag]` dict of selected presets per section
- `wizard_custom` — list of custom tags
- `wizard_page_index` — the dynamic wizard's current page
- `wizard_message_id` — ID of the wizard message (for in-place editing)

**Additional FSM data in the management UI:**
- `management_message_id` — the single editable message for the home screen / menu / CRUD
- `management_preset_type` — current preset section key (`category`, `setting`, `character`, `section_N`, ...)
- `management_preset_id` — currently selected preset item

**Edge-case behavior:** if a section or preset disappears while a moderator has the wizard open, the values already selected are not lost — they're automatically moved into `wizard_custom`.

---

### Calendar and time picker

- Inline calendar with month navigation (◀ ▶)
- Past dates are blocked (shown as «·»)
- Hours: a 6×4 grid (00–23); if the date is today, past hours are blocked
- Minutes: 5-minute steps (00–55, 3 rows of 4); if it's today and the current hour, past minutes are blocked (strictly less than the current minute; the current minute itself is available)
- A **← Назад** (Back) button on the hours screen (→ to the calendar), on the minutes screen (→ to the hours) and on the confirmation screen (→ to the minute picker, keeping the selected date and hour)
- Final confirmation: «Подтвердить» (Confirm) / «Изменить время» (Change time) / «Отмена» (Cancel); on confirmation it validates that the date is still in the future

---

## Moderator ↔ viewer communication

Two independent, non-overlapping reply channels — a viewer's reply always disambiguates cleanly because the two reply-target texts match different regexes, checked with the direct pattern first (`handlers/contact.py`):

**Submission channel** (tied to a post, `messages.submission_id` set, `target_user_id` NULL):
1. The moderator taps «Связаться с автором» (Contact the author) while viewing a post
2. Enters text → the bot forwards it to the viewer tagged «Сообщение от модератора по поводу поста #N» (`MODERATOR_MESSAGE_TO_VIEWER`, Message from a moderator about post #N)
3. The viewer replies with **reply** text to the bot's message → the `IsModeratorReply` filter intercepts the reply → the bot extracts `sub_id` from the text (regex `msg.VIEWER_REPLY_PATTERN` = `поста #(\d+)` from `core/messages.py`) and posts it to the author's forum topic (`topic_notifications.notify_contact_from_viewer`)
4. If the viewer replies with a media file, the bot rejects it with a hint (media replies are not supported)

**Direct channel** (no submission involved, `messages.submission_id` NULL, `target_user_id` set): reachable from the author card's «Написать» (`AuthorCardCB` action `contact`) rather than from a specific post — for authors with no open submission, or messages unrelated to any post.
1. The moderator taps «Написать» on an author's card → enters text → `handlers/contact.py::deliver_direct_message` sends it tagged «Сообщение от модерации» (`MODERATOR_DIRECT_MESSAGE_TO_VIEWER`)
2. The viewer replies with **reply** text → `IsDirectModeratorReply` matches on `msg.DIRECT_REPLY_PATTERN` = `Сообщение от модерации` (checked *before* the submission pattern, since a moderator's direct message could itself quote «поста #NN» inside forwarded text) → posted to the author's forum topic (`topic_notifications.notify_direct_from_viewer`)
3. If the viewer replies with a media file, the bot rejects it with the same media-not-supported hint

Common to both channels: when the media composition of a submission changes, `TOPIC_NOTIFY_MEDIA_CHANGED` is sent to the forum topic (🖼 Состав медиа изменён модератором — media composition changed by moderator); every text message on either channel is saved as a row in `messages`, distinguished by which of `submission_id` / `target_user_id` is set.
