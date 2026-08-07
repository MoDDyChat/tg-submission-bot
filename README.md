# tg-submission-bot — a submission bot for a Telegram channel

**English** · [Русский](README.ru.md)

A Telegram bot that collects creative content (art, animations, video) from viewers,
moderates it in a dedicated forum group and publishes it to a channel on a schedule.

---

## Features

**Viewer** (any user):
- `/start` — receives the submission rules (the text is configurable, see `RULES_PATH`)
- sends photos / videos / GIFs / documents / text, including albums
- sees a "Post #N accepted" confirmation and can cancel their own submission
- gets a notification when the post is published or rejected

**Moderator** (the role lives in the database; `MODERATOR_IDS` only seeds it at startup):
- every viewer gets **their own topic** in the private forum group; media and a post card with its status appear there
- post preview, caption editing, adding/removing media
- tag system driven by a wizard with presets (sections + items, editable right inside the bot)
- publish immediately or on a schedule, unschedule, queue board in the General topic
- reject a post (with a reason or silently), ban the author, contact the author directly

**Administrator** (`ADMIN_IDS` seeds the role the same way):
- everything a moderator can do — an admin is a moderator with extra rights, so there is
  no need to repeat the ID in `MODERATOR_IDS`
- "Moderators" section — appoint and demote moderators right inside the bot, no `.env`
  edit and no restart (see [Managing moderators](#managing-moderators))
- "Recover posts" button — restores lost cards in the topics
- DM notifications about preset edits, bans and every role change

In short: moderators review and publish submissions, admins additionally manage the roster,
get the recovery tool and are told when someone edits the tag presets or bans a user. If you are
running the bot alone, just put your own ID into `ADMIN_IDS` and leave `MODERATOR_IDS` empty.

Details: [`docs/pipelines.md`](docs/pipelines.md), [`docs/roles.md`](docs/roles.md),
[`docs/publishing.md`](docs/publishing.md), [`docs/tags-system.md`](docs/tags-system.md).

---

## Requirements

- A server with Docker and Docker Compose — PostgreSQL and Redis are already part of `docker-compose.yml`
- A Telegram account and the ability to create a channel and a group

The order is: [Step 1](#step-1-telegram-setup) — prepare the bot, the channel and the group
in Telegram; [Step 2](#step-2-installation) — clone the repository and fill in `.env`;
[Step 3](#step-3-running) — start it. Takes 15–20 minutes.

---

## Step 1. Telegram setup

All of this is done once, by hand, before the first launch.

### 1.1. Create the bot

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → set a name and a username.
2. Save the token that looks like `1234567890:AAH...` — this is `BOT__TOKEN`.
3. In the same place: `/mybots` → your bot → **Bot Settings → Group Privacy → Turn off**.
   Without this the bot will not see some of the events in the moderation group.

### 1.2. Create the publishing channel

1. Create a channel (public or private) — this is where the bot will post.
2. Add the bot as an **administrator** with the "Post messages" permission.

### 1.3. Create the moderation group

1. Create a **private supergroup**.
2. Enable **Topics (Forum)** in the group settings — this is mandatory,
   the bot creates one topic per viewer.
3. Add the bot as an administrator with these permissions: "Manage topics", "Send messages",
   "Edit messages", "Delete messages", "Pin messages".
4. Add all the moderators to the group.

### 1.4. Find the channel and group IDs

The IDs look like `-1001234567890` (always with a minus sign and the `-100` prefix).

The easiest way: forward any message from the channel/group to
[@userinfobot](https://t.me/userinfobot) or [@getidsbot](https://t.me/getidsbot) —
it will show `Forwarded from chat: -100...`.

The same bot will show your own personal ID (for `MODERATOR_IDS`) if you just send it `/start`.

> Moderators must send the bot a `/start` in a private chat at least once — otherwise the bot
> will not be able to send them notifications.

---

## Step 2. Installation

```bash
git clone https://github.com/<your-account>/tg-submission-bot.git
cd tg-submission-bot
cp .env.example .env
```

Open `.env` and fill it in (the full list of variables is below):

```env
BOT__TOKEN=1234567890:AAH...

# Pick any password — the PostgreSQL container from compose will come up with it.
DB__HOST=postgres
DB__PORT=5432
DB__NAME=tg_submission_bot
DB__USER=postgres
DB__PASSWORD=<your password>

# Bootstrap lists, not the full roster: the IDs below are granted their role at
# startup, after that the roster is managed inside the bot. For several IDs,
# list them comma-separated on one line:
#   MODERATOR_IDS=123456789,987654321,555444333
MODERATOR_IDS=123456789
# Admins get moderator rights automatically - no need to repeat them above
ADMIN_IDS=987654321

CHANNEL_ID=-1001111111111
MODERATOR_GROUP_ID=-1002222222222

TIMEZONE=Europe/Moscow
REDIS_URL=redis://redis:6379/0
RULES_PATH=config/rules.txt
```

### Rules text

`/start` shows a viewer the contents of the file at `RULES_PATH` (Telegram HTML markup:
`<b>`, `<i>`, `<a href="...">`). Copy the example and edit it for your own channel:

```bash
cp config/rules.example.txt config/rules.txt
```

If the file is missing, the built-in text from `src/core/messages.py` is used.

### All other bot texts

Every other user-facing string (buttons, prompts, notifications) lives in `config/messages.yaml`
(path configurable via `MESSAGES_PATH`). Edit any value — the file is validated at startup: if an
edit drops or renames a `{placeholder}` the message actually needs, the bot refuses to start with
a clear error instead of crashing mid-conversation. Keys with no file entry fall back to the
built-in defaults in `src/core/messages.py`.

---

## Step 3. Running

```bash
docker compose up -d --build
```

One command brings up three containers: the bot, PostgreSQL and Redis. The bot creates
the database tables itself on first start — you do not need to run anything for that.

Check that everything came up:

```bash
docker compose logs -f bot
```

On a successful start the logs show: database connection → `get_me()` → command registration →
access checks for the channel and the group → creation of the navigation message in the General topic →
scheduler start → polling start.

If the bot has no access to the channel or the group, it will say so explicitly and refuse to start.

That's it, the bot is running. Now send it `/start` from a moderator account.

---

## Managing moderators

**The database is the source of truth for roles.** `MODERATOR_IDS` / `ADMIN_IDS` are only a
bootstrap and break-glass list: on every start the bot grants the listed IDs their role
(creating a placeholder user row if the person has never written to the bot) and **never takes
a role away**. Removing an ID from the list and restarting does *not* demote anyone.

Everything else happens inside the bot: `/start` → **Management → Moderators** (the section is
visible to admins only). From there you can:

- **Create an invite link** — a one-shot `https://t.me/<bot>?start=modinvite_<token>` link,
  valid for 24 hours. Whoever opens it first becomes a moderator; the link is burned on use,
  so it cannot be reused even if it gets forwarded. Expired links are cleaned up daily.
- **Add by Telegram ID** — enter the ID directly, no invite round-trip.
- **Demote a moderator** — clears both roles, cancels their unused invite links and releases
  any post-editing locks they were holding, so nothing stays stuck.
- **Grant / revoke admin** — an admin keeps the moderator role when admin rights are revoked.

Guardrails (all enforced in the same transaction as the change):

- nobody can change their own role;
- the last remaining admin cannot be demoted;
- a banned user cannot be given a role, and a user holding a role cannot be banned —
  demote them first;
- a user listed in `MODERATOR_IDS` / `ADMIN_IDS` is **config-protected** and cannot be demoted
  through the UI (the next restart would hand the role straight back). To remove such a person,
  take their ID out of `.env` first.

Every role change is DM'd to all admins as an audit line, and the affected user is notified too.

Details: [`docs/roles.md`](docs/roles.md).

---

## Running without Docker

You will need your own PostgreSQL 14+ and, optionally, Redis.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# in .env: DB__HOST=localhost, REDIS_URL=redis://localhost:6379/0
python main.py
```

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `BOT__TOKEN` | yes | — | Token from @BotFather |
| `DB__HOST` | yes | `postgres` | PostgreSQL host |
| `DB__PORT` | no | `5432` | PostgreSQL port |
| `DB__NAME` | no | `tg_submission_bot` | Database name |
| `DB__USER` | no | `postgres` | Database user |
| `DB__PASSWORD` | yes | — | Database password |
| `MODERATOR_IDS` | yes* | — | Bootstrap moderator IDs, comma-separated: `123456789,987654321` |
| `ADMIN_IDS` | no | empty | Bootstrap admin IDs; they get moderator rights automatically |
| `CHANNEL_ID` | yes | — | ID of the publishing channel (`-100...`) |
| `MODERATOR_GROUP_ID` | yes | — | ID of the moderation forum group (`-100...`) |
| `TIMEZONE` | no | `Europe/Moscow` | Timezone used for the publishing schedule |
| `LOG_LEVEL` | no | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `RULES_PATH` | no | `config/rules.txt` | File with the rules text (HTML) |
| `REDIS_URL` | no | empty | Set → persistent FSM; unset → in-memory states |
| `EDIT_LOCK_TTL_SECONDS` | no | `300` | TTL of the post editing lock |
| `PROXY_URL` | no | empty | SOCKS5/HTTP proxy to the Telegram API |
| `SILENT_MODERATOR_NOTIFICATIONS` | no | `true` | Moderation group and admin DMs — no push notifications |
| `TAG_PARSING_MODE` | no | `suggest` | Hashtags in the caption: `off` — ignore; `suggest` — offer them to the moderator; `auto` — also apply exact preset matches |
| `TAG_PARSING_STRIP_FROM_CAPTION` | no | `false` | Cut lines made only of hashtags from the head and tail of the caption |
| `API_HOST` / `API_PORT` | no | `0.0.0.0` / `5400` | HTTP server for health/metrics |
| `LOG_DIR` | no | `/logs` in the image | Directory for the log file; outside Docker, `<project>/logs` |

\* At least one of `MODERATOR_IDS` / `ADMIN_IDS` must be filled in; either one on its own is fine.
Both are **bootstrap lists**, not the live roster — see [Managing moderators](#managing-moderators).

The double underscore in `BOT__TOKEN` and `DB__*` is not a typo: that is how pydantic-settings
maps variables onto the nested sections of the config.

---

## Operations

**Upgrading to a new version:**

```bash
git pull
docker compose up -d --build
```

The database schema updates itself when the container starts.

**Your own infrastructure tweaks** (log paths, external networks, an existing PostgreSQL
server instead of the container, a second bot instance) go into `docker-compose.override.yml` —
Compose picks it up automatically, and the file is not committed to git.

**Logs:** written to `/logs` inside the container (mount your own volume in `docker-compose.yml`)
and to stdout — `docker compose logs`.

**Health and metrics:**

- `GET http://<host>:5400/api/v1/health` — JSON for the healthcheck. `503` = the Telegram API
  or PostgreSQL is unavailable; `degraded` = Redis or the scheduler is down
- `GET http://<host>:5400/api/v1/metrics` — Prometheus format (`tgarts_*`)

**Backups:** all data lives in PostgreSQL only. Media files are not downloaded, only the
Telegram `file_id` is stored, so `pg_dump` is enough.

> ⚠️ A `file_id` is only valid for **the bot** that received it. If you change the bot
> token, old posts will stop being publishable.

---

## Customizing for your own channel

| What | Where |
|---|---|
| Rules text for viewers (`/start`) | the file at `RULES_PATH`, e.g. `config/rules.txt` |
| Topic status names and icons | `config/topic_statuses.json` |
| Tag presets | right inside the bot: `/start` → "Management" → "Tag presets" |
| All other bot texts | `config/messages.yaml`, path configurable via `MESSAGES_PATH` |

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `Telegram ID не может быть равен 0` ("Telegram ID cannot be 0") on startup | `CHANNEL_ID` / `MODERATOR_GROUP_ID` are not filled in |
| `channel_id и moderator_group_id должны быть отрицательными` ("must be negative") | an ID was given without the `-100` prefix |
| `Нужен хотя бы один ID в MODERATOR_IDS или ADMIN_IDS` ("at least one ID is required") | both lists are empty |
| The bot does not create topics | Topics are not enabled in the group, or the bot lacks the "Manage topics" permission |
| A moderator receives no DM notifications | they never pressed `/start` in the bot |
| An ID was removed from `MODERATOR_IDS`, but the person still moderates | roles live in the DB; demote them via Management → Moderators |
| "Moderators" is missing from the Management menu | the account is a moderator, not an admin |
| No tables / SQL errors on startup | auto-migrations are disabled (`DB_AUTO_MIGRATE=false`) |
| FSM state is lost after a restart | `REDIS_URL` is not set |

---

## Development

```bash
pip install -r requirements-dev.txt
pytest                 # fast tests
ruff check . && mypy src
```

Integration tests require a real PostgreSQL and the `TEST_DATABASE_URL` variable;
without it they are skipped automatically. See [`docs/testing.md`](docs/testing.md) for details.

Code documentation: [`docs/architecture.md`](docs/architecture.md),
[`docs/db-schema.md`](docs/db-schema.md).
