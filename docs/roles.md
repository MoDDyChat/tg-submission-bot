# Roles in tg-submission-bot

## Role matrix

| Action | VIEWER | MODERATOR | ADMIN |
|----------|--------|-----------|-------|
| Submit a post | ✓ | ✓ | ✓ |
| Cancel own submission | ✓ | ✓ | ✓ |
| Reply to a moderator message | ✓ | ✓ | ✓ |
| View a submission in preview | — | ✓ | ✓ |
| Edit a post's caption / tags | — | ✓ | ✓ |
| Schedule / unschedule | — | ✓ | ✓ |
| Publish now | — | ✓ | ✓ |
| Reject a post (with reason / silently) | — | ✓ | ✓ |
| Ban the author | — | ✓ | ✓ |
| Contact the author | — | ✓ | ✓ |
| CRUD tag sections and presets | — | ✓ | ✓ |
| Unban a user | — | ✓ | ✓ |
| **Recover posts** (card recovery) | — | — | ✓ |
| Receive DM notifications for preset/section CRUD | — | — | ✓ |
| Receive DM notifications for user ban/unban | — | — | ✓ |

---

## How roles are assigned

### MODERATOR
Set via the `MODERATOR_IDS` environment variable (comma-separated):
```env
MODERATOR_IDS=123456789,987654321
```
Everyone in `ADMIN_IDS` is added to this list automatically at config load time — an admin is a moderator by definition and does not have to be repeated in `MODERATOR_IDS`. At least one of the two variables must be non-empty, otherwise the config is rejected on startup.

On every user interaction, the `AuthMiddleware` middleware upserts and syncs the `is_moderator` flag in the `users` table based on `config.moderator_ids`. Changing `MODERATOR_IDS` takes effect after a bot restart.

### ADMIN
Set via the `ADMIN_IDS` environment variable (comma-separated). Admin rights are a superset of moderator rights, so an ID listed here does not need to appear in `MODERATOR_IDS`:
```env
ADMIN_IDS=123456789
```
On bot startup, `sync_admin_flags()` (in `core/bot.py`) sets/clears `is_admin=True` in the `users` table for all records:
- Users in `ADMIN_IDS` → `is_admin=True`
- Everyone else (including those removed from `ADMIN_IDS`) → `is_admin=False`
- If `ADMIN_IDS` is empty — all `is_admin` flags are reset to `False`

If `ADMIN_IDS` is not set — no user has admin rights. The Admin role is determined at runtime by the `users.is_admin` field, not just by config.

---

## Filters in code

| Filter | File | Logic |
|--------|------|--------|
| `IsModerator` | `filters/is_moderator.py` | `event.from_user.id in config.moderator_ids` |
| `IsAdmin` | `filters/is_admin.py` | `db_user.is_admin == True` |

The moderator router (`handlers/moderator/__init__.py`) uses `IsModerator` at the router level — all of its handlers automatically see only moderators. `IsAdmin` is applied selectively inside individual handlers (e.g. the Recover button).
