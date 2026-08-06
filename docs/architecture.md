# Архитектура tg-submission-bot

## Назначение

Telegram-бот для системы предложки творческого контента (арты, анимации, видео и т.п.) от зрителей с модерацией и запланированной публикацией в основной Telegram-канал.

**Роли:** один или несколько модераторов (ID через `MODERATOR_IDS` в конфиге), неограниченное число зрителей.

---

## Стек

| Компонент | Технология | Назначение |
|-----------|-----------|------------|
| Bot framework | aiogram 3.x | Telegram Bot API, FSM, routers, middleware |
| ORM | SQLAlchemy 2.0 async | Модели, запросы, relationships |
| БД | PostgreSQL + asyncpg | Хранение данных |
| Миграции | Alembic | Версионирование схемы БД |
| Планировщик | APScheduler 3.x | Отложенная публикация |
| Конфигурация | pydantic-settings | Типизированные настройки из .env |
| FSM Storage | MemoryStorage / RedisStorage | Опционально: persistent FSM через Redis (`REDIS_URL`) |
| Деплой | Docker + docker-compose | Бот и Redis в compose; Postgres опционально как внешний сервис или закомментированный блок |

---

## Структура проекта

```
tg-submission-bot/
├── main.py                    # Точка входа: sys.path → src/, asyncio.run(main())
├── .env.example               # Шаблон переменных окружения
├── .gitignore
├── .dockerignore              # Исключает .venv, __pycache__, .git, .env, logs/
├── requirements.txt
├── requirements-dev.txt        # Dev deps: pytest, pytest-asyncio, pytest-cov
├── Dockerfile                 # python:3.12-slim, non-root user (botuser), CMD ["python", "main.py"]
├── docker-compose.yml
├── alembic.ini                # prepend_sys_path = src
├── pytest.ini                 # Pytest config + integration marker
│
├── alembic/
│   ├── env.py                 # sys.path → src/, Base.metadata + core.config; поддерживает config.attributes["connection"] для программного вызова
│   ├── script.py.mako         # Шаблон миграций
│   └── versions/              # Автогенерируемые миграции
│
└── src/
    ├── core/
    │   ├── bot.py             # _create_dispatcher(), _create_storage(), main() — lifecycle, proxy, ensure_general_topic_nav(), polling
    │   ├── config.py          # Вложенные Settings: BotSettings, DBSettings + верхний уровень; moderator_ids, redis_url, proxy_url
    │   ├── messages.py        # Все user-facing строки (константы с плейсхолдерами)
    │   ├── logging.py         # create_logger() / get_logger() / fmt_user() — RotatingFileHandler + console
    │   ├── exceptions.py      # Доменные исключения: MSBotError, Submission*, Publication*, UserNotReachableError
    │   ├── rules_config.py    # Конфигурация текста правил канала
    │   └── topic_status_config.py  # Конфигурация приоритета/эмодзи статусов топиков
    │
    ├── db/
    │   ├── models.py          # SQLAlchemy ORM: User, Submission, SubmissionMedia, Publication, TagPresetSection, TagPresetEntry, Message
    │   ├── session.py         # engine + session_factory на уровне модуля, check_db_connection(), run_migrations(), shutdown_db()
    │   ├── queries/                # Пакет CRUD-операций (SQLAlchemy async)
    │   │   ├── __init__.py         # Реэкспорт публичных функций
    │   │   ├── submissions.py      # CRUD submissions
    │   │   ├── submission_media.py # add_media, delete_media, get_media
    │   │   ├── publications.py     # CRUD publications
    │   │   ├── tag_presets.py      # CRUD разделов/элементов пресетов тегов
    │   │   ├── users.py            # upsert_user, ban/unban
    │   │   ├── topics.py           # UserTopic CRUD
    │   │   └── messages.py         # system_messages CRUD
    │
    ├── handlers/
    │   ├── common.py          # /start (правила), /help, /cancel — с логированием через fmt_user()
    │   ├── viewer.py          # Приём медиа и текстовых постов, media groups (с asyncio.Lock), отмена поста; предупреждение о лишних подписях в альбоме
    │   ├── moderator/         # Пакет модератора (рефакторинг из monolith moderator.py)
    │   │   ├── __init__.py    # router + include sub-routers + /cancel
    │   │   ├── _helpers.py    # _delete_tracked_messages(), _send_submission_view(), TERMINAL_STATUSES
    │   │   ├── management.py  # Домашний экран модератора + глобальное меню управления + CRUD разделов/элементов пресетов
    │   │   ├── recover.py     # Логика восстановления карточек для кнопки Recover
    │   │   ├── review.py      # `/start` модератора + deep link /start review_<id>, handle_close
    │   │   ├── edit.py        # Редактирование описания (FSM)
    │   │   ├── reject.py      # Отклонение с причиной / без
    │   │   ├── schedule.py    # Календарь → час → минуты → подтверждение (~200 строк)
    │   │   ├── publish_now.py # Немедленная публикация с подтверждением
    │   │   ├── unschedule.py  # Снятие с расписания
    │   │   ├── ban.py         # Блокировка автора из карточки поста
    │   │   ├── media.py       # Media Manager: add/delete media in existing submissions (FSM `editing_media` / `adding_media`)
    │   │   ├── submit.py      # viewer submission intake from moderator context
    │   │   └── view.py        # submission viewing/navigation handler
    │   ├── tag_wizard.py      # Динамический визард тегов: N разделов из БД + custom page
    │   └── contact.py         # Связь модератор ↔ зритель; IsModeratorReply — фильтр реплаев (regex из msg.VIEWER_REPLY_PATTERN)
    │
    ├── keyboards/
    │   ├── callbacks.py       # CallbackData factories (SubmissionCB, ManagementCB, TagPresetCB, CalendarCB, TimeCB, TagWizardCB и др.)
    │   ├── viewer.py          # Кнопка «Отменить предложение»
    │   ├── moderator.py       # Кнопки действий, moderator-home, management UI, кнопка в канале модератора, подтверждения
    │   ├── calendar.py        # Inline-календарь + выбор часа/минут (шаг 5 мин, блокировка прошлого, кнопки назад)
    │   └── tags.py            # Клавиатуры динамического визарда тегов: страницы разделов + custom page
    │
    ├── states/
    │   ├── moderator.py       # ModeratorReview: состояния карточки поста + management + tag wizard + ban
    │   └── contact.py         # ContactViewer (writing_message)
    │
    ├── middlewares/
    │   ├── rate_limit.py      # ThrottleMiddleware: 20 req/10s на пользователя; alert на CallbackQuery; авто-очистка пустых ключей
    │   ├── db.py              # DbSessionMiddleware: инъекция AsyncSession, auto commit/rollback (BaseException)
    │   └── auth.py            # AuthMiddleware: upsert user → data["db_user"], логирование новых пользователей
    │
    ├── services/
    │   ├── publisher.py          # Публикация в канал + уведомление зрителя + финализация карточки; идемпотентность; retry (3 попытки); вызывает render_queue после публикации
    │   ├── scheduler.py          # APScheduler обёртка + восстановление jobs из БД; topic_title_sync_job обрабатывает одну outbox-задачу/10с, reconcile каждые 10 мин только ставит drift в очередь
    │   ├── topics.py             # Сервис форум-топиков: карточки, transactional request_topic_title_sync, outbox consumer, DB-only reconcile, ensure_general_topic_nav
    │   ├── topics_queue.py       # Очередь-борд: render_queue(bot, session) — event-driven; строит/обновляет сообщения general:queue:NN в General-топике; идемпотентность через MD5; asyncio.Lock против параллельных вызовов
    │   ├── edit_lock.py          # Оптимистичные блокировки редактирования: acquire/extend/release/force_release + cleanup
    │   ├── admin_notifications.py # Рассылка DM-уведомлений всем is_admin-пользователям при CRUD пресетов/разделов
    │   ├── topic_notifications.py # Уведомления внутри темы (published, rejected, message from mod)
    │   ├── media_append.py       # Album buffering for media append
    │   └── submission_intake.py  # Viewer media-group buffering on initial submission
    │
    ├── filters/
    │   ├── is_moderator.py    # IsModerator: проверка event.from_user.id in config.moderator_ids
    │   └── is_admin.py        # IsAdmin: проверка event.from_user.id in config.admin_ids
    │
    └── utils/
        ├── media.py           # extract_media_info() — file_id/type из Message
        ├── formatting.py      # HTML-форматирование превью, резюме, текста канала модератора; STATUS_MAP; get_html_caption()/get_html_text() — конвертация entities → HTML при сохранении
        ├── tags.py            # format_tags_line(), compose_caption(), validate_caption_length() (1024 для медиа, 4096 для text-only; считает plain text после strip HTML)
        ├── diffs.py           # Caption diff rendering for notifications
        └── html_entities.py   # HTML entity encoding/decoding helpers

logs/                          # Файлы логов (в .gitignore, создаётся в корне проекта)
    └── submission_bot.log

tests/
    ├── conftest.py            # Общие pytest fixtures + безопасные env defaults для импорта src/
    ├── helpers.py             # Фейки Bot/FSM/session и фабрики ORM-объектов
    ├── integration/
    │   ├── conftest.py        # Async engine/session fixtures для real PostgreSQL через TEST_DATABASE_URL
    │   ├── test_db_queries.py # Интеграционные тесты db.queries на PostgreSQL
    │   └── test_migrations.py # Проверка alembic upgrade head на пустой тестовой БД
    ├── test_utils.py          # Pure utility tests
    ├── test_middlewares.py    # Middleware tests
    ├── test_services_*.py     # Publisher / scheduler / mod-channel tests
    └── test_handlers_*.py     # Viewer / moderator / tag-wizard tests
```

---

## Middleware-конвейер

Порядок регистрации (outer → inner):

1. **ThrottleMiddleware** — 20 запросов / 10 с на пользователя; отсекает спам до открытия DB-сессии; на `CallbackQuery` показывает alert «Слишком много запросов»
2. **DbSessionMiddleware** — создаёт AsyncSession, инъектирует в `data["session"]`, коммитит при успехе / откатывает при `BaseException`
3. **AuthMiddleware** — upsert user (`INSERT ... ON CONFLICT`) → `data["db_user"]`; логирует новых пользователей

Каждый handler получает:
- `session: AsyncSession` — SQLAlchemy сессия (auto-commit при успехе)
- `db_user: User` — ORM-объект текущего пользователя

---

## Роутеры (порядок регистрации)

```python
dp.include_routers(
    moderator.router,    # Первый: /start deep link и /cancel для модератора (IsModerator фильтрует)
    common.router,       # /start, /help, /cancel — для всех остальных
    contact.router,      # Связь модератор ↔ зритель (до viewer, чтобы IsModeratorReply перехватывал реплаи)
    viewer.router,       # Обработчики предложений зрителя, отмена
)
```

**Порядок важен:**
- `moderator` **первым** — его `/start` (домашний экран модератора или deep link `review_*`) и `/cancel` (с очисткой превью/management message) обрабатываются раньше, чем обычные команды в `common`; сообщения не-модераторов проскакивают благодаря `IsModerator()` фильтру на роутере
- `contact` перед `viewer` — реплаи зрителя на сообщения модератора не создают новые сабмиты и не попадают в обработчик viewer как обычные текстовые предложения

---

## Пресеты тегов

- Runtime-источник пресетов — связка таблиц `tag_preset_sections` + `tag_presets` в PostgreSQL
- Alembic-миграция сидирует стартовые разделы и значения из прежней фиксированной схемы, после чего wizard и management UI работают только через БД
- Экран `Управление` в ЛС модератора позволяет CRUD для самих разделов и для элементов внутри каждого раздела
- В wizard количество пресет-страниц больше не захардкожено: страницы строятся по `tag_preset_sections.sort_order`, затем всегда идёт custom page
- Удаление или переименование пресета/раздела **не переписывает** уже сохранённые `submissions.tags`; исчезнувшие значения переходят в custom tags при следующем открытии визарда

---

## Конфигурация (.env)

```env
BOT__TOKEN=...             # Telegram Bot API token → config.bot.token
DB__HOST=postgres          # Хост PostgreSQL → config.db.host
DB__PORT=5432              # → config.db.port
DB__NAME=tg_submission_bot # → config.db.name
DB__USER=postgres          # → config.db.user
DB__PASSWORD=...           # → config.db.password
MODERATOR_IDS=123456789,987654321  # Telegram ID модераторов (через запятую) → config.moderator_ids
CHANNEL_ID=-100...         # ID целевого канала (отрицательное число) → config.channel_id
MODERATOR_GROUP_ID=-100...   # ID форум-группы модератора → config.moderator_group_id
ADMIN_IDS=123456789          # (опционально) подмножество MODERATOR_IDS с правами администратора
TIMEZONE=Europe/Moscow     # Для календаря и планировщика → config.timezone
LOG_LEVEL=INFO             # Уровень логирования → config.log_level
REDIS_URL=                 # (опционально) Redis URL для FSM Storage; если пусто — MemoryStorage
PROXY_URL=                 # (опционально) SOCKS5/HTTP прокси для Telegram API
SILENT_MODERATOR_NOTIFICATIONS=true  # (опционально) тихие сообщения в модгруппу и DM админам → config.silent_moderator_notifications
DB_AUTO_MIGRATE=true       # (опционально) применять миграции при старте; false — только проверка → config.db_auto_migrate
DB_MIGRATE_TIMEOUT=300     # (опционально) таймаут прогона миграций, секунды → config.db_migrate_timeout
API_HOST=0.0.0.0           # (опционально) host HTTP-сервера health/metrics → config.api_host
API_PORT=5400              # (опционально) порт HTTP-сервера health/metrics → config.api_port
```

### Доступ из кода

```python
from core.config import config

config.bot.token              # BOT_TOKEN
config.db.url                 # postgresql+asyncpg://... (для SQLAlchemy async)
config.moderator_ids              # list[int] — все ID модераторов
config.moderator_id               # int — первый ID (обратная совместимость)
config.redis_url                  # str | None — Redis URL для FSM Storage
config.proxy_url                  # str | None — прокси для Telegram API
config.channel_id
config.moderator_group_id
config.admin_ids              # list[int] — ID администраторов
config.edit_lock_ttl_seconds  # int — TTL блокировок редактирования (секунды)
config.topic_title_max_length # int — макс. длина заголовка топика
config.timezone
config.log_level
```

---

## Логирование

`core/logging.py` — централизованная настройка:

- `create_logger(level)` вызывается один раз в `core/bot.py` при старте
- Два хендлера: console (уровень из `LOG_LEVEL`) + `RotatingFileHandler` (`logs/submission_bot.log`, 5 MB, 5 бэкапов)
- Шумные библиотеки (`aiogram`, `aiohttp`, `apscheduler`, `sqlalchemy.engine`) глушатся до WARNING
- `sys.excepthook` перехватывает необработанные исключения
- Все модули используют `get_logger(__name__)` — дочерние логгеры в пространстве `submission_bot.*`
- `fmt_user(db_user)` — форматирование пользователя для логов (username или full_name)

---

## Docker

**Bot:** `python:3.12-slim`, non-root user (`botuser`), pip install, `CMD ["python", "main.py"]`

**Redis:** `docker-compose.yml` по умолчанию поднимает `redis:7-alpine`; сервис `bot` ждёт healthcheck Redis и может использовать его через `REDIS_URL`.

**Postgres:** сервис `postgres` в том же compose-файле (volume + healthcheck). Если база уже своя — адрес прописывается в `DB__HOST` / `DB__PORT`, и встроенный сервис просто не запускается (`docker compose up -d bot redis`).

**`.dockerignore`:** исключает `.venv`, `__pycache__`, `.git`, `.env`, `logs/`, `*.md`

При запуске бот: проверяет доступность БД (`check_db_connection()`), **применяет миграции** (`run_migrations()`), при наличии `PROXY_URL` создаёт `AiohttpSession(proxy=...)`, регистрирует команды, проверяет доступ к каналам, вызывает `ensure_general_topic_nav()`, запускает планировщик и восстанавливает jobs из БД.

### Автоприменение миграций

`db/session.py::run_migrations()` вызывается из `core/bot.py` сразу после `check_db_connection()`:

1. Сравнивает `MigrationContext.get_current_heads()` с head'ами `ScriptDirectory`. Совпали — no-op и лог «Схема БД актуальна».
2. Схема отстаёт → берёт session-level `pg_advisory_lock(728413002)`, **перепроверяет** ревизии под локом (параллельный инстанс мог уже всё применить) и запускает `alembic upgrade head`.
3. `command.upgrade` уходит в `asyncio.to_thread` — `alembic/env.py` внутри делает свой `asyncio.run()`, а вызывающий event loop не блокируется и работает `asyncio.wait_for(timeout=DB_MIGRATE_TIMEOUT)`.
4. Любая ошибка/таймаут → `RuntimeError`, старт бота прерывается (лучше не подняться, чем работать на неконсистентной схеме). Лок снимается в `finally`.

Alembic-конфиг собирается **в памяти**, а не из `alembic.ini`: `env.py` вызывает `fileConfig(config_file_name)` и переинициализировал бы логгинг приложения.

Переменные окружения:

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `DB_AUTO_MIGRATE` | `true` | `false` — только проверка: при отставании схемы бот падает с ошибкой, ничего не применяя |
| `DB_MIGRATE_TIMEOUT` | `300` | Таймаут прогона в секундах |

**Graceful shutdown:** при остановке бот ожидает завершения in-flight медиа-групп (до 30 сек), затем вызывает `shutdown_scheduler()` (внутри `scheduler.shutdown(wait=True)`) для завершения текущих задач планировщика.

---

## Health-мониторинг

Рядом с ботом поднимается aiohttp-сервер (`services/health.py`, порт `API_PORT`, по умолчанию 5400):

- `GET /api/v1/health` — JSON для docker healthcheck. 503 (`status: dead`) — недоступен Telegram API или PostgreSQL; `status: degraded` при 200 — упал Redis или планировщик (рестарт не лечит).
- `GET /api/v1/metrics` — Prometheus text format для vmagent: `tgarts_telegram_up`, `tgarts_telegram_failures`, `tgarts_telegram_last_ok_seconds`, `tgarts_db_up`, `tgarts_redis_up` (только при заданном `REDIS_URL`), `tgarts_scheduler_running`.

`TelegramWatchdog` — фоновая задача: `bot.get_me()` каждые 60 с (таймаут 10 с), кэширует состояние, чтобы скрейпы метрик не били по Telegram API. Считает сбои подряд, при восстановлении логирует. Контейнер сам не рестартит — алерты внешние (Grafana).

Порт 5400 слушается внутри docker-сети, наружу публиковать не нужно: healthcheck ходит из самого контейнера, а скрейпер (vmagent/Prometheus) обращается по имени контейнера. Несколько инстансов из одного образа не конфликтуют — у каждого своя сеть/имя. Запуск/остановка — в `core/bot.py` (`watchdog.start()` + `start_health_server()` перед polling; `watchdog.stop()` + `runner.cleanup()` в `finally`).

---

## Пул соединений БД

```python
create_async_engine(
    config.db.url, pool_size=10, max_overflow=5, pool_recycle=600, pool_pre_ping=True,
    connect_args={"command_timeout": 30.0, "server_settings": {
        "statement_timeout": "30000", "idle_in_transaction_session_timeout": "60000"}},
)
```

- `pool_recycle=600` — переподключение каждые 10 минут для устранения проблем с idle-таймаутами PostgreSQL
- `pool_pre_ping=True` — проверяет соединение перед выдачей из пула
- `command_timeout=30` — asyncpg перестаёт ждать ответ. Без него оборванная сеть оставляет корутину висеть навсегда: PostgreSQL видит сессию как `idle in transaction`, а её row lock блокирует всех писателей за собой
- `statement_timeout` / `idle_in_transaction_session_timeout` — та же страховка со стороны сервера: зависшая транзакция умирает через минуту и отпускает блокировки
- Миграции Alembic идут на **отдельном движке** без этих таймаутов (`NullPool`) — переписывание таблицы законно длится дольше, и обрывать его посередине хуже, чем медленный старт. Это же касается ожидания advisory-лока: на основном движке `statement_timeout` убил бы `pg_advisory_lock` через 30 с

**Инвариант границ транзакций:** внешний I/O (Telegram API, `asyncio.sleep` в retry) **не выполняется внутри открытой транзакции**, которая уже что-то записала. Схема — claim → call → record, каждая БД-фаза отдельно (`process_next_topic_title_sync`, `reconcile_submission_cards`). Правило распространяется и на handler'ы: `cmd_start_review`, `handle_close`, `_cancel_viewing_post`, `handle_media_done` коммитят запись блокировки/статуса **до** обновления карточки в Telegram; `cleanup_edit_locks_job` сначала коммитит удаление истёкших локов и только потом ходит в Telegram по каждому — в своей короткой сессии; `_render_queue_inner` / `_render_schedule_inner` закрывают read-only транзакцию перед первым `editMessageText`. Нарушение этого правила однажды заморозило воркер заголовков на двое суток: сетевой сбой подвесил корутину, транзакция осталась открытой, и `INSERT` новых пользователей встал в очередь за её блокировкой

---

## Зависимости

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
redis[asyncio]>=5.0.0,<6.0          # опционально, для FSM RedisStorage
```

### Dev / test dependencies

```
pytest>=8.3.0,<9.0
pytest-asyncio>=0.24.0,<1.0
pytest-cov>=5.0.0,<7.0
```

---

## Тестирование

- Основной `pytest`-suite разделён на быстрые unit/service tests и PostgreSQL integration tests в `tests/integration/`
- Integration tests требуют `TEST_DATABASE_URL`; если переменная не задана, они автоматически `skip`, поэтому локальный `pytest` остаётся безопасным
- `tests/integration/test_db_queries.py` проверяет реальные `db.queries` на PostgreSQL, включая `INSERT ... ON CONFLICT`, каскадные удаления, загрузку relationships и CRUD для публикаций/тегов
- `tests/integration/test_migrations.py` запускает `python -m alembic upgrade head` на пустой тестовой БД и валидирует создание ключевых таблиц
---

## Запуск

1. Скопировать `.env.example` → `.env`, заполнить реальными значениями
2. Применить миграции: `alembic upgrade head`
3. `docker-compose up --build`

Бот автоматически: проверит соединение с БД → при необходимости создаст Telegram session через proxy → получит bot username (`get_me()`) → зарегистрирует команды (`set_my_commands()`) → **проверит доступ к каналам** (channel_id, moderator_group_id) → закрепит навигационное сообщение в General-топике форума (`ensure_general_topic_nav()`) → запустит планировщик → восстановит незавершённые публикации → начнёт polling.

---

## Хранение медиа

Файлы **не скачиваются**. Хранится только Telegram `file_id` — стабильный идентификатор в рамках одного бота. При публикации файл отправляется по `file_id` через CDN Telegram (мгновенно, без трафика на сервере).
