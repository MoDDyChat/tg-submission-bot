# Edit Locks in tg-submission-bot

## Purpose

Optimistic locks (edit locks) prevent two moderators from editing the same resource at the same time. Implemented in `services/edit_lock.py` via the `edit_locks` table.

---

## Table `edit_locks`

| Field | Type | Description |
|------|-----|----------|
| resource_type | VARCHAR(64) PK | Resource type (see below) |
| resource_id | VARCHAR(128) PK | Resource ID (see below) |
| moderator_id | BIGINT NOT NULL | Telegram ID of the lock holder |
| acquired_at | TIMESTAMPTZ NOT NULL | Acquisition time |
| expires_at | TIMESTAMPTZ NOT NULL | Expiration time (UTC) |

**Composite PK:** (`resource_type`, `resource_id`)

---

## Resource types

| resource_type | resource_id | What it protects | TTL |
|---------------|-------------|--------------|-----|
| `submission` | `str(submission.id)` | Viewing/editing a specific post | `config.edit_lock_ttl_seconds` (default 300s) |
| `management` | `presets` | CRUD on tag sections and presets | `config.edit_lock_ttl_seconds` (default 300s) |
| `management` | `banned` | Banned-user list + unban | `config.edit_lock_ttl_seconds` (default 300s) |

---

## API (`services/edit_lock.py`)

```python
acquire_lock(resource_type, resource_id, moderator_id, ttl_seconds) -> bool
```
Attempts to acquire the lock. Returns `True` on success. If someone else's lock is active, returns `False` (does not raise). Uses `INSERT ... ON CONFLICT DO UPDATE` — atomic.

```python
extend_lock(resource_type, resource_id, moderator_id, ttl_seconds) -> bool
```
Extends the TTL of an existing lock held by the same moderator. Returns `False` if the lock has expired or belongs to another moderator.

```python
release_lock(resource_type, resource_id, moderator_id) -> None
```
Releases the lock. Only if `moderator_id` matches; leaves other moderators' locks untouched.

```python
force_release_lock(resource_type, resource_id) -> None
```
Forcibly removes any lock (regardless of `moderator_id`). Used for administrative reset.

```python
cleanup_edit_locks_job(session) -> None
```
APScheduler job: deletes all records with `expires_at < NOW()`. Runs periodically. The deletion is committed **before** iterating over Telegram: each submission lock's card is then updated afterward, in its own short per-lock session, so the network call doesn't hold a transaction open with the write (see `docs/architecture.md`).

---

## Lifecycle scenarios

### submission lock

```
handler review.py (/start review_<id>)
  → acquire_lock("submission", str(sub_id), mod_id, ttl=300)
  → if False → "Пост сейчас редактирует другой модератор" ("Another moderator is currently editing this post")

During an active session (FSM viewing_post):
  → every callback starts with extend_lock(...)
  → if False → MODERATOR_LOCK_LOST → FSM clear

Terminal actions (publish / reject / ban):
  → release_lock after a successful action

handle_close:
  → release_lock explicitly

APScheduler cleanup_edit_locks_job:
  → cleans up expired records and commits; then for each submission lock
    updates the card in the forum topic (removes the "being edited" indicator)
    with no notifications — each card in its own short session
```

### management lock

```
handle_open_presets / handle_open_banned
  → acquire_lock("management", "presets"|"banned", mod_id, ttl=300)
  → if False → "Раздел сейчас редактирует @other" ("This section is currently being edited by @other")

Every CRUD callback (add/edit/delete preset, add/edit/delete section, unban):
  → extend_lock("management", "presets"|"banned", mod_id, ttl=300)
  → if False → alert "Сессия истекла, войдите снова" ("Session expired, please re-enter")

handle_close_management:
  → release_lock("management", "presets"|"banned", mod_id)
```

---

## Behavior on lock loss

On `extend_lock → False` (the lock expired or was taken over):
- The moderator receives the `MODERATOR_LOCK_LOST` message from `core/messages.py`
- The FSM is cleared (`state.clear()`)
- All temporary preview messages are deleted

---

## Configuration

```env
EDIT_LOCK_TTL_SECONDS=300   # Lock TTL in seconds (default 5 minutes)
```

Available via `config.edit_lock_ttl_seconds`.

---

## /cancel and locks

The behavior of the `/cancel` command depends on the current FSM state:

| State | `/cancel` behavior |
|-----------|---------------------|
| No state | "Нет активного действия" ("No active action") |
| management_* | Releases `management/presets` and `management/banned` → returns to the home screen |
| `viewing_post` | Releases `submission/<id>` + updates the card → FSM is reset |
| editing_*, picking_*, confirm_* (sub-state) | Extends the lock: if we still hold it — deletes sub-state messages, returns to `viewing_post`; if the lock was lost — FSM is reset, `MODERATOR_LOCK_LOST` message |

**Key principle:** leaving a sub-state (`/cancel`) does NOT release the lock — the moderator
remains the post's "owner". Only leaving `viewing_post` (or closing via "Close")
releases the lock.

---

## noop callback (locked post button)

When another moderator has opened a post for editing, the card in the forum topic shows a
`✏️ Редактирует @mod` ("Being edited by @mod") button with `callback_data="noop"`.

The lock indicator is **always computed from `edit_locks`** inside `topics._resolve_card_lock_owner()`,
rather than being passed in by the caller: `update_submission_card()` and `probe_submission_card()` don't
accept `lock_owner`. Otherwise, any call site that forgot to pass the owner through (the tag wizard,
caption editing, scheduling) would redraw a locked post as free — with the
"New" status and a live "Edit" button. A moderator without an `@username` yields `locked_by=""`
(the generic "Редактируется модератором" ("Being edited by a moderator") label), but the card remains locked.

The `handle_noop` handler in `handlers/moderator/__init__.py`:
1. Finds the submission by `topic_card_message_id == callback.message.message_id`
2. Gets the active lock via `edit_lock.get_active_lock`
3. Responds with `callback.answer(LOCK_NOOP_HELD_BY.format(mod=...), show_alert=True)`
