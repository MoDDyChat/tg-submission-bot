"""Tests for core.bot._redact_url: the Redis password must not reach the logs."""

from __future__ import annotations

import pytest

from core.bot import _redact_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("redis://sm_tg_redis:6379/0", "redis://sm_tg_redis:6379/0"),
        ("redis://:s3cret@sm_tg_redis:6379/0", "redis://:***@sm_tg_redis:6379/0"),
        ("redis://default:s3cret@host:6379/1", "redis://default:***@host:6379/1"),
        ("redis://user@host:6379", "redis://user@host:6379"),
    ],
)
def test_redact_url(url: str, expected: str) -> None:
    assert _redact_url(url) == expected
    assert "s3cret" not in _redact_url(url)
