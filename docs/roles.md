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
| **Managing moderator roster** (add / demote, grant / revoke admin) | — | — | ✓ |
| **Recover posts** (card recovery) | — | — | ✓ |
| Receive DM notifications for preset/section CRUD | — | — | ✓ |
| Receive DM notifications for user ban/unban | — | — | ✓ |

---

## Roles and the source of truth

There are two runtime roles: **MODERATOR** and **ADMIN**. ADMIN is a strict superset of MODERATOR — an admin is always a moderator too (`is_moderator=True` is set whenever `is_admin=True` is granted).

Both roles live on the `users` table (`users.is_moderator`, `users.is_admin`). **The DB is the single source of truth at runtime** — every check (filters, the Recover button, the roster UI guard) reads the flags from the `db_user` row, never from the config lists. Config values only seed the DB (see below).

---

## How roles are assigned

### Bootstrap via environment (additive, break-glass)

`MODERATOR_IDS` and `ADMIN_IDS` are **bootstrap lists, not the full roster**. At startup `bootstrap_roles()` (in `core/bot.py`) upserts a `users` row for every configured Telegram ID, setting `is_moderator=True` (admins also get `is_admin=True`), creating placeholder rows for people who have never written to the bot.

The bootstrap is strictly **additive**: it never clears flags. Removing an ID from `MODERATOR_IDS` / `ADMIN_IDS` and restarting does **not** demote that person — their DB flags stay set. At least one of the two variables must be non-empty, otherwise the config is rejected on startup. Admin IDs are merged into the config's moderator list by a model validator, so an admin does not need to be repeated in `MODERATOR_IDS`.

### Config-protected moderators

A moderator is **config-protected** when their Telegram ID is present in `MODERATOR_IDS` (or `ADMIN_IDS`) at runtime. The roster service refuses to demote or revoke admin from such a user (`ConfigProtectedRoleError`) — the env lists act as a break-glass guarantee that the person who operates the server can never be locked out. To demote a config-protected user, remove their ID from the env list and clear the flag in the DB manually (no code path does it for you).

### Adding a moderator

There are two supported paths, both in the roster UI (`handlers/moderator/moderators.py`, ADMIN-only, guarded by the `("management", "moderators")` edit lock):

- **Invite link** — `services/moderator_invites.py` creates a one-shot invite (default TTL 24h) and renders a deep link `https://t.me/<bot_username>?start=modinvite_<token>`. Opening the link sends `/start modinvite_<token>`; `redeem_invite()` atomically marks the invite used and grants `is_moderator=True` in the same transaction. Expired invites are cleaned up by a periodic job; unused invites are burned when the issuer is demoted.
- **By Telegram ID** — the admin enters the user's ID directly; the user is granted the MODERATOR role without any invite round-trip.

### Demoting a moderator

`remove_moderator()` in `services/roles.py` clears both `is_moderator` and `is_admin`, burns the target's unused invites, and force-releases all their edit locks (see `docs/edit-locks.md`). The affected submission cards are repainted afterwards. Admin rights can be revoked separately (`revoke_admin()`) — the target stays a moderator. Guardrails:

- Nobody can change their own role (`CannotChangeOwnRoleError`)
- The last remaining admin cannot be revoked (`CannotRemoveLastAdminError`)
- Banned users cannot be granted roles (`RoleTargetBannedError`)
- Config-protected users cannot be demoted (see above)

Every role change is committed by the caller, then DMs all admins and best-effort DMs the target (`notify_role_change()`).

---

## Filters in code

| Filter | File | Logic |
|--------|------|--------|
| `IsModerator` | `filters/is_moderator.py` | `db_user.is_moderator == True` |
| `IsAdmin` | `filters/is_admin.py` | `db_user.is_admin == True` |

Both filters receive the `db_user` row injected by `AuthMiddleware` (`middlewares/auth.py`) — the flags come from the DB, never from the config lists. The moderator router (`handlers/moderator/__init__.py`) applies `IsModerator` at the router level, so all of its handlers automatically see only moderators. `IsAdmin` is applied selectively inside individual handlers (the Recover button, the roster UI).
