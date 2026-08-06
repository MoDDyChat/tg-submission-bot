"""Topic status style configuration.

Loads label + icon settings for each submission status from
``config/topic_statuses.json`` (path configurable via ``config.topic_statuses_path``).

Usage::

    from core.topic_status_config import get_style

    style = get_style("pending")
    print(style.label)          # "📥 НОВОЕ"
    print(style.icon_color)     # 16766590
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, field_validator

import core.messages as messages

# Valid Telegram forum icon_color values (palette from Bot API docs)
VALID_ICON_COLORS = frozenset({7322096, 16766590, 13338331, 9367192, 16749490, 16478047})

_DEFAULTS: dict[str, dict] = {
  "pending":   {"label": "НОВОЕ",  "emoji": "⏳", "icon_color": 16766590, "icon_custom_emoji_id": "5357121491508928442"},
  "editing":   {"label": "РЕДАКТИРУЕТСЯ",    "emoji": "✏️", "icon_color": 7322096,  "icon_custom_emoji_id": "5238156910363950406"},
  "scheduled": {"label": "ЗАПЛАНИРОВАНО",   "emoji": "📅", "icon_color": 9367192,  "icon_custom_emoji_id": "5433614043006903194"},
  "published": {"label": "ОПУБЛИКОВАНО", "emoji": "✅", "icon_color": 13338331, "icon_custom_emoji_id": "5237699328843200968"},
  "rejected":  {"label": "ОТКЛОНЕНО",  "emoji": "❌", "icon_color": 16749490, "icon_custom_emoji_id": "5377498341074542641"},
  "cancelled": {"label": "ОТМЕНЕНО", "emoji": "🚫", "icon_color": 16478047, "icon_custom_emoji_id": "5377438129928020693"}
}


class TopicStatusStyle(BaseModel):
    label: str
    emoji: str = "❓"
    icon_color: int | None = None
    icon_custom_emoji_id: str | None = None

    @field_validator("icon_color")
    @classmethod
    def _validate_icon_color(cls, v: int | None) -> int | None:
        if v is not None and v not in VALID_ICON_COLORS:
            raise ValueError(
                f"icon_color {v!r} is not in the valid Telegram palette; "
                f"valid values: {sorted(VALID_ICON_COLORS)}"
            )
        return v


class TopicStatusConfig:
    """Registry of per-status styles, loaded from a JSON file."""

    def __init__(self, data: dict[str, dict]) -> None:
        self._styles: dict[str, TopicStatusStyle] = {}
        for key, raw in data.items():
            self._styles[key] = TopicStatusStyle(**raw)

    def get_style(self, status_key: str) -> TopicStatusStyle:
        """Return the style for *status_key*; falls back to a generic style."""
        return self._styles.get(
            status_key,
            TopicStatusStyle(label=status_key.upper()),
        )

    def all_status_keys(self) -> list[str]:
        return list(self._styles.keys())


def _load(path: str | Path | None = None) -> TopicStatusConfig:
    """Load config from *path* (JSON file), falling back to built-in defaults.

    Values in the file override the defaults for matching keys using a per-status
    deep merge: only the provided fields are overridden; unset fields keep their
    default values.
    """
    merged: dict[str, dict] = {key: dict(val) for key, val in _DEFAULTS.items()}
    if path:
        p = Path(path)
        if p.is_file():
            with p.open(encoding="utf-8") as fh:
                overrides: dict = json.load(fh)
            for status_key, override_fields in overrides.items():
                if isinstance(override_fields, dict):
                    if status_key in merged:
                        merged[status_key].update(override_fields)
                    else:
                        merged[status_key] = dict(override_fields)
    return TopicStatusConfig(merged)


# Module-level singleton — initialised lazily on first import that calls
# ``_init`` (done in ``core/config.py`` after settings are loaded).
_config_instance: TopicStatusConfig | None = None


def _init(path: str | None = None) -> None:
    """Initialise the module singleton. Called once at application start."""
    global _config_instance
    _config_instance = _load(path)


def get_style(status_key: str) -> TopicStatusStyle:
    """Return the TopicStatusStyle for *status_key*.

    Initialises from defaults if ``_init`` was not called yet.
    """
    if _config_instance is None:
        _init()
    if _config_instance is None:
        raise RuntimeError("TopicStatusConfig не инициализирован; вызовите _init() в main()")
    return _config_instance.get_style(status_key)


def build_topic_nav_legend(bot_username: str = "") -> str:
    """Render the pinned status legend from the current status config.

    Emoji and labels are read from the loaded config (defaults + JSON overrides),
    so the legend always matches the icons painted on topics and queue lines.
    Statuses without a description are skipped.

    *bot_username* turns the moderator-panel hints into a ``t.me`` deep link, so
    tapping them opens the bot's DM instead of running ``/start`` in the group.
    """
    lines = []
    for status_key in messages.TOPIC_LEGEND_STATUS_KEYS:
        description = messages.TOPIC_LEGEND_DESCRIPTIONS.get(status_key)
        if not description:
            continue
        style = get_style(status_key)
        lines.append(f"{style.emoji} [{style.label}] — {description}")
    panel_link = (
        messages.TOPIC_NAV_PANEL_LINK.format(bot_username=bot_username)
        if bot_username
        else messages.TOPIC_NAV_PANEL_PLAIN
    )
    return messages.TOPIC_NAV_LEGEND_TEMPLATE.format(
        status_lines="\n".join(lines),
        panel_link=panel_link,
    )
