"""All user-facing text constants for the submission bot.

IMPORTANT: Callers are responsible for ``html.escape``-ing user-controlled arguments
when these templates are rendered with ``parse_mode="HTML"``.  Do NOT pass raw
user input (names, captions, reasons) directly into format strings without escaping.
"""

# ── Common ─────────────────────────────────────────────────────────

# Fallback shown when the file at ``RULES_PATH`` is missing. Deliberately
# channel-agnostic: every instance is expected to ship its own text (see
# ``config/rules.example.txt``).
RULES = (
    "Привет! Я бот-предложка творчества.\n"
    "Вы можете поделиться своим творчеством через меня, и в случае прохождения "
    "модерации ваш пост будет опубликован в канале.\n\n"
    "<b>Как предложить пост:</b>\n"
    "• Отправь мне изображение, видео или текст.\n"
    "• Можно прикреплять сразу несколько файлов за раз.\n"
    "• Обязательно укажи ссылку на себя (тгк, группа ВК или что угодно, если есть)!\n\n"
    "<b>Правила:</b>\n"
    "• Контент должен быть авторским или с обязательным указанием источника.\n"
    "• Без NSFW, 18+, AI, спама и оскорблений.\n"
    "• Кровь, оголённый торс и сцены близости — под спойлер!\n"
    "• Количество предложенных постов не ограничено.\n\n"
    "После модерации пост будет опубликован в основном канале, "
    "и ты получишь уведомление об этом. Модераторы оставляют право отклонить публикацию без уведомления."
)

HELP = (
    "<b>Доступные команды:</b>\n\n"
    "/start — правила предложки\n"
    "/help — эта справка\n"
    "/cancel — отменить текущее действие"
)

CANCEL_NOTHING = "Нет активного действия для отмены."
CANCEL_OK = "Действие отменено."
CANCEL_REVERTED = "Возврат к посту."

LOCK_NOOP_HELD_BY = "🔒 Пост сейчас редактирует {mod}. Попробуйте позже."
LOCK_NOOP_GENERIC = "🔒 Эта кнопка временно неактивна."


# ── Viewer ─────────────────────────────────────────────────────────

SUBMISSION_ACCEPTED = (
    "<b>Пост (#{sub_id})</b> принят в предложку! Спасибо!\n\n"
    "Ты получишь уведомление, когда он будет опубликован."
)

TEXT_EMPTY_WARNING = "Сообщение не может быть пустым. Отправь текст или медиафайл."
TEXT_TOO_LONG = "Текст слишком длинный ({length} символов). Максимум — {max_length}."

SUBMISSION_NOT_FOUND = "Пост не найден."
NOT_YOUR_SUBMISSION = "Это не твой пост."
SUBMISSION_CANT_CANCEL = "Пост уже нельзя отменить (статус: {status})."
SUBMISSION_CANCELLED = "<b>Пост (#{sub_id})</b> отменён."
PUBLISHED_NOTIFICATION = (
    "Твой <b>пост (#{sub_id})</b> опубликован в канале! Спасибо за твоё творчество!"
)
SUBMISSION_SEND_ERROR = (
    "Пост принят, но произошла ошибка при отправке модератору. Обратись к Модди."
)
UNSUPPORTED_MEDIA = (
    "Этот тип медиа не поддерживается. Отправьте фото, видео, GIF или документ."
)

YOU_ARE_BANNED = (
    "Вы заблокированы и не можете отправлять посты.\n\n<b>Причина:</b> {reason}"
)


# ── Moderator ──────────────────────────────────────────────────────

POST_NOT_FOUND_OR_CANCELLED = "Пост не найден или был отменён автором."
CHOOSE_ACTION = "Выберите действие:"

EDIT_CAPTION_PROMPT = "Отправьте новое описание для поста.\nИли /cancel для отмены."

TAGS_WIZARD_SECTION = "Выберите теги из раздела «{section}»:"
TAGS_WIZARD_CUSTOM = (
    "Отправьте дополнительные теги текстом (например: #тег1 #тег2),\n"
    "или нажмите «Завершить редактирование»."
)
TAGS_UPDATED = "Теги обновлены."
TAGS_PREVIEW_HEADER = "<b>Предпросмотр:</b>\n{preview}\n─────────"
CUSTOM_TAG_TOO_LONG = "Тег «{tag}» слишком длинный (максимум {max} символов)."
CUSTOM_TAGS_LIMIT = "Превышен лимит пользовательских тегов (максимум {max} шт.)."
TAGS_TOO_LONG = (
    "Итоговая подпись с тегами слишком длинная ({length} символов). "
    "Максимум — {max_length}. Уберите лишние теги."
)

REJECT_REASON_PROMPT = (
    "Укажите причину отклонения поста #{sub_id}.\nИли /cancel для отмены."
)
REJECTED_WITH_REASON = "Пост #{sub_id} отклонён.\n\n<b>Причина:</b> {reason}"
REJECTED_SILENT = "Пост #{sub_id} тихо отклонён."
REJECTION_NOTIFICATION = (
    "😔 Твой пост #{sub_id} был отклонён модератором.\n\n<b>Причина:</b> {reason}"
)

PICK_DATE = "Выберите дату публикации:"
PICK_HOUR = "Дата: {date}\nВыберите час:"
PICK_MINUTE = "Дата: {date}\nВыберите минуты:"
CONFIRM_SCHEDULE = "<b>Подтверждение публикации поста #{sub_id}</b>\n\n{summary}"
SCHEDULED_OK = "Пост #{sub_id} запланирован на {time}."
UNSCHEDULED_OK = "Пост #{sub_id} снят с расписания."
CONFIRM_PUBLISH_NOW = "<b>Опубликовать пост #{sub_id} прямо сейчас?</b>\n\n{summary}"
PUBLISHED_NOW_OK = "Пост #{sub_id} опубликован в канал."
PUBLISH_NOW_FAILED = "Не удалось опубликовать пост #{sub_id}. Ошибка: {error}"
POST_CANCELLED_BY_AUTHOR = "Пост был отменён автором."

BAN_REASON_PROMPT = (
    "Укажите причину блокировки автора поста #{sub_id}.\nИли /cancel для отмены."
)
USER_BANNED = "Пользователь {user} заблокирован.\n\n<b>Причина:</b> {reason}"
USER_UNBANNED = "Пользователь {user} разблокирован."
USER_ALREADY_BANNED = "Пользователь уже заблокирован."
CANNOT_BAN_MODERATOR = "Нельзя заблокировать модератора."
MODERATOR_HOME = (
    "<b>Панель модератора</b>\n\n"
    "Здесь находится глобальное управление ботом: пресеты тегов, "
    "список заблокированных пользователей и восстановление карточек."
)
MODERATOR_HELP = (
    "<b>Команды модератора</b>\n\n"
    "/start — открыть панель модератора\n"
    "/help — эта справка\n"
    "/cancel — отменить текущий ввод или закрыть активный экран"
)
MANAGEMENT_MENU_TEXT = "<b>Управление</b>\n\nВыберите раздел."
MANAGEMENT_BANNED_USERS_TEXT = (
    "<b>Забаненные пользователи</b>\n\nНажмите на пользователя, чтобы снять блокировку."
)
MANAGEMENT_NO_BANNED_USERS_TEXT = "<b>Забаненные пользователи</b>\n\nСписок пуст."
MANAGEMENT_RECOVER_TEXT = "<b>Recover постов</b>\n\n{body}"
TAG_PRESETS_MENU_TEXT = "<b>Пресеты тегов</b>\n\nВыберите раздел для управления."
TAG_PRESET_SECTION_TEXT = (
    "<b>{section}</b>\n\n"
    "Элементов: {count}\n"
    "Выберите запись для редактирования или добавьте новую.\n\n"
    "Новый элемент можно отправить как <code>тег</code> или <code>label | tag</code>."
)
TAG_PRESET_SECTION_EMPTY_TEXT = (
    "<b>{section}</b>\n\n"
    "Список пуст. Можно добавить первый элемент.\n\n"
    "Отправьте <code>тег</code> или <code>label | tag</code>."
)
TAG_PRESET_ITEM_TEXT = (
    "<b>{section}</b>\n\n<b>Label:</b> {label}\n<b>Tag:</b> <code>#{tag}</code>"
)
TAG_PRESET_SECTION_DELETE_CONFIRM_TEXT = (
    "<b>{section}</b>\n\n"
    "Удалить раздел и все {count} его пресетов?\n\n"
    "Уже сохранённые теги в постах не изменятся: при следующем открытии wizard они останутся в custom tags."
)
TAG_PRESET_DELETE_CONFIRM_TEXT = (
    "<b>{section}</b>\n\nУдалить элемент <b>{label}</b> с тегом <code>#{tag}</code>?"
)
TAG_PRESET_ADD_PROMPT = (
    "<b>{section}</b>\n\n"
    "Отправьте новый элемент в формате <code>тег</code> или <code>label | tag</code>.\n"
    "Пример: <code>МайнШилд 5 | MineShield5</code>"
)
TAG_PRESET_SECTION_ADD_PROMPT = (
    "<b>Новый раздел</b>\n\n"
    "Отправьте название раздела. Новый раздел сразу появится в wizard и будет выводиться сеткой 3 в ряд."
)
TAG_PRESET_SECTION_EDIT_PROMPT = "<b>{section}</b>\n\nОтправьте новое название раздела."
TAG_PRESET_EDIT_LABEL_PROMPT = (
    "<b>{section}</b>\n\nТекущий label: <b>{label}</b>\nОтправьте новый label."
)
TAG_PRESET_EDIT_TAG_PROMPT = (
    "<b>{section}</b>\n\n"
    "Текущий tag: <code>#{tag}</code>\n"
    "Отправьте новый tag без # и пробелов."
)
TAG_PRESET_SECTION_CREATED = "Раздел добавлен."
TAG_PRESET_SECTION_UPDATED = "Раздел обновлён."
TAG_PRESET_SECTION_DELETED = "Раздел удалён."
TAG_PRESET_CREATED = "Элемент добавлен."
TAG_PRESET_UPDATED = "Элемент обновлён."
TAG_PRESET_DELETED = "Элемент удалён."
TAG_PRESET_NOT_FOUND = "Элемент больше не найден."
TAG_PRESET_SECTION_NOT_FOUND = "Раздел больше не найден."
TAG_PRESET_EMPTY_SECTION_LABEL = "Название раздела не может быть пустым."
TAG_PRESET_EMPTY_LABEL = "Label не может быть пустым."
TAG_PRESET_EMPTY_TAG = "Tag не может быть пустым."
TAG_PRESET_INVALID_TAG = "Tag должен быть без пробелов и символа |. # можно не писать."
TAG_PRESET_INVALID_FORMAT = (
    "Используйте формат <code>тег</code> или <code>label | tag</code>."
)
TAG_PRESET_DUPLICATE_SECTION_LABEL = "Раздел с таким названием уже есть."
TAG_PRESET_DUPLICATE_LABEL = "Элемент с таким label уже есть в этом разделе."
TAG_PRESET_DUPLICATE_TAG = "Элемент с таким tag уже есть в этом разделе."
BTN_MANAGEMENT = "Управление"
BTN_SUBMIT_ART = "📨 Предложить арт"
BTN_CLOSE = "Закрыть"
BTN_TAG_PRESETS = "Пресеты тегов"
BTN_BANNED_USERS = "Забаненные пользователи"
BTN_RECOVER_POSTS = "Recover постов"
BTN_BACK = "Назад"
BTN_ADD = "Добавить"
BTN_ADD_SECTION = "Добавить раздел"
BTN_EDIT_LABEL = "Изменить label"
BTN_EDIT_TAG = "Изменить tag"
BTN_EDIT_SECTION = "Переименовать раздел"
BTN_DELETE = "Удалить"
BTN_DELETE_SECTION = "Удалить раздел"
BTN_DELETE_CONFIRM = "Да, удалить"
BTN_EDIT_MEDIA = "🖼 Редактировать медиа"
BTN_ADD_MEDIA = "➕ Добавить медиа"
BTN_MEDIA_DONE = "← Готово"

MEDIA_TYPE_LABELS = {"photo":"📷 Фото","video":"🎬 Видео","animation":"🎞 GIF","document":"📄 Документ"}

MEDIA_MANAGER_TITLE = "🖼 <b>Медиа поста #{sub_id}</b> — вложений: {count}"
MEDIA_ADD_PROMPT = "Пришлите медиа (можно альбомом). Когда закончите — нажмите «Готово»."
MEDIA_DELETED = "Вложение удалено."
MEDIA_DELETE_LAST_FORBIDDEN = "Нельзя удалить последнее вложение. У поста должно остаться хотя бы одно медиа (или сделайте его текстовым иначе)."
MEDIA_ADDED = "Медиа добавлено ({count} шт.)."
MEDIA_COMPOSITION_INVALID = "Нельзя смешивать эти типы вложений в одном посте: GIF можно отправлять только отдельно, документы — только с документами."
MEDIA_CAPTION_TOO_LONG_FOR_MEDIA = "Не получается добавить медиа: описание длиннее 1024 символов, что превышает лимит для медиа-поста. Сократите описание."
MEDIA_UNSUPPORTED = "Этот тип медиа не поддерживается."
MEDIA_ADDING_EXPECT_MEDIA = "Ожидаю медиа. Пришлите файл или нажмите «Готово» для выхода."


MODERATOR_SUBMIT_PROMPT = (
    "Пришлите фото, видео, GIF или текст — он попадёт в предложку.\n"
    "Можно прислать несколько. Для выхода — /cancel."
)

RECOVER_NO_ACTIVE = "Нет активных постов для проверки."
RECOVER_CHECKING = "Проверяю {count} активных постов..."
RECOVER_DONE = "Восстановлено постов: {recovered} из {total} активных."
RECOVER_ALL_OK = "Все {total} активных постов на месте."


# ── Contact ────────────────────────────────────────────────────────

CONTACT_PROMPT = "Напишите сообщение автору поста #{sub_id}.\nИли /cancel для отмены."

# Pattern used by IsModeratorReply filter in contact.py to extract sub_id from bot messages.
# Keep in sync with MODERATOR_MESSAGE_TO_VIEWER template below.
VIEWER_REPLY_PATTERN = r"поста #(\d+)"

MODERATOR_MESSAGE_TO_VIEWER = (
    "Сообщение от модератора по поводу поста #{sub_id}:\n\n"
    "{text}\n\n"
    "<i>Ответьте на это сообщение, чтобы ответить модератору.</i>"
)

MESSAGE_SENT = "Сообщение отправлено автору."
MESSAGE_FAILED = "Не удалось отправить сообщение (пользователь заблокировал бота?)."

REPLY_SENT = "Ваш ответ отправлен модератору."
REPLY_TEXT_ONLY = (
    "Пожалуйста, ответьте текстом — медиафайлы в ответах модератору не поддерживаются."
)

RATE_LIMIT_EXCEEDED = "Слишком много запросов. Подождите немного."

SCHEDULE_TIME_PAST = "Выбранное время уже прошло. Выберите другое."
SUBMISSION_NOT_AVAILABLE = "Заявка недоступна или уже обработана."
SOMETHING_WENT_WRONG = "Произошла ошибка. Попробуйте снова."
NETWORK_ERROR_RETRY = "⚠️ Проблемы со связью с Telegram. Повторите действие через пару секунд."
CAPTION_TOO_LONG = "Описание слишком длинное. Максимум — 1024 символа для медиапоста (без учёта тегов)."
MEDIA_GROUP_EXTRA_CAPTIONS_WARNING = (
    "⚠️ Обратите внимание: описание принято только с первого файла альбома. "
    "Подписи к остальным файлам ({count} шт.) проигнорированы — "
    "Telegram не поддерживает отдельные подписи для каждого файла в альбоме."
)


# ── Forum topics ───────────────────────────────────────────────────


# Statuses shown in the legend, in display order. Emoji and label are NOT
# duplicated here — they come from ``core.topic_status_config`` so the legend
# can never drift from the icons actually painted on topics and queue lines.
TOPIC_LEGEND_STATUS_KEYS = (
    "pending",
    "editing",
    "scheduled",
    "published",
    "rejected",
    "cancelled",
)

# Per-status explanation used by ``build_topic_nav_legend``.
TOPIC_LEGEND_DESCRIPTIONS: dict[str, str] = {
    "pending": "пост ждёт модерации",
    "editing": "модератор редактирует прямо сейчас",
    "scheduled": "запланировано",
    "published": "опубликовано",
    "rejected": "последний пост отклонён",
    "cancelled": "последний пост отменён автором",
}

TOPIC_NAV_LEGEND_TEMPLATE = (
    "<b>Легенда</b>\n\n"
    "<b>Статусы тем (иконка — цвет темы):</b>\n"
    "{status_lines}\n\n"
    "<b>Полезные команды модератора:</b>\n"
    "• Редактирование пресетов тегов — {panel_link} → «Управление» → «Пресеты тегов»\n"
    "• Разбан пользователей — {panel_link} → «Управление» → «Заблокированные»\n"
)

# Deep link into the bot's DM: opening it never runs a command in the current
# chat, unlike a bare "/start" mention inside the moderator group.
TOPIC_NAV_PANEL_LINK = '<a href="https://t.me/{bot_username}?start=panel">панель модератора</a>'
# Fallback when the bot username is unknown (link cannot be built).
TOPIC_NAV_PANEL_PLAIN = "панель модератора (DM боту → /start)"

TOPIC_WELCOME_TEXT = "👤 Тема пользователя: {display}\n\nЗдесь появляются все посты этого автора и переписка с модерацией."

# ── Topic notifications ────────────────────────────────────────────

# Shown to the moderator who tried to open a locked post
MODERATOR_POST_LOCKED = "🔒 Пост #{sub_id} сейчас редактирует {mod}. Попробуйте позже."
# Shown to the moderator whose session expired while editing
MODERATOR_LOCK_LOST = (
    "⌛ Ваша сессия редактирования истекла. Закройте этот экран и откройте пост заново."
)

# System posts sent to the author's forum topic (disable_notification=True)
TOPIC_NOTIFY_CAPTION_CHANGED = "✏️ {mod} изменил описание поста #{sub_id}.\n\n{diff}"
TOPIC_NOTIFY_TAGS_CHANGED = "🏷 {mod} изменил теги поста #{sub_id}.\n\n{diff}"
TOPIC_NOTIFY_SCHEDULED = "📅 {mod} запланировал пост #{sub_id} на {time}."
TOPIC_NOTIFY_RESCHEDULED = "📅 {mod} перенёс публикацию поста #{sub_id} на {time}."
TOPIC_NOTIFY_UNSCHEDULED = "📅 {mod} снял пост #{sub_id} с расписания."
TOPIC_NOTIFY_PUBLISHED_BY_MOD = "✅ {mod} опубликовал пост #{sub_id} вручную."
TOPIC_NOTIFY_PUBLISHED_SCHEDULED = "✅ Пост #{sub_id} опубликован по расписанию."
TOPIC_NOTIFY_REJECTED = "❌ {mod} отклонил пост #{sub_id}.\n\n<b>Причина:</b> {reason}"
TOPIC_NOTIFY_REJECTED_SILENT = "❌ {mod} тихо отклонил пост #{sub_id}."
TOPIC_NOTIFY_BANNED = (
    "🚫 {mod} заблокировал автора поста #{sub_id}.\n\n<b>Причина:</b> {reason}"
)
TOPIC_NOTIFY_UNBANNED = "✅ {mod} разблокировал пользователя {user}."
TOPIC_NOTIFY_VIEWER_CANCELLED = "🚫 Автор отменил пост #{sub_id}."
TOPIC_NOTIFY_MEDIA_CHANGED = "🖼 Состав медиа изменён модератором {mod} (пост #{sub_id})"
# Stale topic card that could not be deleted (>48h, Telegram limit) after a
# media re-post. The fresh card is posted right below it.
TOPIC_CARD_OUTDATED = (
    "♻️ <b>Устаревшая версия поста #{sub_id}</b>\n"
    "Состав медиа изменён — актуальная карточка ниже ⬇️"
)
TOPIC_CONTACT_FROM_MOD = "💬 {mod} → автору:\n\n{text}"
TOPIC_CONTACT_FROM_VIEWER = "💬 Автор → модерации:\n\n{text}"

# ── Admin DM notifications ─────────────────────────────────────────

ADMIN_NOTIFY_PRESET_CREATED = (
    "🏷 {actor} добавил пресет <b>{label}</b> (#{tag}) в раздел «{section}»."
)
ADMIN_NOTIFY_PRESET_UPDATED = (
    "🏷 {actor} изменил пресет <b>{label}</b> (#{tag}) в разделе «{section}»."
)
ADMIN_NOTIFY_PRESET_DELETED = (
    "🏷 {actor} удалил пресет <b>{label}</b> (#{tag}) из раздела «{section}»."
)
ADMIN_NOTIFY_SECTION_CREATED = "📂 {actor} добавил раздел тегов <b>{label}</b>."
ADMIN_NOTIFY_SECTION_UPDATED = "📂 {actor} переименовал раздел тегов в <b>{label}</b>."
ADMIN_NOTIFY_SECTION_DELETED = (
    "📂 {actor} удалил раздел тегов <b>{label}</b> со всеми его пресетами."
)
ADMIN_NOTIFY_USER_BANNED = (
    "🚫 {actor} заблокировал пользователя {user}.\n\n<b>Причина:</b> {reason}"
)
ADMIN_NOTIFY_USER_UNBANNED = "✅ {actor} разблокировал пользователя {user}."

# ── Management section locks ───────────────────────────────────────

MANAGEMENT_PRESET_LOCKED = (
    "🔒 Раздел пресетов сейчас редактирует {mod}. Попробуйте позже."
)
MANAGEMENT_BANNED_LOCKED = (
    "🔒 Список заблокированных сейчас редактирует {mod}. Попробуйте позже."
)
MANAGEMENT_LOCK_LOST = "⌛ Ваша сессия управления истекла. Войдите в раздел заново."

# ── Admin-only actions ─────────────────────────────────────────────

RECOVER_ADMIN_ONLY = "⛔ Функция Recover доступна только администраторам."
