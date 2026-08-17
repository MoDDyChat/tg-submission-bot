from __future__ import annotations

from db import session


def test_server_settings_bound_lock_wait_and_stall_timeouts() -> None:
    assert session._SERVER_SETTINGS == {
        "statement_timeout": "30000",
        "idle_in_transaction_session_timeout": "60000",
        "lock_timeout": "5000",
    }


def test_command_timeout_matches_the_server_side_budget() -> None:
    assert session._COMMAND_TIMEOUT == 30.0
