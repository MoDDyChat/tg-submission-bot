# Edit-Locks в tg-submission-bot

## Назначение

Оптимистичные блокировки (edit locks) предотвращают одновременное редактирование одного ресурса двумя модераторами. Реализованы в `services/edit_lock.py` через таблицу `edit_locks`.

---

## Таблица `edit_locks`

| Поле | Тип | Описание |
|------|-----|----------|
| resource_type | VARCHAR(64) PK | Тип ресурса (см. ниже) |
| resource_id | VARCHAR(128) PK | ID ресурса (см. ниже) |
| moderator_id | BIGINT NOT NULL | Telegram ID держателя блокировки |
| acquired_at | TIMESTAMPTZ NOT NULL | Время захвата |
| expires_at | TIMESTAMPTZ NOT NULL | Время истечения (UTC) |

**Composite PK:** (`resource_type`, `resource_id`)

---

## Типы ресурсов

| resource_type | resource_id | Что защищает | TTL |
|---------------|-------------|--------------|-----|
| `submission` | `str(submission.id)` | Просмотр/редактирование конкретного поста | `config.edit_lock_ttl_seconds` (по умолчанию 300 с) |
| `management` | `presets` | CRUD разделов и пресетов тегов | `config.edit_lock_ttl_seconds` (по умолчанию 300 с) |
| `management` | `banned` | Список забаненных пользователей + разбан | `config.edit_lock_ttl_seconds` (по умолчанию 300 с) |

---

## API (`services/edit_lock.py`)

```python
acquire_lock(resource_type, resource_id, moderator_id, ttl_seconds) -> bool
```
Пытается захватить блокировку. Возвращает `True` при успехе. При наличии чужой активной блокировки — возвращает `False` (не бросает исключение). Использует `INSERT ... ON CONFLICT DO UPDATE` — атомарно.

```python
extend_lock(resource_type, resource_id, moderator_id, ttl_seconds) -> bool
```
Продлевает TTL существующей блокировки того же модератора. Возвращает `False`, если блокировка истекла или принадлежит другому модератору.

```python
release_lock(resource_type, resource_id, moderator_id) -> None
```
Освобождает блокировку. Только если `moderator_id` совпадает; чужие блокировки не трогает.

```python
force_release_lock(resource_type, resource_id) -> None
```
Принудительно удаляет любую блокировку (независимо от `moderator_id`). Используется при административном сбросе.

```python
cleanup_edit_locks_job(session) -> None
```
APScheduler job: удаляет все записи с `expires_at < NOW()`. Запускается периодически. Удаление коммитится **до** обхода Telegram: карточка каждого submission-лока обновляется потом, в отдельной короткой сессии на лок, чтобы сетевой вызов не удерживал транзакцию с записью (см. `docs/architecture.md`).

---

## Сценарии жизненного цикла

### submission lock

```
handler review.py (/start review_<id>)
  → acquire_lock("submission", str(sub_id), mod_id, ttl=300)
  → если False → "Пост сейчас редактирует другой модератор"

Во время активного сеанса (FSM viewing_post):
  → каждый callback начинает с extend_lock(...)
  → если False → MODERATOR_LOCK_LOST → FSM clear

Терминальные действия (publish / reject / ban):
  → release_lock после успешного действия

handle_close:
  → release_lock явно

APScheduler cleanup_edit_locks_job:
  → чистит истёкшие записи и коммитит; затем для каждого submission-лока
    обновляет карточку в теме форума (убирает "редактируется" индикатор)
    без уведомлений — каждая карточка в своей короткой сессии
```

### management lock

```
handle_open_presets / handle_open_banned
  → acquire_lock("management", "presets"|"banned", mod_id, ttl=300)
  → если False → "Раздел сейчас редактирует @other"

Каждый CRUD callback (add/edit/delete preset, add/edit/delete section, unban):
  → extend_lock("management", "presets"|"banned", mod_id, ttl=300)
  → если False → alert "Сессия истекла, войдите снова"

handle_close_management:
  → release_lock("management", "presets"|"banned", mod_id)
```

---

## Поведение при потере блокировки

При `extend_lock → False` (блокировка истекла или перехвачена):
- Модератор получает сообщение `MODERATOR_LOCK_LOST` из `core/messages.py`
- FSM очищается (`state.clear()`)
- Все временные сообщения превью удаляются

---

## Конфигурация

```env
EDIT_LOCK_TTL_SECONDS=300   # TTL блокировки в секундах (по умолчанию 5 минут)
```

Доступно через `config.edit_lock_ttl_seconds`.

---

## /cancel и блокировки

Поведение команды `/cancel` зависит от текущего FSM-состояния:

| Состояние | Поведение `/cancel` |
|-----------|---------------------|
| Нет состояния | «Нет активного действия» |
| management_* | Освобождает `management/presets` и `management/banned` → возврат на домашний экран |
| `viewing_post` | Освобождает `submission/<id>` + обновляет карточку → FSM сброшен |
| editing_*, picking_*, confirm_* (sub-state) | Продлевает лок: если лок ещё наш — удаляет sub-state сообщения, возврат в `viewing_post`; если лок утерян — FSM сброшен, сообщение `MODERATOR_LOCK_LOST` |

**Ключевой принцип:** выход из sub-state (`/cancel`) НЕ освобождает блокировку — модератор
остаётся «владельцем» поста. Только выход из `viewing_post` (или закрытие через «Закрыть»)
освобождает лок.

---

## noop callback (кнопка заблокированного поста)

Когда другой модератор открыл пост на редактирование, карточка в форум-теме показывает кнопку
`✏️ Редактирует @mod` с `callback_data="noop"`.

Индикатор лока **всегда вычисляется из `edit_locks`** внутри `topics._resolve_card_lock_owner()`,
а не передаётся вызывающим кодом: `update_submission_card()` и `probe_submission_card()` не
принимают `lock_owner`. Иначе любая точка, забывшая пробросить владельца (визард тегов,
редактирование описания, планирование), перерисовывала бы залоченный пост как свободный — со
статусом «Новое» и живой кнопкой «Редактировать». Модератор без `@username` даёт `locked_by=""`
(общая надпись «Редактируется модератором»), но карточка остаётся заблокированной.

Обработчик `handle_noop` в `handlers/moderator/__init__.py`:
1. Находит сабмишн по `topic_card_message_id == callback.message.message_id`
2. Получает активный лок через `edit_lock.get_active_lock`
3. Отвечает `callback.answer(LOCK_NOOP_HELD_BY.format(mod=...), show_alert=True)`
