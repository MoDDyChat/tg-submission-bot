from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, PostgresDsn, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, EnvSettingsSource, PydanticBaseSettingsSource, SettingsConfigDict


class BotSettings(BaseModel):
    token: str


class DBSettings(BaseModel):
    host: str = "postgres"
    port: int = 5432
    name: str = "tg_submission_bot"
    user: str = "postgres"
    password: str = ""

    @property
    def url(self) -> str:
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.user,
                password=self.password,
                host=self.host,
                port=self.port,
                path=self.name,
            )
        )


class Config(BaseSettings):
    bot: BotSettings
    db: DBSettings = DBSettings()
    # Список ID модераторов (через запятую: MODERATOR_IDS=123,456).
    # Админы попадают сюда автоматически — дублировать их не нужно.
    moderator_ids: list[int] = []
    channel_id: int
    # Группа-форум для модерации (MODERATOR_GROUP_ID=-100xxxxxxxxxx)
    moderator_group_id: int
    # ID администраторов (ADMIN_IDS=123,456). Админ — модератор с
    # дополнительными правами, отдельно в MODERATOR_IDS его писать не нужно.
    admin_ids: list[int] = []
    # TTL блокировки редактирования (секунды), по умолчанию 5 минут
    edit_lock_ttl_seconds: int = 300
    # Максимальная длина названия темы в форуме (ограничение Telegram API)
    topic_title_max_length: int = 128
    timezone: str = "Europe/Moscow"
    log_level: str = "INFO"
    # Redis URL для FSM Storage; если не задан — используется MemoryStorage
    redis_url: str | None = None
    # SOCKS5/HTTP прокси для подключения к Telegram API (опционально)
    # Формат: socks5://user:pass@host:port  или  http://user:pass@host:port
    proxy_url: str | None = None
    # Путь к JSON-файлу конфигурации статусов тем (лейблы + иконки)
    topic_statuses_path: str = "config/topic_statuses.json"
    # Путь к YAML-файлу с текстами бота (переопределяет core.messages._DEFAULTS).
    # При отсутствии файла или отдельных ключей используются встроенные значения.
    messages_path: str = "config/messages.yaml"
    # Путь к текстовому файлу с правилами предложки (текст /start для вьюеров).
    # У каждого инстанса свой файл (задаётся через RULES_PATH в .env). HTML.
    # При отсутствии файла используется встроенный текст core.messages.RULES.
    rules_path: str = "config/rules.txt"
    # Параметры троттлинга запросов (ThrottleMiddleware)
    throttle_rate: int = 20
    throttle_period: float = 10.0
    # HTTP-сервер health/metrics (docker healthcheck + скрейп vmagent)
    api_host: str = "0.0.0.0"
    api_port: int = 5400
    # Автоприменение Alembic-миграций при старте (DB_AUTO_MIGRATE=false —
    # только проверка: если схема отстаёт от head, бот падает на старте)
    db_auto_migrate: bool = True
    # Таймаут прогона миграций в секундах
    db_migrate_timeout: float = 300.0
    # Тихие уведомления в группе модераторов: карточки, доска очереди и ответы
    # хендлеров уходят с disable_notification=True (без пуш-пинга).
    # SILENT_MODERATOR_NOTIFICATIONS=false — вернуть звук.
    silent_moderator_notifications: bool = True
    # Режим парсинга хэштегов из описания поста (TAG_PARSING_MODE=off|suggest|auto):
    # off — не парсить, suggest — сохранять в suggested_tags (решает модератор),
    # auto — точные совпадения с пресетами дополнительно писать в tags.
    tag_parsing_mode: Literal["off", "suggest", "auto"] = "suggest"
    # Вырезать из caption «голые» строки, целиком состоящие из хэштегов
    # (TAG_PARSING_STRIP_FROM_CAPTION=true)
    tag_parsing_strip_from_caption: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def moderator_id(self) -> int:
        """Первый ID модератора — для обратной совместимости."""
        return self.moderator_ids[0]

    @field_validator("moderator_ids", mode="before")
    @classmethod
    def parse_moderator_ids(cls, v: object) -> list[int]:
        if isinstance(v, str):
            ids = [int(x.strip()) for x in v.split(",") if x.strip()]
        elif isinstance(v, int):
            ids = [v]
        else:
            ids = [int(x) for x in v]  # type: ignore[union-attr]
        if any(i == 0 for i in ids):
            raise ValueError("Telegram ID модератора не может быть равен 0")
        return ids

    @field_validator("channel_id", "moderator_group_id")
    @classmethod
    def validate_telegram_ids(cls, v: int) -> int:
        if v == 0:
            raise ValueError("Telegram ID не может быть равен 0")
        if v > 0:
            raise ValueError(
                "channel_id и moderator_group_id должны быть отрицательными (супергруппы/каналы)"
            )
        return v

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, KeyError):
            raise ValueError(f"Невалидная timezone: {v}")
        return v

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, v: object) -> list[int]:
        if v is None:
            return []
        if isinstance(v, str):
            ids = [int(x.strip()) for x in v.split(",") if x.strip()]
        elif isinstance(v, int):
            ids = [v]
        else:
            ids = [int(x) for x in v]  # type: ignore[union-attr]
        if any(i == 0 for i in ids):
            raise ValueError("Telegram ID администратора не может быть равен 0")
        return ids

    @model_validator(mode="after")
    def merge_admins_into_moderators(self) -> "Config":
        """Админ — модератор по определению: права модератора он получает
        автоматически, повторять его ID в MODERATOR_IDS не требуется."""
        known = set(self.moderator_ids)
        extra: list[int] = []
        for admin_id in self.admin_ids:
            if admin_id not in known:
                known.add(admin_id)
                extra.append(admin_id)
        self.moderator_ids = self.moderator_ids + extra
        if not self.moderator_ids:
            raise ValueError("Нужен хотя бы один ID в MODERATOR_IDS или ADMIN_IDS")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        **kwargs: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Use a custom env source that passes comma-separated list fields as raw
        # strings so that @field_validator(mode="before") can split them — newer
        # pydantic-settings tries json.loads first and fails on "1,2,3" syntax.
        sources = super().settings_customise_sources(settings_cls, **kwargs)
        return tuple(
            _CommaSeparatedEnvSource(settings_cls) if isinstance(s, EnvSettingsSource) else s
            for s in sources
        )


class _CommaSeparatedEnvSource(EnvSettingsSource):
    _comma_fields: frozenset[str] = frozenset({"moderator_ids", "admin_ids"})

    def prepare_field_value(
        self,
        field_name: str,
        field: object,
        value: object,
        value_is_complex: bool,
    ) -> object:
        if field_name in self._comma_fields and isinstance(value, str):
            return value  # let @field_validator(mode="before") handle splitting
        return super().prepare_field_value(field_name, field, value, value_is_complex)


config = Config()
