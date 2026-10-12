"""Unit tests for the desktop view broker core (#870)."""

from __future__ import annotations

import asyncio
import struct
from datetime import datetime, timedelta

import pytest

from preloop.services import desktop_broker as broker

HANDSHAKE = b"RFB 003.008\n" + b"\x01" + b"\x01"


def set_pixel_format() -> bytes:
    return b"\x00" + b"\x00" * 19


def set_encodings(*encodings: int) -> bytes:
    return struct.pack(">BxH", 2, len(encodings)) + b"".join(
        struct.pack(">i", e) for e in encodings
    )


def fb_update_request() -> bytes:
    return struct.pack(">BBHHHH", 3, 1, 0, 0, 640, 480)


def key_event() -> bytes:
    return struct.pack(">BBxxI", 4, 1, 0x61)


def pointer_event() -> bytes:
    return struct.pack(">BBHH", 5, 1, 10, 10)


def cut_text(text: bytes) -> bytes:
    return struct.pack(">Bxxxi", 6, len(text)) + text


def fence() -> bytes:
    return struct.pack(">BxxxIB", 248, 0, 2) + b"ab"


VIEW_STREAM = set_pixel_format() + set_encodings(0, -223) + fb_update_request()
INPUT_STREAM = key_event() + pointer_event() + cut_text(b"secret clipboard")


class TestRfbClientFilter:
    def test_view_only_drops_key_pointer_and_cut_text(self) -> None:
        f = broker.RfbClientFilter()
        out = f.feed(HANDSHAKE + VIEW_STREAM + INPUT_STREAM, allow_input=False)
        assert out == HANDSHAKE + VIEW_STREAM
        assert dict(f.dropped) == {4: 1, 5: 1, 6: 1}

    def test_control_mode_passes_input(self) -> None:
        f = broker.RfbClientFilter()
        stream = HANDSHAKE + VIEW_STREAM + INPUT_STREAM
        assert f.feed(stream, allow_input=True) == stream

    def test_split_input_message_never_leaks(self) -> None:
        f = broker.RfbClientFilter()
        stream = HANDSHAKE + key_event() + fb_update_request()
        out = b"".join(
            f.feed(stream[i : i + 1], allow_input=False) for i in range(len(stream))
        )
        assert out == HANDSHAKE + fb_update_request()

    def test_fence_and_continuous_updates_pass(self) -> None:
        f = broker.RfbClientFilter()
        continuous = struct.pack(">BBHHHH", 150, 1, 0, 0, 10, 10)
        stream = HANDSHAKE + fence() + continuous
        assert f.feed(stream, allow_input=False) == stream

    @pytest.mark.parametrize(
        "message",
        [
            struct.pack(">BxHHBx", 251, 800, 600, 0),
            struct.pack(">BxBB", 250, 1, 2),
            struct.pack(">BBHII", 255, 0, 1, 0x61, 30),
        ],
    )
    def test_other_input_messages_dropped(self, message: bytes) -> None:
        f = broker.RfbClientFilter()
        assert f.feed(HANDSHAKE + message, allow_input=False) == HANDSHAKE

    def test_unknown_message_type_is_protocol_error(self) -> None:
        f = broker.RfbClientFilter()
        with pytest.raises(broker.RfbProtocolError):
            f.feed(HANDSHAKE + b"\x07\x00\x00\x00", allow_input=False)

    def test_security_type_other_than_none_rejected(self) -> None:
        f = broker.RfbClientFilter()
        with pytest.raises(broker.RfbProtocolError):
            f.feed(b"RFB 003.008\n\x02", allow_input=False)

    def test_rfb_33_rejected(self) -> None:
        with pytest.raises(broker.RfbProtocolError):
            broker.RfbClientFilter().feed(b"RFB 003.003\n", allow_input=False)

    def test_oversize_cut_text_rejected(self) -> None:
        f = broker.RfbClientFilter()
        header = struct.pack(">Bxxxi", 6, broker.MAX_CUT_TEXT_BYTES + 1)
        with pytest.raises(broker.RfbProtocolError):
            f.feed(HANDSHAKE + header, allow_input=False)


class TestThroughputLimiter:
    def test_paces_to_rate(self) -> None:
        clock = [0.0]
        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)
            clock[0] += seconds

        limiter = broker.ThroughputLimiter(
            rate=8 * 1024 * 1024,
            burst=1024 * 1024,
            clock=lambda: clock[0],
            sleep=fake_sleep,
        )

        async def run() -> None:
            for _ in range(17):  # 17 MiB: 1 MiB burst + 16 MiB at 8 MiB/s
                await limiter.acquire(1024 * 1024)

        asyncio.run(run())
        assert clock[0] == pytest.approx(2.0, rel=0.01)

    def test_under_rate_never_sleeps(self) -> None:
        clock = [0.0]
        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        limiter = broker.ThroughputLimiter(
            rate=1000, burst=1000, clock=lambda: clock[0], sleep=fake_sleep
        )

        async def run() -> None:
            for _ in range(5):
                clock[0] += 1.0
                await limiter.acquire(900)

        asyncio.run(run())
        assert slept == []


class TestViewToken:
    def test_token_is_32_random_bytes_and_hashed(self) -> None:
        token, token_hash = broker.issue_view_token()
        assert len(token) >= 43
        assert token not in token_hash and len(token_hash) == 64
        assert broker.view_token_matches(token, token_hash)
        assert not broker.view_token_matches(token + "x", token_hash)
        assert not broker.view_token_matches("", token_hash)
        assert not broker.view_token_matches(token, None)

    def test_tokens_differ(self) -> None:
        assert broker.issue_view_token()[0] != broker.issue_view_token()[0]


class TestStateMachine:
    def test_transitions(self) -> None:
        assert broker.can_transition("pending", "live")
        assert broker.can_transition("pending", "closed")
        assert broker.can_transition("live", "closed")
        assert not broker.can_transition("live", "pending")
        assert not broker.can_transition("closed", "live")
        assert not broker.can_transition("closed", "closed")

    def test_every_reason_has_a_close_code(self) -> None:
        assert broker.CLOSE_CODE_BY_REASON["agent_disconnected"] == 4410
        assert broker.CLOSE_CODE_BY_REASON["expired"] == 4411
        assert broker.CLOSE_CODE_BY_REASON["idle"] == 4408


def test_view_ended_payload_has_duration_and_no_secret() -> None:
    facts = broker.AuditFacts(
        view_id="v",
        endpoint_id="e",
        hostname="host.example.com",
        managed_agent_id=None,
        runner_id=None,
        runtime_session_id=None,
        mode="view_only",
    )
    live = datetime(2026, 10, 11, 12, 0, 0)
    details = broker.view_ended_details(
        facts,
        live_at=live,
        closed_at=live + timedelta(seconds=90),
        closed_reason="expired",
    )
    assert details["duration_seconds"] == 90
    assert details["closed_reason"] == "expired"
    assert not any("token" in key or "password" in key for key in details)


def test_local_bus_delivers_in_order_and_unsubscribes() -> None:
    async def run() -> list[bytes]:
        bus = broker.LocalDesktopBus()
        got: list[bytes] = []

        async def handler(data: bytes) -> None:
            got.append(data)

        unsubscribe = await bus.subscribe("s", handler)
        await broker.publish_bytes(bus, "s", b"x" * (broker.BUS_CHUNK_BYTES + 5))
        await bus.publish("s", b"end")
        await asyncio.sleep(0)
        await unsubscribe()
        await bus.publish("s", b"late")
        return got

    got = asyncio.run(run())
    assert [len(g) for g in got] == [broker.BUS_CHUNK_BYTES, 5, 3]
    assert got[-1] == b"end"


def test_local_bus_backpressures_a_stalled_subscriber() -> None:
    """A stalled viewer must slow the producer, not grow memory."""

    async def run() -> tuple[int, bool]:
        bus = broker.LocalDesktopBus()
        release = asyncio.Event()

        async def stalled(_: bytes) -> None:
            await release.wait()

        unsubscribe = await bus.subscribe("s", stalled)
        sent = 0
        blocked = False
        for _ in range(broker.LOCAL_BUS_QUEUE_SIZE * 4):
            try:
                await asyncio.wait_for(bus.publish("s", b"x"), 0.05)
            except asyncio.TimeoutError:
                blocked = True
                break
            sent += 1
        release.set()
        await unsubscribe()
        return sent, blocked

    sent, blocked = asyncio.run(run())
    assert blocked
    assert sent <= broker.LOCAL_BUS_QUEUE_SIZE + 1


class FakeNatsSub:
    def __init__(self) -> None:
        self.unsubscribed = False

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class FakeNats:
    """In-memory stand-in for a connected nats-py client."""

    def __init__(self) -> None:
        self.is_connected = True
        self.subs: list[tuple[str, object, FakeNatsSub, dict]] = []
        self.published: list[tuple[str, bytes]] = []

    async def subscribe(self, subject: str, cb=None, **kwargs):  # type: ignore[no-untyped-def]
        sub = FakeNatsSub()
        self.subs.append((subject, cb, sub, kwargs))
        return sub

    async def publish(self, subject: str, payload: bytes) -> None:
        self.published.append((subject, payload))
        for sub_subject, cb, sub, _ in list(self.subs):
            if sub_subject == subject and not sub.unsubscribed:

                class Msg:
                    data = payload

                await cb(Msg())  # type: ignore[misc]


def test_nats_bus_wires_subscribe_publish_and_unsubscribe() -> None:
    async def run() -> tuple[FakeNats, list[bytes]]:
        client = FakeNats()
        bus = broker.NatsDesktopBus(client)
        got: list[bytes] = []

        async def handler(data: bytes) -> None:
            got.append(data)

        unsubscribe = await bus.subscribe("desktop.view.v.to_viewer", handler)
        await broker.publish_bytes(bus, "desktop.view.v.to_viewer", b"rfb")
        await unsubscribe()
        await bus.publish("desktop.view.v.to_viewer", b"late")
        return client, got

    client, got = asyncio.run(run())
    assert got == [b"rfb"]
    subject, _, sub, kwargs = client.subs[0]
    assert subject == "desktop.view.v.to_viewer" and sub.unsubscribed
    assert kwargs["pending_bytes_limit"] == broker.NATS_PENDING_BYTES_LIMIT


def test_get_desktop_bus_prefers_connected_nats(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from preloop.sync.services import event_bus

    client = FakeNats()

    async def connected():  # type: ignore[no-untyped-def]
        return client

    broker.set_desktop_bus(None)
    monkeypatch.setattr(event_bus, "get_nats_client", connected)
    bus = asyncio.run(broker.get_desktop_bus())
    assert isinstance(bus, broker.NatsDesktopBus)

    client.is_connected = False
    assert asyncio.run(broker.get_desktop_bus()) is broker._local_bus

    async def missing():  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(event_bus, "get_nats_client", missing)
    assert asyncio.run(broker.get_desktop_bus()) is broker._local_bus
