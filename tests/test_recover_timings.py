import inspect
from datetime import timedelta
from unittest.mock import Mock


def test_cardless_recovery_default_min_age_is_two_minutes() -> None:
    from db.queries.submissions import list_active_submissions_without_card

    # Суммарное худшее время intake после коммита поста может превышать min_age,
    # поэтому основная защита — in-flight guard; min_age страхует только случай
    # оборванного процесса.
    default = inspect.signature(list_active_submissions_without_card).parameters[
        "min_age"
    ].default

    assert default == timedelta(minutes=2)


def test_topic_cards_recover_job_uses_one_minute_interval(monkeypatch) -> None:
    import core.bot as bot_mod
    import services.scheduler as scheduler_mod

    scheduler_mock = Mock()
    monkeypatch.setattr(scheduler_mod, "scheduler", scheduler_mock)

    bot_mod._register_scheduled_jobs(Mock(), Mock())

    calls_by_id = {
        call.kwargs["id"]: call for call in scheduler_mock.add_job.call_args_list
    }
    call = calls_by_id["topic_cards_recover"]

    assert call.args[1] == "interval"
    assert call.kwargs["minutes"] == 1
    assert call.kwargs["max_instances"] == 1
