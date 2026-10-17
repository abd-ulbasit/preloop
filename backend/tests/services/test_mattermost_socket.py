"""Real local TLS HTTP/WebSocket provider with synthetic PostgreSQL fixtures."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import ssl
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from preloop.models import models
from preloop.models.crud import crud_chat
from preloop.services.mattermost_listener import listen_connection
from preloop.utils.encryption import encrypt_value


@pytest.mark.asyncio
async def test_tls_socket_deduplicates_canonical_dm_and_stops_on_disable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = os.getenv("CHAT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires an isolated migrated CHAT_TEST_DATABASE_URL")
    from preloop.config import settings

    monkeypatch.setattr(settings, "model_endpoint_allowed_cidrs", "127.0.0.0/8")
    monkeypatch.setattr(
        "preloop.services.ai_model_provider._resolve_endpoint_host",
        lambda _: [ipaddress.ip_address("127.0.0.1")],
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "chat.example.com")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("chat.example.com")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = (
        tmp_path / "synthetic-cert.pem",
        tmp_path / "synthetic-key.pem",
    )
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_file))
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_file, key_file)
    engine = create_engine(url)
    sockets = []
    created = 0
    stop = asyncio.Event()
    tasks = []

    async def process(connection, request):
        path = request.path
        if path == "/api/v4/websocket":
            return None
        if "/posts/" in path:
            value = {
                "id": path.rsplit("/", 1)[-1],
                "user_id": "actor",
                "channel_id": "dm",
                "create_at": created if not path.endswith("/old") else created - 60000,
                "message": "link synthetic-proof",
            }
        elif path.endswith("/users/actor"):
            value = {"id": "actor", "is_bot": False}
        elif path.endswith("/channels/dm"):
            value = {"id": "dm", "type": "D", "team_id": ""}
        elif "/channels/dm/members/" in path:
            value = {"user_id": path.rsplit("/", 1)[-1], "channel_id": "dm"}
        else:
            value = {"user_id": path.rsplit("/", 1)[-1], "team_id": "team"}
        body = json.dumps(value).encode()
        return Response(
            200,
            "OK",
            Headers(
                {
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "Connection": "close",
                }
            ),
            body,
        )

    async def handler(socket):
        nonlocal created
        sockets.append(socket)
        challenge = json.loads(await socket.recv())
        assert challenge["action"] == "authentication_challenge"
        await socket.send(json.dumps({"event": "hello"}))
        await socket.send(json.dumps({"seq_reply": 1, "status": "OK"}))
        await asyncio.sleep(0.05)
        created = int(time.time() * 1000)
        # These identity fields are deliberately false; only the ID is a lookup hint.
        for post_id in ["old", "new", "new"]:
            await socket.send(
                json.dumps(
                    {
                        "event": "posted",
                        "data": {
                            "post": json.dumps(
                                {
                                    "id": post_id,
                                    "user_id": "forged",
                                    "channel_id": "forged",
                                }
                            )
                        },
                    }
                )
            )
        await socket.wait_closed()

    with Session(engine) as db:
        account = models.Account(organization_name="Synthetic TLS provider")
        db.add(account)
        db.commit()
        account_id = account.id
    try:
        async with serve(
            handler, "127.0.0.1", 0, ssl=tls, process_request=process
        ) as server:
            port = server.sockets[0].getsockname()[1]
            with Session(engine) as db:
                connection = crud_chat.connections.create(
                    db,
                    obj_in={
                        "account_id": account_id,
                        "provider": "mattermost",
                        "workspace_id": "team",
                        "name": "Synthetic TLS",
                        "credentials_encrypted": encrypt_value(
                            json.dumps(
                                {
                                    "verification_secret": "",
                                    "bot_token": "synthetic-token",
                                    "bot_user_id": "bot",
                                    "base_url": f"https://chat.example.com:{port}",
                                }
                            )
                        ),
                    },
                )
                connection_id = connection.id

            def factory():
                return sessionmaker(bind=engine)

            tasks = [
                asyncio.create_task(
                    listen_connection(connection_id, stop, session_factory=factory)
                )
                for _ in range(2)
            ]

            async def received():
                for _ in range(100):
                    with Session(engine) as db:
                        rows = crud_chat.work.get_multi(db, connection_id=connection_id)
                        if rows:
                            assert len(rows) == 1
                            assert rows[0].event_id == "new"
                            assert rows[0].external_user_id == "actor"
                            assert rows[0].payload["text"] == "/link"
                            return
                    await asyncio.sleep(0.05)
                raise AssertionError("Synthetic DM was not enqueued")

            await received()
            assert len(sockets) == 1
            # A reconnect cannot replay an old command.
            await sockets[0].close()
            await asyncio.sleep(1.5)
            with Session(engine) as db:
                assert (
                    len(crud_chat.work.get_multi(db, connection_id=connection_id)) == 1
                )
                row = crud_chat.connections.get(db, connection_id)
                crud_chat.connections.update(db, db_obj=row, obj_in={"enabled": False})
            for socket in sockets:
                if socket.state.name == "OPEN":
                    await socket.send(json.dumps({"event": "hello"}))
            await asyncio.sleep(0.2)
            with Session(engine) as db:
                assert (
                    len(crud_chat.work.get_multi(db, connection_id=connection_id)) == 1
                )
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with Session(engine) as db:
            db.query(models.Account).filter_by(id=account_id).delete()
            db.commit()
        engine.dispose()
