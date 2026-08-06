# tg-submission-bot Architecture

## Purpose

A Telegram bot for a creative content submission system (art, animations, video, etc.) from viewers, with moderation and scheduled publishing to the main Telegram channel.

**Roles:** one or more moderators (IDs via `MODERATOR_IDS` in config), unlimited number of viewers.

---

## Stack

| Component | Technology | Purpose |
|-----------|-----------|------------|
| Bot framework | aiogram 3.x | Telegram Bot API, FSM, routers, middleware |
| ORM | SQLAlchemy 2.0 async | Models, queries, relationships |
| DB | PostgreSQL + asyncpg | Data storage |
| Migrations | Alembic | DB schema versioning |
| Scheduler | APScheduler 3.x | Deferred publishing |
| Configuration | pydantic-settings | Typed settings from .env |
| FSM Storage | MemoryStorage / RedisStorage | Optional: persistent FSM via Redis (`REDIS_URL`) |
| Deploy | Docker + docker-compose | Bot and Redis in compose; Postgres optional as an external service or a commented-out block |

---

## Project structure

```
tg-submission-bot/
├── main.py                    # Entry point: sys.path → src/, asyncio.run(main())
├── .env.example               # Environment variable template
├── .gitignore
├── .dockerignore              # Excludes .venv, __pycache__, .git, .env, logs/
├── requirements.txt
├── requirements-dev.txt        # Dev deps: pytest, pytest-asyncio, pytest-cov
├── Dockerfile                 # python:3.12-slim, non-root user (botuser), CMD ["python", "main.py"]
├── docker-compose.yml
├── alembic.ini                # prepend_sys_path = src
├── pytest.ini                 # Pytest config + integration marker
│
├── alembic/
│   ├── env.py                 # sys.path → src/, Base.metadata + core.config; supports config.attributes["connection"] for programmatic invocation
│   ├── script.py.mako         # Migration template
│   └── versions/              # Auto-generated migrations
│
└── src/
    ├── core/
    │   ├── bot.py             # _create_dispatcher(), _create_storage(), main() — lifecycle, proxy, ensure_general_topic_nav(), polling
    │   ├── config.py          # Nested Settings: BotSettings, DBSettings + top level; moderator_ids, redis_url, proxy_url
    │   ├── messages.py        # All user-facing strings (constants with placeholders)
    │   ├── logging.py         # create_logger() / get_logger() / fmt_user() — RotatingFileHandler + console
    │   ├── exceptions.py      # Domain exceptions: MSBotError, Submission*, Publication*, UserNotReachableError
    │   ├── rules_config.py    # Channel rules text configuration
    │   └── topic_status_config.py  # Topic status priority/emoji configuration
    │
    ├── db/
    │   ├── models.py          # SQLAlchemy ORM: User, Submission, SubmissionMedia, Publication, TagPresetSection, TagPresetEntry, Message
    │   ├── session.py         # module-level engine + session_factory, check_db_connection(), run_migrations(), shutdown_db()
    │   ├── queries/                # CRUD operations package (SQLAlchemy async)
    │   │   ├── __init__.py         # Re-exports public functions
    │   │   ├── submissions.py      # Submission CRUD
    │   │   ├── submission_media.py # add_media, delete_media, get_media
    │   │   ├── publications.py     # Publication CRUD
    │   │   ├── tag_presets.py      # CRUD for tag preset sections/entries
    │   │   ├── users.py            # upsert_user, ban/unban
    │   │   ├── topics.py           # UserTopic CRUD
    │   │   └── messages.py         # system_messages CRUD
    │
    ├── handlers/
    │   ├── common.py          # /start (rules), /help, /cancel — logged via fmt_user()
    │   ├── viewer.py          # Receiving media and text posts, media groups (with asyncio.Lock), post cancellation; warning about extra captions in an album
    │   ├── moderator/         # Moderator package (refactored from the monolithic moderator.py)
    │   │   ├── __init__.py    # router + include sub-routers + /cancel
    │   │   ├── _helpers.py    # _delete_tracked_messages(), _send_submission_view(), TERMINAL_STATUSES
    │   │   ├── management.py  # Moderator home screen + global management menu + CRUD for preset sections/entries
    │   │   ├── recover.py     # Card recovery logic for the Recover button
    │   │   ├── review.py      # moderator `/start` + deep link /start review_<id>, handle_close
    │   │   ├── edit.py        # Caption editing (FSM)
    │   │   ├── reject.py      # Rejection with or without a reason
    │   │   ├── schedule.py    # Calendar → hour → minutes → confirmation (~200 lines)
    │   │   ├── publish_now.py # Immediate publishing with confirmation
    │   │   ├── unschedule.py  # Removing from schedule
    │   │   ├── ban.py         # Banning the author from the post card
    │   │   ├── media.py       # Media Manager: add/delete media in existing submissions (FSM `editing_media` / `adding_media`)
    │   │   ├── submit.py      # viewer submission intake from moderator context
    │   │   └── view.py        # submission viewing/navigation handler
    │   ├── tag_wizard.py      # Dynamic tag wizard: N sections from the DB + custom page
    │   └── contact.py         # Moderator ↔ viewer contact; IsModeratorReply — reply filter (regex from msg.VIEWER_REPLY_PATTERN)
    │
    ├── keyboards/
    │   ├── callbacks.py       # CallbackData factories (SubmissionCB, ManagementCB, TagPresetCB, CalendarCB, TimeCB, TagWizardCB, etc.)
    │   ├── viewer.py          # "Cancel submission" button
    │   ├── moderator.py       # Action buttons, moderator-home, management UI, button in the moderator channel, confirmations
    │   ├── calendar.py        # Inline calendar + hour/minute picker (5-min step, blocks the past, back buttons)
    │   └── tags.py            # Dynamic tag wizard keyboards: section pages + custom page
    │
    ├── states/
    │   ├── moderator.py       # ModeratorReview: post-card states + management + tag wizard + ban
    │   └── contact.py         # ContactViewer (writing_message)
    │
    ├── middlewares/
    │   ├── rate_limit.py      # ThrottleMiddleware: 20 req/10s per user; alert on CallbackQuery; auto-cleanup of empty keys
    │   ├── db.py              # DbSessionMiddleware: injects AsyncSession, auto commit/rollback (BaseException)
    │   └── auth.py            # AuthMiddleware: upsert user → data["db_user"], logs new users
    │
    ├── services/
    │   ├── publisher.py          # Publishing to the channel + notifying the viewer + finalizing the card; idempotency; retry (3 attempts); calls render_queue after publishing
    │   ├── scheduler.py          # APScheduler wrapper + restoring jobs from the DB; topic_title_sync_job processes one outbox task/10s, reconcile every 10 min only queues drift
    │   ├── topics.py             # Forum topics service: cards, transactional request_topic_title_sync, outbox consumer, DB-only reconcile, ensure_general_topic_nav
    │   ├── topics_queue.py       # Queue board: render_queue(bot, session) — event-driven; builds/updates general:queue:NN messages in the General topic; idempotency via MD5; asyncio.Lock against concurrent calls
    │   ├── edit_lock.py          # Optimistic edit locks: acquire/extend/release/force_release + cleanup
    │   ├── admin_notifications.py # Sends DM notifications to all is_admin users on preset/section CRUD
    │   ├── topic_notifications.py # In-topic notifications (published, rejected, message from mod)
    │   ├── media_append.py       # Album buffering for media append
    │   └── submission_intake.py  # Viewer media-group buffering on initial submission
    │
    ├── filters/
    │   ├── is_moderator.py    # IsModerator: checks event.from_user.id in config.moderator_ids
    │   └── is_admin.py        # IsAdmin: checks event.from_user.id in config.admin_ids
    │
    └── utils/
        ├── media.py           # extract_media_info() — file_id/type from Message
        ├── formatting.py      # HTML formatting for previews, summaries, moderator channel text; STATUS_MAP; get_html_caption()/get_html_text() — converting entities → HTML on save
        ├── tags.py            # format_tags_line(), compose_caption(), validate_caption_length() (1024 for media, 4096 for text-only; counts plain text after stripping HTML)
        ├── diffs.py           # Caption diff rendering for notifications
        └── html_entities.py   # HTML entity encoding/decoding helpers

logs/                          # Log files (in .gitignore, created at project root)
    └── submission_bot.log

tests/
    ├── conftest.py            # Shared pytest fixtures + safe env defaults for importing src/
    ├── helpers.py             # Fake Bot/FSM/session and ORM object factories
    ├── integration/
    │   ├── conftest.py        # Async engine/session fixtures for real PostgreSQL via TEST_DATABASE_URL
    │   ├── test_db_queries.py # Integration tests for db.queries against PostgreSQL
    │   └── test_migrations.py # Checks alembic upgrade head on an empty test DB
    ├── test_utils.py          # Pure utility tests
    ├── test_middlewares.py    # Middleware tests
    ├── test_services_*.py     # Publisher / scheduler / mod-channel tests
    └── test_handlers_*.py     # Viewer / moderator / tag-wizard tests
```

---

## Middleware pipeline

Registration order (outer → inner):

1. **ThrottleMiddleware** — 20 requests / 10s per user; cuts off spam before a DB session is opened; on `CallbackQuery` shows an alert «Слишком много запросов» (too many requests)
2. **DbSessionMiddleware** — creates an AsyncSession, injects it into `data["session"]`, commits on success / rolls back on `BaseException`
3. **AuthMiddleware** — upserts the user (`INSERT ... ON CONFLICT`) → `data["db_user"]`; logs new users

Every handler gets:
- `session: AsyncSession` — SQLAlchemy session (auto-committed on success)
- `db_user: User` — ORM object for the current user

---

## Routers (registration order)

```python
dp.include_routers(
    moderator.router,    # First: moderator's /start deep link and /cancel (filtered by IsModerator)
    common.router,       # /start, /help, /cancel — for everyone else
    contact.router,      # Moderator ↔ viewer contact (before viewer, so IsModeratorReply catches replies)
    viewer.router,       # Viewer submission handlers, cancellation
)
```

**Order matters:**
- `moderator` **first** — its `/start` (moderator home screen or `review_*` deep link) and `/cancel` (clearing the preview/management message) are handled before the generic commands in `common`; non-moderator messages pass through thanks to the `IsModerator()` filter on the router
- `contact` before `viewer` — a viewer's replies to a moderator's message don't create new submissions and don't land in the viewer handler as regular text submissions

---

## Tag presets

- Runtime source of presets — the `tag_preset_sections` + `tag_presets` table pair in PostgreSQL
- An Alembic migration seeds the initial sections and values from the previous fixed schema, after which the wizard and management UI work only through the DB
- The `Управление` (Management) screen in the moderator's DM allows CRUD for the sections themselves and for the entries within each section
- In the wizard, the number of preset pages is no longer hardcoded: pages are built from `tag_preset_sections.sort_order`, always followed by a custom page
- Deleting or renaming a preset/section **does not rewrite** already-saved `submissions.tags`; values that disappear move to custom tags the next time the wizard is opened

---

## Configuration (.env)

```env
BOT__TOKEN=...             # Telegram Bot API token → config.bot.token
DB__HOST=postgres          # PostgreSQL host → config.db.host
DB__PORT=5432              # → config.db.port
DB__NAME=tg_submission_bot # → config.db.name
DB__USER=postgres          # → config.db.user
DB__PASSWORD=...           # → config.db.password
MODERATOR_IDS=123456789,987654321  # Moderator Telegram IDs (comma-separated) → config.moderator_ids
CHANNEL_ID=-100...         # Target channel ID (negative number) → config.channel_id
MODERATOR_GROUP_ID=-100...   # Moderator forum group ID → config.moderator_group_id
ADMIN_IDS=123456789          # (optional) admins; merged into config.moderator_ids automatically
TIMEZONE=Europe/Moscow     # For the calendar and scheduler → config.timezone
LOG_LEVEL=INFO             # Logging level → config.log_level
REDIS_URL=                 # (optional) Redis URL for FSM Storage; empty — MemoryStorage
PROXY_URL=                 # (optional) SOCKS5/HTTP proxy for the Telegram API
SILENT_MODERATOR_NOTIFICATIONS=true  # (optional) silent messages to the mod group and admin DMs → config.silent_moderator_notifications
DB_AUTO_MIGRATE=true       # (optional) apply migrations at startup; false — check only → config.db_auto_migrate
DB_MIGRATE_TIMEOUT=300     # (optional) migration run timeout, seconds → config.db_migrate_timeout
LOG_DIR=/logs              # (optional) log file directory; set to /logs in the image
API_HOST=0.0.0.0           # (optional) health/metrics HTTP server host → config.api_host
API_PORT=5400              # (optional) health/metrics HTTP server port → config.api_port
```

### Access from code

```python
from core.config import config

config.bot.token              # BOT_TOKEN
config.db.url                 # postgresql+asyncpg://... (for SQLAlchemy async)
config.moderator_ids              # list[int] — all moderator IDs
config.moderator_id               # int — first ID (backward compat)
config.redis_url                  # str | None — Redis URL for FSM Storage
config.proxy_url                  # str | None — proxy for the Telegram API
config.channel_id
config.moderator_group_id
config.admin_ids              # list[int] — admin IDs
config.edit_lock_ttl_seconds  # int — edit lock TTL (seconds)
config.topic_title_max_length # int — max topic title length
config.timezone
config.log_level
```

---

## Logging

`core/logging.py` — centralized setup:

- `create_logger(level)` is called once in `core/bot.py` at startup
- Two handlers: console (level from `LOG_LEVEL`) + `RotatingFileHandler` (`$LOG_DIR/submission_bot.log`, 5 MB, 5 backups). `LOG_DIR` defaults to `<project>/logs`; in the image it's set to `/logs`, where a volume is mounted — otherwise the file would be written inside the container and lost on every recreation
- Noisy libraries (`aiogram`, `aiohttp`, `apscheduler`, `sqlalchemy.engine`) are muted to WARNING
- `sys.excepthook` catches unhandled exceptions
- All modules use `get_logger(__name__)` — child loggers under the `submission_bot.*` namespace
- `fmt_user(db_user)` — formats a user for logs (username or full_name)

---

## Docker

**Bot:** `python:3.12-slim`, non-root user (`botuser`), pip install, `CMD ["python", "main.py"]`

**Redis:** `docker-compose.yml` brings up `redis:7-alpine` by default; the `bot` service waits for Redis's healthcheck and can use it via `REDIS_URL`.

**Postgres:** the `postgres` service in the same compose file (volume + healthcheck). If you already have your own database, its address goes into `DB__HOST` / `DB__PORT`, and the built-in service just isn't started (`docker compose up -d bot redis`).

**`.dockerignore`:** excludes `.venv`, `__pycache__`, `.git`, `.env`, `logs/`, `*.md`

On startup the bot: checks DB availability (`check_db_connection()`), **applies migrations** (`run_migrations()`), creates an `AiohttpSession(proxy=...)` if `PROXY_URL` is set, registers commands, checks access to the channels, calls `ensure_general_topic_nav()`, starts the scheduler and restores jobs from the DB.

### Automatic migrations

`db/session.py::run_migrations()` is called from `core/bot.py` right after `check_db_connection()`:

1. Compares `MigrationContext.get_current_heads()` with the `ScriptDirectory` heads. If they match — no-op and logs «Схема БД актуальна» (DB schema is up to date).
2. If the schema is behind → takes a session-level `pg_advisory_lock(728413002)`, **re-checks** the revisions under the lock (a parallel instance might have already applied everything) and runs `alembic upgrade head`.
3. `command.upgrade` runs inside `asyncio.to_thread` — `alembic/env.py` does its own `asyncio.run()` internally, so the calling event loop isn't blocked and `asyncio.wait_for(timeout=DB_MIGRATE_TIMEOUT)` applies.
4. Any error/timeout → `RuntimeError`, bot startup aborts (better not to come up than to run on an inconsistent schema). The lock is released in `finally`.

The Alembic config is built **in memory**, not from `alembic.ini`: `env.py` calls `fileConfig(config_file_name)`, which would reinitialize the application's logging.

Environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `DB_AUTO_MIGRATE` | `true` | `false` — check only: if the schema is behind, the bot crashes with an error without applying anything |
| `DB_MIGRATE_TIMEOUT` | `300` | Run timeout in seconds |

**Graceful shutdown:** on stop, the bot waits for in-flight media groups to finish (up to 30s), then calls `shutdown_scheduler()` (internally `scheduler.shutdown(wait=True)`) to let the scheduler's current jobs finish.

---

## Health monitoring

An aiohttp server runs alongside the bot (`services/health.py`, port `API_PORT`, default 5400):

- `GET /api/v1/health` — JSON for the docker healthcheck. 503 (`status: dead`) — Telegram API or PostgreSQL unavailable; `status: degraded` with 200 — Redis or the scheduler is down (a restart won't fix it).
- `GET /api/v1/metrics` — Prometheus text format for vmagent: `tgarts_telegram_up`, `tgarts_telegram_failures`, `tgarts_telegram_last_ok_seconds`, `tgarts_db_up`, `tgarts_redis_up` (only when `REDIS_URL` is set), `tgarts_scheduler_running`.

`TelegramWatchdog` — a background task: `bot.get_me()` every 60s (10s timeout), caches the state so metrics scrapes don't hit the Telegram API directly. Counts consecutive failures, logs on recovery. The container doesn't restart itself — alerting is external (Grafana).

Port 5400 is only listened to inside the docker network; no need to publish it externally — the healthcheck runs from within the container itself, and the scraper (vmagent/Prometheus) reaches it by container name. Multiple instances from the same image don't conflict — each has its own network/name. Started/stopped in `core/bot.py` (`watchdog.start()` + `start_health_server()` before polling; `watchdog.stop()` + `runner.cleanup()` in `finally`).

---

## DB connection pool

```python
create_async_engine(
    config.db.url, pool_size=10, max_overflow=5, pool_recycle=600, pool_pre_ping=True,
    connect_args={"command_timeout": 30.0, "server_settings": {
        "statement_timeout": "30000", "idle_in_transaction_session_timeout": "60000"}},
)
```

- `pool_recycle=600` — reconnects every 10 minutes to avoid PostgreSQL idle-timeout issues
- `pool_pre_ping=True` — checks the connection before handing it out from the pool
- `command_timeout=30` — asyncpg stops waiting for a response. Without it, a dropped connection leaves the coroutine hanging forever: PostgreSQL sees the session as `idle in transaction`, and its row lock blocks every writer behind it
- `statement_timeout` / `idle_in_transaction_session_timeout` — the same safety net from the server side: a hung transaction dies after a minute and releases its locks
- Alembic migrations run on a **separate engine** without these timeouts (`NullPool`) — a legitimate table rewrite can take longer, and aborting it mid-way is worse than a slow startup. The same applies to waiting on the advisory lock: on the main engine, `statement_timeout` would kill `pg_advisory_lock` after 30s

**Transaction boundary invariant:** external I/O (Telegram API, `asyncio.sleep` in a retry) is **never performed inside an open transaction** that has already written something. The pattern is claim → call → record, each DB phase separate (`process_next_topic_title_sync`, `reconcile_submission_cards`). This rule also applies to handlers: `cmd_start_review`, `handle_close`, `_cancel_viewing_post`, `handle_media_done` commit the lock/status write **before** updating the card in Telegram; `cleanup_edit_locks_job` first commits the deletion of expired locks and only then talks to Telegram for each one — in its own short session; `_render_queue_inner` / `_render_schedule_inner` close the read-only transaction before the first `editMessageText`. Breaking this rule once froze the title worker for two days: a network failure hung a coroutine, the transaction stayed open, and the `INSERT` for new users queued up behind its lock

---

## Dependencies

```
aiogram>=3.7.0,<3.8
sqlalchemy[asyncio]>=2.0,<2.1
asyncpg>=0.29.0,<0.31
alembic>=1.13.0,<1.15
apscheduler>=3.10.0,<4.0
pydantic-settings>=2.0,<3.0
python-dotenv>=1.0,<2.0
aiohttp>=3.9.0,<4.0
aiohttp-socks>=0.8.0,<0.10
redis[asyncio]>=5.0.0,<6.0          # optional, for FSM RedisStorage
```

### Dev / test dependencies

```
pytest>=8.3.0,<9.0
pytest-asyncio>=0.24.0,<1.0
pytest-cov>=5.0.0,<7.0
```

---

## Testing

- The main `pytest` suite is split into fast unit/service tests and PostgreSQL integration tests in `tests/integration/`
- Integration tests require `TEST_DATABASE_URL`; if the variable isn't set, they're automatically `skip`ped, so a local `pytest` run stays safe
- `tests/integration/test_db_queries.py` checks real `db.queries` against PostgreSQL, including `INSERT ... ON CONFLICT`, cascading deletes, loading relationships, and CRUD for publications/tags
- `tests/integration/test_migrations.py` runs `python -m alembic upgrade head` on an empty test DB and validates that the key tables get created
---

## Running

1. Copy `.env.example` → `.env`, fill in real values
2. Apply migrations: `alembic upgrade head`
3. `docker-compose up --build`

The bot automatically: checks the DB connection → creates a Telegram session via proxy if needed → fetches the bot username (`get_me()`) → registers commands (`set_my_commands()`) → **checks access to the channels** (channel_id, moderator_group_id) → pins the navigation message in the forum's General topic (`ensure_general_topic_nav()`) → starts the scheduler → restores unfinished publications → starts polling.

---

## Media storage

Files are **not downloaded**. Only the Telegram `file_id` is stored — a stable identifier within a given bot. When publishing, the file is sent by `file_id` through Telegram's CDN (instant, no server-side traffic).
