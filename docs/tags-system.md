# tg-submission-bot Tags System

## General principles

**Tags and the caption are independent entities.** Tags are stored in `submissions.tags` (a JSON array of strings without `#`), the caption is in `submissions.caption`.

### Source of tags
Tags are **not extracted** from the viewer's caption. `submissions.tags` is empty when a post is created. Tags are added exclusively by a moderator through a 4-page wizard.

### Independence
- Editing the caption **does not change** the tags
- Editing the tags **does not change** the caption
- Length validation accounts for the combined length of `tags + description`: up to 1024 characters for a media post and up to 4096 for text-only (length is counted as plain text, HTML tags are stripped before counting — the same way Telegram counts length)

---

## Publication format

```
#MineShieldArt | #MineShield4 | #МайнШилд4 | #Nerkin | #Leosha

Post description...
```

Tags always on top, joined by ` | `, with `#`. The description below, separated by a double line break. The `compose_caption()` function assembles the final text.

---

## Tag sections and presets (`tag_preset_sections`, `tag_presets`)

The runtime source of presets is the pair of tables `tag_preset_sections` and `tag_presets` in PostgreSQL.

### `tag_preset_sections`
- `key` — internal section key (`category`, `setting`, `character`, `section_N`, ...)
- `label` — section name in the wizard / management UI
- `columns` — inline button layout on the wizard page
- `sort_order` — order of pages and of the listing in the management UI

### `tag_presets`
- `preset_type` — reference to `tag_preset_sections.key`
- `label` — button text in the wizard / management UI
- `tag` — the value, without `#`, that ends up in `submissions.tags`
- `sort_order` — display order within the section

Initial values are seeded by a migration from the previous fixed schema, but after that migration the wizard and the management UI work exclusively through the DB.

### Preset management

The moderator's `Управление` ("Management") screen provides full CRUD for both the sections themselves and the presets within them.

Management UI behavior:
- the `Пресеты тегов` ("Tag Presets") screen shows all sections from `tag_preset_sections`
- a new section is created by name; the internal `key` is generated automatically (`section_N`)
- a new preset is created in a single step: you can send either `tag`, or `label | tag`
- deleting a section cascades deletion of its presets from the reference table, but does not rewrite already-saved `submissions.tags`

Deleting or renaming a preset **does not rewrite** already-saved `submissions.tags`. If an old post contains a tag that's no longer in the reference table, the next time the wizard is opened, that tag lands in `wizard_custom`. The same rule applies to a section that was deleted entirely.

---

## Tag editing wizard

The wizard structure is now dynamic:

1. Each section from `tag_preset_sections` gets its own selection page
2. After the last section there's always a **Custom** page

Each page shows a live preview of the final post (tags + description). On re-entry, previously selected presets are recognized against the current DB data.

Edge-case rules:
- if a preset was deleted after an old post was saved, its value lands in `wizard_custom`
- if a section or preset is deleted while the wizard is already open, the selected values are not lost: they are automatically moved into `wizard_custom`
- if there are no sections at all, the wizard opens directly on the custom tags page

All inline edits go through a single `_render_wizard_message()`, which re-reads the current sections/presets from the DB, safely handles `message is not modified`, and reuses `wizard_message_id`.

---

## Callback Data Factories

| Factory | Prefix | Fields | Purpose |
|---------|--------|------|-----------|
| SubmissionCB | sub | action, sub_id | Post actions (edit_caption, edit_tags, schedule, unschedule, publish_now, reject, reject_silent, ban_author, contact, close) |
| TagWizardCB | tagwiz | action, value | Tag wizard (`toggle`, `page_next`, `page_prev`, `back_presets`, `custom_finish`) |
| CalendarCB | cal | action, year, month, day | Date picker; action: day, prev_month, next_month, ignore, back_to_cal, back_to_hours |
| TimeCB | time | hour, minute | Time picker |
| ConfirmCB | confirm | action | Confirmation (yes, no, back) |
| ViewerCancelCB | vcancel | sub_id | Viewer cancels a post |
| ContactCB | contact | action, sub_id | Moderator ↔ viewer contact |
| UnbanCB | unban | user_id | Unbanning a user from the management UI |
| ManagementCB | mgmt | action | Moderator home screen and global management sections |
| MediaCB | media | action, sub_id, media_id | Media manager: adding/deleting/finishing media edits on a post |
| TagPresetCB | tpreset | action, preset_type, preset_id | CRUD on tag sections and presets in the management UI |
