# Testing — tg-submission-bot

## Tools

- **pytest** — primary framework
- **pytest-asyncio** (`asyncio_mode = "auto"`) — async tests
- **pytest-cov** — coverage
- **SQLAlchemy async** — real PostgreSQL for integration tests (no DB mocks)

## Running tests

```bash
# Fast tests (excluding integration)
pytest tests/ --ignore=tests/integration

# Integration tests (require TEST_DATABASE_URL)
TEST_DATABASE_URL=postgresql+asyncpg://... pytest tests/integration/

# All tests with coverage
pytest --cov=src --cov-report=term-missing
```

## Configuration

`pytest.ini` / `pyproject.toml` — `asyncio_mode = "auto"`, `testpaths = ["tests"]`.

CI: `.github/workflows/pytest.yml` runs the fast suite + integration when the `TEST_DATABASE_URL` secret is present.

## Conventions

- Integration tests do NOT use DB mocks — real PostgreSQL only
- `tests/conftest.py` — global fixtures (bot, config, session factory)
- `tests/integration/conftest.py` — fixtures with a real DB
- `tests/helpers.py` — helper functions (creating users, submissions, etc.)

## Validation matrix

| Change type | Validation scope |
|---|---|
| Logic (no schema changes) | unit tests for the affected module + ruff/mypy |
| Schema / model | unit + integration |
| Alembic migration | integration/test_migrations.py + risk gate |
| Config / .env | manual review (risk gate) |
| Docs only | markdown lint |
| Publisher / scheduler | integration + manual check in staging |
