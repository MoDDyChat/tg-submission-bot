from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("BOT__TOKEN", "123456:TEST")
os.environ.setdefault("MODERATOR_IDS", "1,2")
os.environ.setdefault("CHANNEL_ID", "-100111")
os.environ.setdefault("MODERATOR_GROUP_ID", "-100333")
os.environ.setdefault("TIMEZONE", "Europe/Moscow")
os.environ.setdefault("ADMIN_IDS", "")


@pytest.fixture(autouse=True)
def _reset_runtime_state() -> None:
    yield

    intake_module = sys.modules.get("services.submission_intake")
    if intake_module is not None:
        intake_module.reset_media_group_buffers()

    append_module = sys.modules.get("services.media_append")
    if append_module is not None:
        append_module.reset_append_buffers()

    # The Recover guard is a module-level singleton lazily bound to the loop that
    # first takes it. pytest-asyncio gives every test its own loop, so a lock
    # left over from a previous test would raise "bound to a different event
    # loop" in the next one.
    management_module = sys.modules.get("handlers.moderator.management")
    if management_module is not None:
        task = getattr(management_module, "_recover_task", None)
        if task is not None and not task.done():
            task.cancel()
        management_module._recover_lock = None
        management_module._recover_task = None

    scheduler_module = sys.modules.get("services.scheduler")
    if scheduler_module is not None:
        sched = getattr(scheduler_module, "scheduler", None)
        if sched is not None:
            try:
                sched.remove_all_jobs()
            except Exception:
                pass
