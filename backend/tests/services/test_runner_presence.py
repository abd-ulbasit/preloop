"""Presence derivation table (#1506)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from preloop.services.runner_presence import (
    IDLE_AFTER,
    OFFLINE_AFTER,
    derive_presence,
    derive_state,
    parse_idle_seconds,
)

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
FRESH = NOW - timedelta(seconds=10)
STALE = NOW - OFFLINE_AFTER - timedelta(seconds=1)
IDLE = int(IDLE_AFTER.total_seconds())


def _runner(**overrides):
    values = dict(
        status="online",
        last_heartbeat=FRESH,
        sleep_signaled_at=None,
        os_idle_seconds=None,
        free_slots=1,
        presence_state=None,
        presence_since=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({}, "online"),
        ({"free_slots": 0, "status": "busy"}, "busy"),
        ({"os_idle_seconds": IDLE}, "idle"),
        ({"os_idle_seconds": IDLE - 1}, "online"),
        ({"os_idle_seconds": IDLE, "free_slots": 0}, "busy"),
        ({"last_heartbeat": STALE}, "offline"),
        ({"last_heartbeat": None}, "offline"),
        ({"status": "offline"}, "offline"),
        ({"status": "offline", "sleep_signaled_at": FRESH}, "asleep"),
        ({"last_heartbeat": STALE, "sleep_signaled_at": STALE}, "asleep"),
        # Said goodbye, socket not closed yet: already asleep.
        ({"sleep_signaled_at": FRESH}, "asleep"),
        ({"last_heartbeat": NOW - OFFLINE_AFTER}, "online"),
    ],
)
def test_derive_state_table(overrides, expected) -> None:
    assert derive_state(_runner(**overrides), NOW) == expected


def test_since_keeps_stored_start_for_same_state() -> None:
    started = NOW - timedelta(hours=1)
    presence = derive_presence(
        _runner(presence_state="online", presence_since=started), NOW
    )
    assert (presence.state, presence.since) == ("online", started)


def test_since_for_silent_drop_is_last_seen() -> None:
    presence = derive_presence(
        _runner(last_heartbeat=STALE, presence_state="online", presence_since=FRESH),
        NOW,
    )
    assert (presence.state, presence.since) == ("offline", STALE)


def test_since_for_asleep_is_sleep_signal() -> None:
    presence = derive_presence(_runner(status="offline", sleep_signaled_at=FRESH), NOW)
    assert (presence.state, presence.since) == ("asleep", FRESH)


@pytest.mark.parametrize(
    "raw, parsed",
    [(0, 0), (12, 12), (3.7, 3), (-1, None), (True, None), ("5", None), (None, None)],
)
def test_parse_idle_seconds(raw, parsed) -> None:
    assert parse_idle_seconds(raw) == parsed
