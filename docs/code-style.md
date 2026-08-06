# Стиль кода

## Цель

Этот документ фиксирует решения, которые не покрываются Ruff, mypy и форматтером.

## Главный принцип

Код пишется для человека, который будет его отлаживать через полгода.

## Приоритеты

1. Ясность
2. Простота
3. Корректность
4. Проверяемость
5. Производительность только там, где она реально нужна

## Основные правила

1. Предпочитай простой и явный код, а не умные конструкции.
2. По умолчанию используй функции. Классы нужны там, где они владеют состоянием, lifecycle, зависимостями или одним цельным use case.
3. Не добавляй абстракции «на будущее». Protocol, ABC и дополнительные слои допустимы только при реальной границе или текущем дублировании.
4. Держи границы явными: handler отвечает за Telegram-событие, service — за orchestration и бизнес-операцию, middleware — за сквозные concerns.
5. Side effects должны быть очевидны. Не прячь доступ к БД, сети, логам, кешу или глобальному состоянию в случайных helper-функциях.
6. Используй понятные имена. Однобуквенные переменные допустимы только для локальных индексов.
7. Предпочитай читаемый control flow плотным one-liner конструкциям.
8. Комментарии и docstring объясняют мотив, инвариант или компромисс, а не пересказывают код.
9. Типизация должна улучшать понимание данных и контрактов, а не добавлять декоративную сложность.

## Composition и lifecycle

Точка входа — `main.py` в корне проекта:

```python
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import asyncio
from core.bot import main

if __name__ == "__main__":
    asyncio.run(main())
```

`core/bot.py` — composition root:

- `_create_storage()` — выбирает `RedisStorage` (если задан `REDIS_URL`) или `MemoryStorage`
- `_create_dispatcher()` — создаёт `Dispatcher(bot, storage=...)`, регистрирует middleware и routers
- `main()` — настраивает логирование, проверяет соединение с БД, создаёт `Bot` с `DefaultBotProperties(parse_mode="HTML")`, запускает `dp.start_polling(bot)`, graceful shutdown закрывает bot session и scheduler

**Нет service locator.** Long-lived ресурсы — module-level в `core/`, `db/`, `services/`:

- `db/session.py` — `engine` и `session_factory` на уровне модуля
- `services/scheduler.py` — scheduler на уровне модуля
- `middlewares/rate_limit.py` — `ThrottleMiddleware` с внутренним состоянием

Владение явное: `main()` вызывает shutdown в обратном порядке.

## Router / handler structure

Каждый handler-модуль определяет:

```python
from aiogram import Router

router = Router()
```

Регистрация в `core/bot.py` с фиксированным приоритетом:

```python
dp.include_routers(
    service_messages.router,   # подавляет технический шум форума
    moderator.router,          # /start deep link для модератора
    common.router,             # /start, /help, /cancel для всех
    contact.router,            # связь модератор ↔ зритель
    viewer.router,             # приём предложений зрителя
)
```

Handlers — `async def` с типизированными параметрами aiogram:

- `message: Message` — текстовые сообщения и команды
- `callback: CallbackQuery` — inline-кнопки
- `state: FSMContext` — состояние FSM
- `session: AsyncSession` — инжектируется `DbSessionMiddleware`
- `db_user: User` — инжектируется `AuthMiddleware`

Нет `Depends()`. Cross-cutting concerns — через middleware:

1. **`ThrottleMiddleware`** — 20 req/10s на пользователя
2. **`DbSessionMiddleware`** — создаёт `AsyncSession`, коммитит/откатывает
3. **`AuthMiddleware`** — upsert пользователя, инжектирует `db_user`

## FSM (Finite State Machine)

- По умолчанию `MemoryStorage`; при наличии `REDIS_URL` — `RedisStorage`
- Состояния — `StatesGroup` классы в пакете `states/`
- Каждое состояние — `State()` с именем, отражающим экран или действие
- `/cancel` обрабатывается через `STATE_CATEGORY` (management / viewing / sub) — словарь строковых ключей, определяющий поведение отмены

## DB sessions

`DbSessionMiddleware` в `middlewares/db.py`:

- Создаёт `AsyncSession` из `session_factory` (`db/session.py`)
- Инжектирует как `data["session"]`
- Auto-commit при успехе, auto-rollback при `BaseException`

Бизнес-логика в `services/` получает `session: AsyncSession` как параметр. Никакого обращения к глобальному engine из бизнес-кода.

## Строковые константы

Все user-facing строки — в `core/messages.py` как module-level константы с `.format()` плейсхолдерами:

```python
SUBMISSION_ACCEPTED = (
    "<b>Пост (#{sub_id})</b> принят в предложку! Спасибо!"
)
```

Никаких inline-строк в handlers.

`submissions.caption` хранит HTML — display функции передают HTML как есть, без `html.escape`.

## Логирование

- `get_logger(__name__)` из `core/logging.py` — возвращает дочерний логгер в пространстве `submission_bot.*`
- `fmt_user(db_user)` — форматирование пользователя: `[id:42 (@username)]`
- Два хендлера: console + `RotatingFileHandler` (5 MB, 5 бэкапов)
- Шумные библиотеки (`aiogram`, `aiohttp`, `apscheduler`, `sqlalchemy.engine`) — silenced до WARNING
- `sys.excepthook` перехватывает необработанные исключения

## Доменные исключения

Базовый класс `MSBotError` в `core/exceptions.py`:

- `SubmissionNotFoundError`, `SubmissionCancelledError`, `SubmissionStatusError`
- `PublicationNotFoundError`, `PublishFailedError`, `PublishStateUnknownError`
- `UserNotReachableError`

Обрабатываются в middleware или хендлерах — не пробрасываются сквозь aiogram.

## Тесты

- Проверяй поведение как можно ближе к измененному коду.
- Предпочитай самый маленький тест, который может опровергнуть текущую реализацию.
- Не пиши тесты, которые просто зеркалят внутреннее устройство кода.

## Anti-patterns

- speculative abstractions;
- module-level mutable lifecycle state без явного owner;
- giant utility modules со случайными helper-функциями;
- service locator style access в business logic;
- плотный код, в который сложно поставить breakpoint.

## Вопросы для review

1. Это действительно самая простая корректная версия?
2. Каждая абстракция окупается уже сейчас?
3. Границы и side effects очевидны?
4. Понятно ли, где ставить breakpoint?
5. Проверка действительно тестирует измененное поведение?
