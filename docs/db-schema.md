# tg-submission-bot Database Schema

## Tables

`users`, `submissions`, `submission_media`, `publications`, `user_topics`, `edit_locks`, `tag_preset_sections`, `tag_presets`, `messages`, `system_messages`

---

### users
| Field | Type | Description |
|------|-----|----------|
| id | BIGSERIAL PK | Internal ID |
| telegram_id | BIGINT UNIQUE NOT NULL | Telegram user ID |
| username | VARCHAR(255) | @username (nullable) |
| full_name | VARCHAR(512) NOT NULL | User's name |
| is_moderator | BOOLEAN NOT NULL DEFAULT false | Moderator flag |
| is_admin | BOOLEAN NOT NULL DEFAULT false | Admin flag (subset of moderators) |
| is_banned | BOOLEAN NOT NULL DEFAULT false | Whether the user is banned |
| ban_reason | TEXT | Ban reason (nullable) |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | Updated automatically via onupdate |

**Upsert:** `INSERT ... ON CONFLICT(telegram_id) DO UPDATE` — updates username/full_name on every request, eliminating the race condition.

### submissions
| Field | Type | Description |
|------|-----|----------|
| id | BIGSERIAL PK | |
| user_id | BIGINT NOT NULL FK → users.id ON DELETE CASCADE | Author |
| caption | TEXT | Viewer's HTML description as submitted (hashtags are not stripped); Telegram formatting (bold, italic, links) is preserved via `html_decoration.unparse()` |
| tags | JSON NOT NULL DEFAULT '[]' | Array of tags without `#` (e.g. `["MineShieldArt", "Nerkin"]`) |
| status | VARCHAR(32) NOT NULL DEFAULT 'pending' | pending → scheduled → published / cancelled / rejected |
| topic_card_message_id | BIGINT | message_id of the text card in the forum topic (nullable) |
| topic_media_message_ids | JSON | List of media message_ids in the forum topic (nullable) |
| card_rendered_hash | VARCHAR(64) | sha256 of the card payload confirmed by Telegram; written only after a successful send/edit. NULL = never confirmed → reconcile treats it as drift |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

**Indexes:** idx_submissions_status, idx_submissions_user_id

### user_topics
| Field | Type | Description |
|------|-----|----------|
| user_id | BIGINT PK FK → users.id ON DELETE CASCADE | User |
| topic_id | BIGINT NOT NULL UNIQUE | message_thread_id of the forum topic in the moderator group |
| current_status_key | VARCHAR(32) NOT NULL DEFAULT 'pending' | Current status key for the title (pending/editing/scheduled/published/...) |
| title_sync_version | BIGINT NOT NULL DEFAULT 0 | Latest title revision requested by a domain transaction |
| title_applied_version | BIGINT NOT NULL DEFAULT 0 | Latest revision successfully applied on Telegram |
| title_force_sync_version | BIGINT NOT NULL DEFAULT 0 | Revision that must be sent even if the computed status matches `current_status_key` |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

**Outbox invariant:** a row needs processing as long as `title_sync_version > title_applied_version`. The worker commits the exact revision it captured, so a newer change arriving during the Telegram request remains pending. The partial index `idx_user_topics_title_sync_pending` on `updated_at` covers only such rows.

### edit_locks
| Field | Type | Description |
|------|-----|----------|
| resource_type | VARCHAR(32) PK | Resource type (submission, management) |
| resource_id | VARCHAR(64) PK | Resource ID (sub id, presets, banned) |
| moderator_id | BIGINT NOT NULL | Who holds the lock |
| acquired_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | Acquisition time |
| expires_at | TIMESTAMPTZ NOT NULL | Expiration time (TTL) |

**Composite PK:** (`resource_type`, `resource_id`)

### submission_media
| Field | Type | Description |
|------|-----|----------|
| id | BIGSERIAL PK | |
| submission_id | BIGINT NOT NULL FK → submissions.id ON DELETE CASCADE | |
| file_id | VARCHAR(512) NOT NULL | Telegram file_id (files are never downloaded) |
| file_unique_id | VARCHAR(256) NOT NULL | Telegram file_unique_id |
| media_type | VARCHAR(32) NOT NULL | photo / video / animation / document |
| sort_order | INT NOT NULL DEFAULT 0 | Order within the album |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

### publications
| Field | Type | Description |
|------|-----|----------|
| id | BIGSERIAL PK | |
| submission_id | BIGINT UNIQUE NOT NULL FK → submissions.id ON DELETE CASCADE | |
| edited_caption | TEXT | Text edited by the moderator |
| publish_at | TIMESTAMPTZ NOT NULL | Scheduled publish time (stored in UTC) |
| published_at | TIMESTAMPTZ | Actual publish time (NULL until published) |
| channel_message_id | BIGINT | ID of the first message in the channel after publishing |
| channel_message_ids | JSON | All channel message IDs (for media groups) |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

**Partial index:** idx_publications_publish_at WHERE published_at IS NULL

### tag_preset_sections
| Field | Type | Description |
|------|-----|----------|
| key | VARCHAR(64) PK | Internal section key (`category`, `setting`, `character`, `section_N`, ...) |
| label | VARCHAR(255) UNIQUE NOT NULL | Section name shown in the wizard / management UI |
| columns | INT NOT NULL DEFAULT 3 | Number of buttons to show per row on the wizard page |
| sort_order | INT NOT NULL DEFAULT 0 | Order of pages in the wizard and of the section list |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

**Index:** idx_tag_preset_sections_sort (`sort_order`).

**Race safety:** `create_tag_preset_section` uses `pg_advisory_xact_lock(hashtext('tag_preset_section_order'))` to serialize MAX(sort_order)+INSERT.

### tag_presets
| Field | Type | Description |
|------|-----|----------|
| id | BIGSERIAL PK | |
| preset_type | VARCHAR(64) NOT NULL FK → tag_preset_sections.key ON DELETE CASCADE | Key of the section the preset belongs to |
| label | VARCHAR(255) NOT NULL | Display name on the inline button |
| tag | VARCHAR(255) NOT NULL | Tag value without `#` that ends up in `submissions.tags` |
| sort_order | INT NOT NULL DEFAULT 0 | Display order in the wizard and management UI |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

**Constraints:** uniqueness on (`preset_type`, `label`) and (`preset_type`, `tag`).

**Behavior:** deleting a record from `tag_preset_sections` cascades deletion of its presets from `tag_presets`, but values already saved in `submissions.tags` remain unchanged.

**Race safety:** `create_tag_preset` uses `pg_advisory_xact_lock(hashtext(preset_type))` to serialize MAX(sort_order)+INSERT within a single section.

**Index:** idx_tag_presets_type_sort (`preset_type`, `sort_order`).

### messages
| Field | Type | Description |
|------|-----|----------|
| id | BIGSERIAL PK | |
| submission_id | BIGINT NOT NULL FK → submissions.id ON DELETE CASCADE | Linked post |
| sender_telegram_id | BIGINT NOT NULL | Who sent it |
| text | TEXT NOT NULL | HTML message text |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

### system_messages
| Field | Type | Description |
|------|-----|----------|
| key | VARCHAR(64) PK | Unique message key |
| chat_id | BIGINT NOT NULL | Chat ID (forum group) |
| message_id | BIGINT NOT NULL | Message ID in the chat |
| payload | JSON | Extra data (e.g. an MD5 checksum for idempotency) |
| updated_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | Updated on every upsert |

**Keys used by the system:**

| key | Purpose |
|-----|-----------|
| `general:legend` | Status-legend message in the forum's General topic |
| `general:queue:00`, `general:queue:01`, ... | Queue board chunks in the General topic; the number of chunks is dynamic |

**Logic:** `get_system_message(session, key)` → record or `None`; `upsert_system_message(session, key, chat_id, message_id, payload)` → INSERT ON CONFLICT UPDATE.

---

## Package `db.queries`

Functions are split across modules; all of them are re-exported from `db.queries` for backward compatibility:

| Module | Functions |
|--------|--------|
| `db.queries.users` | `get_or_create_user`, `ban_user`, `unban_user`, `get_banned_users`, `get_admin_users`, `get_user_by_id` |
| `db.queries.submissions` | `create_submission`, `get_submission`, `get_submission_with_user`, `list_pending_submissions`, `get_active_submissions`, `count_pending_submissions`, `update_submission_status`, `update_submission_tags`, `update_submission_caption`, `get_submission_by_topic_card_id` |
| `db.queries.submission_media` | `add_media`, `get_submission_media`, `delete_media` |
| `db.queries.publications` | `create_publication`, `get_publication`, `get_publication_by_submission`, `get_unpublished_publications`, `mark_published`, `update_publication_time`, `delete_publication` |
| `db.queries.tag_presets` | `list_tag_preset_sections`, `get_tag_preset_section`, `get_tag_preset_section_by_label`, `create_tag_preset_section`, `update_tag_preset_section`, `delete_tag_preset_section`, `list_tag_presets`, `list_tag_presets_grouped`, `get_tag_preset`, `find_tag_preset_conflicts`, `create_tag_preset`, `update_tag_preset`, `delete_tag_preset` |
| `db.queries.messages` | `create_message` |
| `db.queries.topics` | CRUD for `user_topics`/card IDs; `enqueue_topic_title_sync`, `ensure_topic_title_sync_pending`, `mark_topic_title_sync_applied`, `mark_topic_title_externally_drifted` for the title outbox |
| `db.queries.system_messages` | `get_system_message`, `upsert_system_message`, `delete_system_message`, `list_system_messages_by_prefix` |
