# Testing — tg-submission-bot

## Инструменты

- **pytest** — основной фреймворк
- **pytest-asyncio** (`asyncio_mode = "auto"`) — async тесты
- **pytest-cov** — coverage
- **SQLAlchemy async** — реальная PostgreSQL для integration тестов (без моков БД)

## Запуск тестов

```bash
# Быстрые тесты (без integration)
pytest tests/ --ignore=tests/integration

# Integration тесты (требуют TEST_DATABASE_URL)
TEST_DATABASE_URL=postgresql+asyncpg://... pytest tests/integration/

# Все тесты с coverage
pytest --cov=src --cov-report=term-missing
```

## Конфигурация

`pytest.ini` / `pyproject.toml` — `asyncio_mode = "auto"`, `testpaths = ["tests"]`.

CI: `.github/workflows/pytest.yml` запускает fast suite + integration при наличии `TEST_DATABASE_URL` secret.

## Конвенции

- Integration тесты НЕ используют моки БД — только реальный PostgreSQL
- `tests/conftest.py` — глобальные фикстуры (bot, config, session factory)
- `tests/integration/conftest.py` — фикстуры с реальной БД
- `tests/helpers.py` — вспомогательные функции (создание пользователей, submissions и т.д.)

## Валидационная матрица

| Тип изменения | Scope валидации |
|---|---|
| Логика (без схем) | unit тесты затронутого модуля + ruff/mypy |
| Схема / модель | unit + integration |
| Alembic миграция | integration/test_migrations.py + risk gate |
| Конфиг / .env | manual review (risk gate) |
| Только docs | markdown lint |
| Publisher / scheduler | integration + ручная проверка в staging |
