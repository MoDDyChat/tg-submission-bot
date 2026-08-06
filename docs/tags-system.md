# Система тегов tg-submission-bot

## Общие принципы

**Теги и описание — независимые сущности.** Теги хранятся в `submissions.tags` (JSON-массив строк без `#`), описание — в `submissions.caption`.

### Источник тегов
Теги **не извлекаются** из описания зрителя. `submissions.tags` при создании поста пуст. Теги добавляются исключительно модератором через 4-страничный визард.

### Независимость
- Редактирование описания **не меняет** теги
- Редактирование тегов **не меняет** описание
- Валидация длины учитывает итоговую длину `tags + description`: до 1024 символов для медиапоста и до 4096 для text-only (длина считается по plain text, HTML-теги стрипаются перед подсчётом — аналогично тому, как Telegram считает длину)

---

## Формат публикации

```
#MineShieldArt | #MineShield4 | #МайнШилд4 | #Nerkin | #Leosha

Описание поста...
```

Теги всегда сверху, через ` | `, с `#`. Описание ниже через двойной перенос. Функция `compose_caption()` собирает финальный текст.

---

## Разделы и пресеты тегов (`tag_preset_sections`, `tag_presets`)

Runtime-источник пресетов — связка таблиц `tag_preset_sections` и `tag_presets` в PostgreSQL.

### `tag_preset_sections`
- `key` — внутренний ключ раздела (`category`, `setting`, `character`, `section_N`, ...)
- `label` — название раздела в wizard / management UI
- `columns` — раскладка inline-кнопок на странице wizard
- `sort_order` — порядок страниц и вывода в management UI

### `tag_presets`
- `preset_type` — ссылка на `tag_preset_sections.key`
- `label` — текст кнопки в wizard / management UI
- `tag` — значение, которое попадёт в `submissions.tags` без `#`
- `sort_order` — порядок вывода внутри раздела

Стартовые значения сидируются миграцией из прежней фиксированной схемы, но после миграции wizard и management UI работают только через БД.

### Управление пресетами

В `Управлении` модератора доступен полный CRUD и для самих разделов, и для пресетов внутри них.

Поведение management UI:
- экран `Пресеты тегов` показывает все разделы из `tag_preset_sections`
- новый раздел создаётся по названию; внутренний `key` генерируется автоматически (`section_N`)
- новый пресет создаётся в один шаг: можно отправить либо `тег`, либо `label | tag`
- удаление раздела каскадно удаляет его пресеты из справочника, но не переписывает уже сохранённые `submissions.tags`

Удаление или переименование пресета **не переписывает** уже сохранённые `submissions.tags`. Если старый пост содержит тег, которого больше нет в справочнике, при следующем открытии wizard этот тег попадёт в `wizard_custom`. То же правило действует для целиком удалённого раздела.

---

## Визард редактирования тегов

Структура wizard теперь динамическая:

1. Для каждого раздела из `tag_preset_sections` создаётся отдельная страница выбора
2. После последнего раздела всегда идёт страница **Кастомные**

Каждая страница отображает живой предпросмотр финального поста (теги + описание). При повторном входе ранее выбранные пресеты распознаются по актуальным данным из БД.

Edge-case правила:
- если пресет удалили после сохранения старого поста, его значение попадёт в `wizard_custom`
- если раздел или пресет удалили, пока wizard уже открыт, выбранные значения не пропадут: они автоматически переносятся в `wizard_custom`
- если разделов нет вообще, wizard сразу открывает страницу custom tags

Все inline-редактирования идут через единый `_render_wizard_message()`, который перечитывает актуальные разделы/пресеты из БД, безопасно обрабатывает `message is not modified` и переиспользует `wizard_message_id`.

---

## Callback Data Factories

| Factory | Prefix | Поля | Назначение |
|---------|--------|------|-----------|
| SubmissionCB | sub | action, sub_id | Действия с постом (edit_caption, edit_tags, schedule, unschedule, publish_now, reject, reject_silent, ban_author, contact, close) |
| TagWizardCB | tagwiz | action, value | Визард тегов (`toggle`, `page_next`, `page_prev`, `back_presets`, `custom_finish`) |
| CalendarCB | cal | action, year, month, day | Выбор даты; action: day, prev_month, next_month, ignore, back_to_cal, back_to_hours |
| TimeCB | time | hour, minute | Выбор времени |
| ConfirmCB | confirm | action | Подтверждение (yes, no, back) |
| ViewerCancelCB | vcancel | sub_id | Отмена поста зрителем |
| ContactCB | contact | action, sub_id | Связь модератор ↔ зритель |
| UnbanCB | unban | user_id | Разблокировка пользователя из management UI |
| ManagementCB | mgmt | action | Домашний экран модератора и глобальные разделы управления |
| MediaCB | media | action, sub_id, media_id | Менеджер медиа: добавление/удаление/завершение редактирования медиа в посте |
| TagPresetCB | tpreset | action, preset_type, preset_id | CRUD разделов и пресетов тегов в management UI |
