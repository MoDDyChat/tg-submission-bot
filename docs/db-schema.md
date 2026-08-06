# Схема базы данных tg-submission-bot

## Таблицы

`users`, `submissions`, `submission_media`, `publications`, `user_topics`, `edit_locks`, `tag_preset_sections`, `tag_presets`, `messages`, `system_messages`

---

### users
| Поле | Тип | Описание |
|------|-----|----------|
| id | BIGSERIAL PK | Внутренний ID |
| telegram_id | BIGINT UNIQUE NOT NULL | Telegram user ID |
| username | VARCHAR(255) | @username (nullable) |
| full_name | VARCHAR(512) NOT NULL | Имя пользователя |
| is_moderator | BOOLEAN NOT NULL DEFAULT false | Флаг модератора |
| is_admin | BOOLEAN NOT NULL DEFAULT false | Флаг администратора (подмножество модераторов) |
| is_banned | BOOLEAN NOT NULL DEFAULT false | Забанен ли пользователь |
| ban_reason | TEXT | Причина бана (nullable) |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | Обновляется автоматически через onupdate |

**Upsert:** `INSERT ... ON CONFLICT(telegram_id) DO UPDATE` — обновляет username/full_name при каждом обращении, устраняет race condition.

### submissions
| Поле | Тип | Описание |
|------|-----|----------|
| id | BIGSERIAL PK | |
| user_id | BIGINT NOT NULL FK → users.id ON DELETE CASCADE | Автор |
| caption | TEXT | HTML-описание зрителя в исходном виде (хештеги не удаляются); форматирование Telegram (жирный, курсив, ссылки) сохраняется через `html_decoration.unparse()` |
| tags | JSON NOT NULL DEFAULT '[]' | Массив тегов без `#` (например `["MineShieldArt", "Nerkin"]`) |
| status | VARCHAR(32) NOT NULL DEFAULT 'pending' | pending → scheduled → published / cancelled / rejected |
| topic_card_message_id | BIGINT | message_id текстовой карточки в теме форума (nullable) |
| topic_media_message_ids | JSON | Список message_id медиа в теме форума (nullable) |
| card_rendered_hash | VARCHAR(64) | sha256 payload карточки, подтверждённого Telegram; пишется только после успешной отправки/правки. NULL = никогда не подтверждалась → reconcile считает дрейфом |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

**Индексы:** idx_submissions_status, idx_submissions_user_id

### user_topics
| Поле | Тип | Описание |
|------|-----|----------|
| user_id | BIGINT PK FK → users.id ON DELETE CASCADE | Пользователь |
| topic_id | BIGINT NOT NULL UNIQUE | message_thread_id форум-топика в группе модератора |
| current_status_key | VARCHAR(32) NOT NULL DEFAULT 'pending' | Текущий статус-ключ для заголовка (pending/editing/scheduled/published/...) |
| title_sync_version | BIGINT NOT NULL DEFAULT 0 | Последняя revision заголовка, запрошенная доменной транзакцией |
| title_applied_version | BIGINT NOT NULL DEFAULT 0 | Последняя revision, успешно применённая в Telegram |
| title_force_sync_version | BIGINT NOT NULL DEFAULT 0 | Revision, которую нужно отправить даже при совпадении вычисленного статуса с `current_status_key` |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

**Outbox-инвариант:** строка требует обработки, пока `title_sync_version > title_applied_version`. Воркер фиксирует именно захваченную revision, поэтому более новое изменение, пришедшее во время Telegram-запроса, остаётся ожидающим. Частичный индекс `idx_user_topics_title_sync_pending` по `updated_at` покрывает только такие строки.

### edit_locks
| Поле | Тип | Описание |
|------|-----|----------|
| resource_type | VARCHAR(32) PK | Тип ресурса (submission, management) |
| resource_id | VARCHAR(64) PK | ID ресурса (sub id, presets, banned) |
| moderator_id | BIGINT NOT NULL | Кто держит блокировку |
| acquired_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | Время захвата |
| expires_at | TIMESTAMPTZ NOT NULL | Время истечения (TTL) |

**Composite PK:** (`resource_type`, `resource_id`)

### submission_media
| Поле | Тип | Описание |
|------|-----|----------|
| id | BIGSERIAL PK | |
| submission_id | BIGINT NOT NULL FK → submissions.id ON DELETE CASCADE | |
| file_id | VARCHAR(512) NOT NULL | Telegram file_id (не скачиваем файлы) |
| file_unique_id | VARCHAR(256) NOT NULL | Telegram file_unique_id |
| media_type | VARCHAR(32) NOT NULL | photo / video / animation / document |
| sort_order | INT NOT NULL DEFAULT 0 | Порядок в альбоме |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

### publications
| Поле | Тип | Описание |
|------|-----|----------|
| id | BIGSERIAL PK | |
| submission_id | BIGINT UNIQUE NOT NULL FK → submissions.id ON DELETE CASCADE | |
| edited_caption | TEXT | Отредактированный модератором текст |
| publish_at | TIMESTAMPTZ NOT NULL | Запланированное время публикации (хранится в UTC) |
| published_at | TIMESTAMPTZ | Фактическое время (NULL до публикации) |
| channel_message_id | BIGINT | ID первого сообщения в канале после публикации |
| channel_message_ids | JSON | Все ID сообщений в канале (для медиагрупп) |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

**Частичный индекс:** idx_publications_publish_at WHERE published_at IS NULL

### tag_preset_sections
| Поле | Тип | Описание |
|------|-----|----------|
| key | VARCHAR(64) PK | Внутренний ключ раздела (`category`, `setting`, `character`, `section_N`, ...) |
| label | VARCHAR(255) UNIQUE NOT NULL | Название раздела в wizard / management UI |
| columns | INT NOT NULL DEFAULT 3 | Сколько кнопок показывать в ряд на странице wizard |
| sort_order | INT NOT NULL DEFAULT 0 | Порядок страниц в wizard и списке разделов |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

**Индекс:** idx_tag_preset_sections_sort (`sort_order`).

**Race safety:** `create_tag_preset_section` использует `pg_advisory_xact_lock(hashtext('tag_preset_section_order'))` для сериализации MAX(sort_order)+INSERT.

### tag_presets
| Поле | Тип | Описание |
|------|-----|----------|
| id | BIGSERIAL PK | |
| preset_type | VARCHAR(64) NOT NULL FK → tag_preset_sections.key ON DELETE CASCADE | Ключ раздела, к которому относится пресет |
| label | VARCHAR(255) NOT NULL | Отображаемое имя в inline-кнопке |
| tag | VARCHAR(255) NOT NULL | Значение тега без `#`, которое попадает в `submissions.tags` |
| sort_order | INT NOT NULL DEFAULT 0 | Порядок вывода в wizard и management UI |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

**Ограничения:** уникальность по (`preset_type`, `label`) и (`preset_type`, `tag`).

**Поведение:** удаление записи из `tag_preset_sections` каскадно удаляет её пресеты из `tag_presets`, но уже сохранённые значения в `submissions.tags` остаются неизменными.

**Race safety:** `create_tag_preset` использует `pg_advisory_xact_lock(hashtext(preset_type))` для сериализации MAX(sort_order)+INSERT внутри одного раздела.

**Индекс:** idx_tag_presets_type_sort (`preset_type`, `sort_order`).

### messages
| Поле | Тип | Описание |
|------|-----|----------|
| id | BIGSERIAL PK | |
| submission_id | BIGINT NOT NULL FK → submissions.id ON DELETE CASCADE | Привязка к посту |
| sender_telegram_id | BIGINT NOT NULL | Кто отправил |
| text | TEXT NOT NULL | HTML-текст сообщения |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

### system_messages
| Поле | Тип | Описание |
|------|-----|----------|
| key | VARCHAR(64) PK | Уникальный ключ сообщения |
| chat_id | BIGINT NOT NULL | ID чата (форум-группы) |
| message_id | BIGINT NOT NULL | ID сообщения в чате |
| payload | JSON | Дополнительные данные (например, MD5-чексумма для идемпотентности) |
| updated_at | TIMESTAMPTZ NOT NULL DEFAULT NOW() | Обновляется при каждом upsert |

**Ключи, используемые системой:**

| key | Назначение |
|-----|-----------|
| `general:legend` | Сообщение-легенда статусов в General-топике форума |
| `general:queue:00`, `general:queue:01`, ... | Чанки очереди-борда в General-топике; число чанков динамическое |

**Логика:** `get_system_message(session, key)` → запись или `None`; `upsert_system_message(session, key, chat_id, message_id, payload)` → INSERT ON CONFLICT UPDATE.

---

## Пакет `db.queries`

Функции разбиты по модулям; все они re-экспортируются из `db.queries` для обратной совместимости:

| Модуль | Функции |
|--------|---------|
| `db.queries.users` | `get_or_create_user`, `ban_user`, `unban_user`, `get_banned_users`, `get_admin_users`, `get_user_by_id` |
| `db.queries.submissions` | `create_submission`, `get_submission`, `get_submission_with_user`, `list_pending_submissions`, `get_active_submissions`, `count_pending_submissions`, `update_submission_status`, `update_submission_tags`, `update_submission_caption`, `get_submission_by_topic_card_id` |
| `db.queries.submission_media` | `add_media`, `get_submission_media`, `delete_media` |
| `db.queries.publications` | `create_publication`, `get_publication`, `get_publication_by_submission`, `get_unpublished_publications`, `mark_published`, `update_publication_time`, `delete_publication` |
| `db.queries.tag_presets` | `list_tag_preset_sections`, `get_tag_preset_section`, `get_tag_preset_section_by_label`, `create_tag_preset_section`, `update_tag_preset_section`, `delete_tag_preset_section`, `list_tag_presets`, `list_tag_presets_grouped`, `get_tag_preset`, `find_tag_preset_conflicts`, `create_tag_preset`, `update_tag_preset`, `delete_tag_preset` |
| `db.queries.messages` | `create_message` |
| `db.queries.topics` | CRUD `user_topics`/card IDs; `enqueue_topic_title_sync`, `ensure_topic_title_sync_pending`, `mark_topic_title_sync_applied`, `mark_topic_title_externally_drifted` для outbox заголовков |
| `db.queries.system_messages` | `get_system_message`, `upsert_system_message`, `delete_system_message`, `list_system_messages_by_prefix` |
