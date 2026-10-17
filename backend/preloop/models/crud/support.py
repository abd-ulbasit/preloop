"""Atomic support ownership, bounded histories, quotas and durable outbox claims."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from preloop.models import models
from .base import CRUDBase


class SupportConflictError(ValueError):
    """Conversation closed, expired, full or ownership changed."""


class CRUDSupport:
    conversations = CRUDBase(models.SupportConversation)
    messages = CRUDBase(models.SupportMessage)
    outbox = CRUDBase(models.SupportOutbox)

    def _lock(
        self, db: Session, conversation_id: uuid.UUID
    ) -> models.SupportConversation:
        row = (
            db.query(models.SupportConversation)
            .filter_by(id=conversation_id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if row is None or row.expires_at <= datetime.utcnow():
            raise SupportConflictError("Conversation expired")
        return row

    def _message(
        self,
        db: Session,
        row: models.SupportConversation,
        *,
        event_id: str,
        role: str,
        content: str,
        citations: list | None = None,
        staged: bool = False,
    ) -> models.SupportMessage:
        message = models.SupportMessage(
            conversation_id=row.id,
            sequence=row.next_sequence,
            event_id=event_id,
            role=role,
            content_encrypted=content,
            citations=citations or [],
            ownership_generation=row.ownership_generation,
            delivery_status="staged" if staged else "visible",
        )
        row.next_sequence += 1
        db.add(message)
        db.flush()
        return message

    def append_user(
        self,
        db: Session,
        conversation_id: uuid.UUID,
        *,
        event_id: str,
        content: str,
        human_request: bool = False,
        contact: str | None = None,
    ) -> models.SupportMessage:
        row = self._lock(db, conversation_id)
        existing = (
            db.query(models.SupportMessage)
            .filter_by(conversation_id=row.id, event_id=event_id)
            .first()
        )
        if existing:
            db.commit()
            return existing
        if row.status == "closed" or row.next_sequence > 200:
            raise SupportConflictError("Conversation closed or message limit reached")
        if contact is not None:
            row.contact_encrypted = contact
        if human_request:
            row.status = "human"
            row.ownership_generation += 1
            self._cancel_ai(db, row)
        message = self._message(
            db, row, event_id=event_id, role="user", content=content
        )
        db.add(
            models.SupportOutbox(
                conversation_id=row.id,
                message_id=message.id,
                kind="notify",
                ownership_generation=row.ownership_generation,
            )
        )
        if row.status == "assistant":
            db.add(
                models.SupportOutbox(
                    conversation_id=row.id,
                    message_id=message.id,
                    kind="inference",
                    ownership_generation=row.ownership_generation,
                )
            )
        db.commit()
        return message

    def _cancel_ai(self, db: Session, row: models.SupportConversation) -> None:
        db.query(models.SupportOutbox).filter_by(
            conversation_id=row.id, kind="inference"
        ).filter(models.SupportOutbox.status.in_(["pending", "processing"])).update(
            {"status": "cancelled"}, synchronize_session=False
        )
        db.query(models.SupportMessage).filter_by(
            conversation_id=row.id, role="assistant", delivery_status="staged"
        ).update({"delivery_status": "cancelled"}, synchronize_session=False)

    def operator_event(
        self,
        db: Session,
        root_id: str,
        operator_id: str,
        event_id: str,
        content: str,
        action: str | None = None,
        listener_token: str | None = None,
    ) -> models.SupportConversation:
        if listener_token:
            cursor = (
                db.query(models.ServiceCursor)
                .filter_by(name="cloud-support-operator", lease_token=listener_token)
                .filter(models.ServiceCursor.lease_until > datetime.utcnow())
                .with_for_update()
                .first()
            )
            if cursor is None:
                raise SupportConflictError("Operator listener lease changed")
        row = (
            db.query(models.SupportConversation)
            .filter_by(operator_root_id=root_id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if row is None or row.expires_at <= datetime.utcnow():
            raise SupportConflictError("Unknown or expired operator thread")
        if (
            db.query(models.SupportMessage)
            .filter_by(conversation_id=row.id, event_id=event_id)
            .first()
        ):
            db.commit()
            return row
        if row.operator_id and row.operator_id != operator_id:
            raise SupportConflictError("Another operator owns this conversation")
        if action:
            if action not in {"takeover", "resume", "close", "reopen"}:
                raise SupportConflictError("Unknown action")
            if row.status == "closed" and action != "reopen":
                raise SupportConflictError("Conversation closed; explicitly reopen it")
            row.status = {
                "takeover": "human",
                "resume": "assistant",
                "close": "closed",
                "reopen": "human",
            }[action]
            row.operator_id = operator_id
            row.ownership_generation += 1
            self._cancel_ai(db, row)
            self._message(db, row, event_id=event_id, role="status", content=content)
        else:
            if row.status != "human" or row.operator_id != operator_id:
                raise SupportConflictError("Take over before replying")
            self._message(db, row, event_id=event_id, role="human", content=content)
        db.commit()
        return row

    def stage_answer(
        self, db: Session, job: models.SupportOutbox, content: str, citations: list
    ) -> bool:
        row = self._lock(db, job.conversation_id)
        if (
            row.status != "assistant"
            or row.ownership_generation != job.ownership_generation
        ):
            db.rollback()
            return False
        current = (
            db.query(models.SupportOutbox)
            .filter_by(id=job.id, lease_token=job.lease_token, status="processing")
            .filter(models.SupportOutbox.lease_until > datetime.utcnow())
            .first()
        )
        if current is None:
            db.rollback()
            return False
        self._message(
            db,
            row,
            event_id=f"answer:{job.id}",
            role="assistant",
            content=content,
            citations=citations,
            staged=True,
        )
        current.status = "sent"
        db.commit()
        return True

    def history(
        self, db: Session, conversation_id: uuid.UUID, *, after: int = 0
    ) -> tuple[models.SupportConversation, list[models.SupportMessage]]:
        row = self._lock(db, conversation_id)
        staged = (
            db.query(models.SupportMessage)
            .filter_by(conversation_id=row.id, delivery_status="staged")
            .all()
        )
        for message in staged:
            if (
                row.status == "assistant"
                and message.ownership_generation == row.ownership_generation
            ):
                message.delivery_status = "visible"
                db.add(
                    models.SupportOutbox(
                        conversation_id=row.id,
                        message_id=message.id,
                        kind="notify",
                        ownership_generation=row.ownership_generation,
                    )
                )
            else:
                message.delivery_status = "cancelled"
        messages = (
            db.query(models.SupportMessage)
            .filter_by(conversation_id=row.id, delivery_status="visible")
            .filter(models.SupportMessage.sequence > after)
            .order_by(models.SupportMessage.sequence)
            .limit(200)
            .all()
        )
        db.commit()
        return row, messages

    def claim(self, db: Session, kind: str) -> models.SupportOutbox | None:
        now = datetime.utcnow()
        db.query(models.SupportOutbox).filter_by(status="sending").filter(
            models.SupportOutbox.lease_until < now
        ).update(
            {
                "status": "uncertain",
                "last_error": "Send interrupted; reconcile before retrying",
            },
            synchronize_session=False,
        )
        query = db.query(models.SupportOutbox).filter(
            models.SupportOutbox.kind == kind,
            models.SupportOutbox.next_attempt_at <= now,
            models.SupportOutbox.conversation_id.in_(
                db.query(models.SupportConversation.id).filter(
                    models.SupportConversation.expires_at > now
                )
            ),
            or_(
                models.SupportOutbox.status == "pending",
                and_(
                    models.SupportOutbox.status == "processing",
                    models.SupportOutbox.lease_until < now,
                ),
            ),
        )
        # Serialize notifications in each conversation, including uncertain roots.
        for job in (
            query.order_by(models.SupportOutbox.created_at, models.SupportOutbox.id)
            .with_for_update(skip_locked=True)
            .limit(20)
            .all()
        ):
            conversation = self._lock(db, job.conversation_id)
            if job.attempts >= 3:
                job.status = "failed"
                job.last_error = "Support retry limit reached"
                continue
            earlier = (
                db.query(models.SupportOutbox)
                .join(
                    models.SupportMessage,
                    models.SupportOutbox.message_id == models.SupportMessage.id,
                )
                .filter(
                    models.SupportOutbox.conversation_id == job.conversation_id,
                    models.SupportOutbox.kind == kind,
                    models.SupportOutbox.status.in_(
                        ["pending", "processing", "sending", "uncertain"]
                        + (["failed"] if kind == "notify" else [])
                    ),
                    models.SupportMessage.sequence
                    < db.query(models.SupportMessage.sequence)
                    .filter_by(id=job.message_id)
                    .scalar(),
                )
                .first()
            )
            if earlier:
                continue
            if kind == "inference" and (
                conversation.status != "assistant"
                or conversation.ownership_generation != job.ownership_generation
            ):
                job.status = "cancelled"
                continue
            job.status, job.lease_token, job.lease_until = (
                "processing",
                str(uuid.uuid4()),
                now + timedelta(seconds=120),
            )
            job.attempts += 1
            db.commit()
            db.refresh(job)
            return job
        db.commit()
        return None

    def transition(
        self, db: Session, job: models.SupportOutbox, status: str, **values: Any
    ) -> bool:
        changed = (
            db.query(models.SupportOutbox)
            .filter_by(id=job.id, lease_token=job.lease_token)
            .filter(
                models.SupportOutbox.status.in_(["processing", "sending"]),
                models.SupportOutbox.lease_until > datetime.utcnow(),
            )
            .update({"status": status, **values}, synchronize_session=False)
        )
        db.commit()
        return bool(changed)

    def prepare_notification(self, db: Session, job: models.SupportOutbox) -> bool:
        row = self._lock(db, job.conversation_id)
        message = self.messages.get(db, job.message_id)
        current = (
            db.query(models.SupportOutbox)
            .filter_by(id=job.id, lease_token=job.lease_token, status="processing")
            .filter(models.SupportOutbox.lease_until > datetime.utcnow())
            .first()
        )
        if not current:
            db.rollback()
            return False
        if message.role == "assistant" and (
            row.status != "assistant"
            or message.ownership_generation != row.ownership_generation
        ):
            current.status = "cancelled"
            db.commit()
            return False
        current.status = "sending"
        db.commit()
        return True

    def sent(
        self, db: Session, job: models.SupportOutbox, provider_id: str, *, is_root: bool
    ) -> None:
        row = self._lock(db, job.conversation_id)
        current = (
            db.query(models.SupportOutbox)
            .filter_by(id=job.id)
            .with_for_update()
            .first()
        )
        if current.status not in {"sending", "uncertain"}:
            raise SupportConflictError("Send ownership changed")
        current.status, current.provider_message_id, current.last_error = (
            "sent",
            provider_id,
            None,
        )
        if is_root:
            if row.operator_root_id and row.operator_root_id != provider_id:
                raise SupportConflictError("Operator root conflict")
            row.operator_root_id = provider_id
        db.commit()

    def retry_notification(
        self, db: Session, conversation_id: uuid.UUID, job_id: uuid.UUID
    ) -> None:
        self._lock(db, conversation_id)
        job = (
            db.query(models.SupportOutbox)
            .filter_by(id=job_id, conversation_id=conversation_id, kind="notify")
            .with_for_update()
            .first()
        )
        if job is None or job.status != "failed":
            raise SupportConflictError(
                "Only a definitively failed notification may be retried; reconcile uncertain sends first"
            )
        job.status = "pending"
        job.attempts = 0
        job.next_attempt_at = datetime.utcnow()
        job.last_error = None
        db.commit()

    def reserve(
        self,
        db: Session,
        bucket: str,
        *,
        request_limit: int,
        amount: int = 0,
        budget: int = 0,
        ttl: int = 86400,
    ) -> bool:
        row = (
            db.query(models.SupportQuota)
            .filter_by(bucket=bucket)
            .with_for_update()
            .first()
        )
        if row is None:
            try:
                with db.begin_nested():
                    row = models.SupportQuota(
                        bucket=bucket,
                        requests=0,
                        reserved_microusd=0,
                        expires_at=datetime.utcnow() + timedelta(seconds=ttl),
                    )
                    db.add(row)
                    db.flush()
            except IntegrityError:
                db.rollback()
                return self.reserve(
                    db,
                    bucket,
                    request_limit=request_limit,
                    amount=amount,
                    budget=budget,
                    ttl=ttl,
                )
        if row.requests >= request_limit or (
            amount and row.reserved_microusd + amount > budget
        ):
            db.rollback()
            return False
        row.requests += 1
        row.reserved_microusd += amount
        db.commit()
        return True

    def operator_health(self, db: Session) -> bool:
        row = (
            db.query(models.ServiceCursor)
            .filter_by(name="cloud-support-operator")
            .filter(models.ServiceCursor.lease_until > datetime.utcnow())
            .first()
        )
        return bool(row and row.state.get("connected"))

    def cursor(
        self, db: Session, name: str, token: str, state: dict | None = None
    ) -> models.ServiceCursor | None:
        now = datetime.utcnow()
        row = (
            db.query(models.ServiceCursor)
            .filter_by(name=name)
            .with_for_update()
            .first()
        )
        if row is None:
            try:
                with db.begin_nested():
                    row = models.ServiceCursor(name=name, state={})
                    db.add(row)
                    db.flush()
            except IntegrityError:
                db.rollback()
                return None
        if row.lease_until and row.lease_until > now and row.lease_token != token:
            db.commit()
            return None
        row.lease_token, row.lease_until = token, now + timedelta(seconds=45)
        if state is not None:
            row.state = state
        db.commit()
        return row

    def purge(self, db: Session) -> None:
        now = datetime.utcnow()
        db.query(models.SupportConversation).filter(
            models.SupportConversation.expires_at < now
        ).delete(synchronize_session=False)
        db.query(models.SupportQuota).filter(
            models.SupportQuota.expires_at < now
        ).delete(synchronize_session=False)
        db.query(models.ChatSetupDraft).filter(
            models.ChatSetupDraft.expires_at < now
        ).delete(synchronize_session=False)
        db.commit()


crud_support = CRUDSupport()
