"""Runner presence derived from heartbeats (#1506).

Every threshold lives here. The server is the only place presence is
computed; the console and the session picker display what it returns.

States:

* ``online``: heartbeats are fresh and the runner has a free slot.
* ``busy``: heartbeats are fresh and every slot is taken.
* ``idle``: heartbeats are fresh, a slot is free, and the runner reported
  OS idle time of at least ``IDLE_AFTER``.
* ``asleep``: heartbeats stopped (or the socket closed) after the runner
  said ``going_to_sleep``.
* ``offline``: heartbeats stopped without a sleep signal.

Routing (``find_matching`` and ``claim_free_slot``) skips ``asleep`` and
``offline`` runners in SQL with the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Optional

from preloop.models.crud.flow_runner import ONLINE_HEARTBEAT_TTL
from preloop.models.schemas.flow_runner import RunnerPresenceState

#: Interval the runner CLI sends heartbeats at.
HEARTBEAT_INTERVAL = timedelta(seconds=15)
#: No heartbeat for this long means the runner is gone (asleep or offline).
OFFLINE_AFTER = ONLINE_HEARTBEAT_TTL
#: Reported OS idle time at or above this marks a reachable runner ``idle``.
IDLE_AFTER = timedelta(minutes=5)

PRESENCE_ONLINE: Final = "online"
PRESENCE_BUSY: Final = "busy"
PRESENCE_IDLE: Final = "idle"
PRESENCE_ASLEEP: Final = "asleep"
PRESENCE_OFFLINE: Final = "offline"
PRESENCE_STATES: Final = (
    PRESENCE_ONLINE,
    PRESENCE_BUSY,
    PRESENCE_IDLE,
    PRESENCE_ASLEEP,
    PRESENCE_OFFLINE,
)


@dataclass(frozen=True)
class Presence:
    state: RunnerPresenceState
    since: Optional[datetime]


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def parse_idle_seconds(value: Any) -> Optional[int]:
    """Accept a non-negative integer (or float) idle time; anything else is None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0 or value != value:  # negative or NaN
        return None
    return int(min(value, 10**9))


def derive_state(runner: Any, now: Optional[datetime] = None) -> RunnerPresenceState:
    """Presence state for ``runner`` at ``now`` from its stored signals."""
    now = now or datetime.now(timezone.utc)
    heartbeat = _aware(getattr(runner, "last_heartbeat", None))
    connected = (
        getattr(runner, "status", None) != "offline"
        and heartbeat is not None
        and now - heartbeat <= OFFLINE_AFTER
    )
    if not connected:
        if _aware(getattr(runner, "sleep_signaled_at", None)) is not None:
            return PRESENCE_ASLEEP
        return PRESENCE_OFFLINE
    if _aware(getattr(runner, "sleep_signaled_at", None)) is not None:
        # Said goodbye but the socket has not dropped yet: do not route to it.
        return PRESENCE_ASLEEP
    free_slots = getattr(runner, "free_slots", 1)
    if isinstance(free_slots, int) and free_slots <= 0:
        return PRESENCE_BUSY
    idle = getattr(runner, "os_idle_seconds", None)
    if isinstance(idle, int) and idle >= IDLE_AFTER.total_seconds():
        return PRESENCE_IDLE
    return PRESENCE_ONLINE


def derive_presence(runner: Any, now: Optional[datetime] = None) -> Presence:
    """State plus when it began.

    When the derived state matches the stored one, its stored start time is
    kept. A runner that went quiet without the socket closing (pulled
    network) has no stored transition yet; its ``since`` is the last
    heartbeat, which is when it was last seen.
    """
    now = now or datetime.now(timezone.utc)
    state = derive_state(runner, now)
    stored_state = getattr(runner, "presence_state", None)
    stored_since = _aware(getattr(runner, "presence_since", None))
    if stored_state == state and stored_since is not None:
        return Presence(state, stored_since)
    if state == PRESENCE_ASLEEP:
        return Presence(state, _aware(getattr(runner, "sleep_signaled_at", None)))
    if state == PRESENCE_OFFLINE:
        heartbeat = _aware(getattr(runner, "last_heartbeat", None))
        return Presence(state, heartbeat or stored_since)
    return Presence(state, now)
