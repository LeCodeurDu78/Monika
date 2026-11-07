"""Reminders: a moment said in French becomes a datetime, or a clear refusal."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from agent.tasks import TasksError, parse_when

NOW = datetime(2026, 11, 6, 10, 0)


def test_delay_is_read_from_words() -> None:
    when = parse_when("dans 20 minutes", now=NOW)

    assert when.at == NOW + timedelta(minutes=20)
    assert when.every_s is None


def test_a_passed_hour_means_tomorrow() -> None:
    assert parse_when("8h", now=NOW).at == datetime(2026, 11, 7, 8, 0)


def test_recurring_every_two_hours() -> None:
    when = parse_when("toutes les 2 heures", now=NOW)

    assert when.every_s == 2 * 3600
    assert when.at == NOW + timedelta(hours=2)


def test_a_weekday_keeps_its_fixed_time() -> None:
    when = parse_when("chaque lundi à 9h", now=NOW)

    assert when.daily_at == "09:00"
    assert when.days == (0,)
    assert when.at == datetime(2026, 11, 9, 9, 0)


def test_nonsense_is_refused() -> None:
    with pytest.raises(TasksError):
        parse_when("quand tu veux", now=NOW)
