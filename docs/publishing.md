# Система публикации tg-submission-bot

## Система планирования публикаций

**Архитектура:** APScheduler (MemoryJobStore) + PostgreSQL как источник истины.

- Время публикации хранится в UTC (конвертируется из локальной таймзоны при вводе модератором)
- При планировании: создаётся/обновляется запись в `publications` + APScheduler job с `trigger="date"` (`replace_existing=True`)
- При повторном планировании: сначала отменяется старый APScheduler job (`cancel_scheduled`), затем publication обновляется в БД, затем создаётся новый job. Это устраняет race condition между старым job и обновлением БД
- При снятии с расписания: APScheduler job отменяется (`cancel_scheduled`), publication удаляется, статус → pending
- При рестарте бота:
  1. `SELECT * FROM publications WHERE published_at IS NULL`
  2. `publish_at > NOW()` → пересоздание APScheduler job
  3. `publish_at <= NOW()` → немедленная публикация (пропущено во время простоя), если просрочка не превышает 24 часа
  4. Все datetime нормализуются в UTC перед сравнением
- Публикации, просроченные более чем на 24 часа, пропускаются с warning в логах и требуют ручной проверки модератором

---

## Публикация (publisher.py)

1. **Идемпотентность:** проверяет `published_at` — если уже опубликовано, пропускает
2. Загружает publication/submission с медиа или text-only содержимым и данными пользователя
3. Проверяет, что publication ещё существует, а submission всё ещё в статусе `scheduled`; отменённые, снятые с расписания или устаревшие job не публикуются
4. Собирает финальный caption: `compose_caption(sub.tags, edited_caption or sub.caption)` — теги сверху через ` | `, описание ниже
5. Отправляет в канал **с retry** (до 3 попыток):
  - Без медиа → `send_message`
   - 1 медиа → `send_photo/video/animation/document`
   - Несколько → `send_media_group` (все `message_id` сохраняются в `channel_message_ids`)
   - `TelegramRetryAfter` → ожидание `retry_after` секунд, повтор
   - `TelegramNetworkError` → повтор через 5 сек
   - Прочие ошибки → `PublishFailedError` без retry
6. **Фаза 1 (критическая):** Обновляет БД: `mark_published()` + статус → published → `commit()`
7. Если фиксация в БД после успешной отправки в канал падает, publisher сначала повторяет запись в новой DB-сессии; если и это не помогает, он пытается удалить уже отправленные сообщения из канала как компенсирующее действие
8. Если компенсирующее удаление тоже не удалось, бросается ошибка неоднозначного состояния — требуется ручная проверка модератором
9. **Фаза 2 (best-effort):** Финализирует карточку в теме форума (`finalize_submission_card`) — убирает кнопку редактирования и обновляет статус; ошибка логируется, не блокирует
10. **Фаза 3 (best-effort):** Уведомляет зрителя: «Твой пост опубликован»
11. **Фаза 4 (best-effort):** Обновляет очередь-борд (`render_queue(bot, session)`) — убирает опубликованный пост из списка ожидающих
12. Все исключения (не только `MSBotError`) перехватываются и логируются в scheduler

---

## Доменные исключения (core/exceptions.py)

| Исключение | Где бросается | Где ловится |
|------------|--------------|-------------|
| `SubmissionNotFoundError` | `publisher.py` | `scheduler.py` |
| `SubmissionCancelledError` | `publisher.py` | `scheduler.py` |
| `SubmissionStatusError` | `publisher.py` | `scheduler.py` |
| `PublicationNotFoundError` | `publisher.py` | `scheduler.py` |
| `PublishFailedError` | `publisher.py` | `scheduler.py` |
| `PublishStateUnknownError` | `publisher.py` | `scheduler.py`, `publish_now.py` |
| `UserNotReachableError` | — | — |

Хендлеры не бросают доменных исключений — обрабатывают ситуации inline и отвечают пользователю.

---

## Обработка ошибок

- **Retry в publisher:** до 3 попыток при `TelegramRetryAfter` (ожидание `retry_after`) и `TelegramNetworkError` (ожидание 5 сек); это же покрывает Telegram flood control
- **Зритель заблокировал бота:** ловится при уведомлении (`publisher.py`), логируется как warning
- **Зритель отменил пост во время модерации:** проверка статуса на каждом шаге FSM; терминальные статусы: `cancelled`, `published`, `rejected`
- **Рестарт бота:** все scheduled jobs восстанавливаются из publications; FSM state теряется при MemoryStorage (сохраняется при RedisStorage — `REDIS_URL`); «Закрыть» работает корректно через `callback.message.delete()`
- **Graceful shutdown:** бот ожидает завершения in-flight медиа-групп (до 30 сек) и вызывает `scheduler.shutdown(wait=True)`
- **Ошибки форум-топика модератора:**
  - `topics.post_submission_card` — пробрасывает исключение вызывающему коду; зритель видит сообщение об ошибке; ID карточки (`topic_card_message_id`, `topic_media_message_ids`) записываются только после успешной отправки
  - `topics.update_submission_card` / `topics.finalize_submission_card` — best-effort операции; логируют warning; не ломают основной flow
  - `topics.delete_submission_card` очищает `topic_card_message_id` / `topic_media_message_ids` только если все сообщения реально удалены; при частичном сбое IDs сохраняются для повторной попытки и диагностики
- **Транзакции:** DbSessionMiddleware делает auto-rollback при `BaseException` (включая `CancelledError`)
- **Публикация:** разделена на критическую фазу (DB commit) и best-effort фазы (финализация карточки в теме форума, уведомление зрителя)
- **Идемпотентность:** `publish_post()` проверяет `published_at` перед повторной публикацией
- **Устаревшие scheduler jobs:** если publication уже удалена или submission больше не в `scheduled`, job логируется как пропущенная и ничего не публикует
- **Компенсация после partial failure:** при неудачной записи результата публикации в БД publisher пытается удалить уже отправленные сообщения из канала; если это тоже не удалось, логируется ошибка неоднозначного состояния и нужен ручной разбор
- **Необработанные исключения:** перехватываются `sys.excepthook` и пишутся в лог с уровнем CRITICAL
