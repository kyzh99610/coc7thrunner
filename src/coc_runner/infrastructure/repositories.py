from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select

from coc_runner.domain.errors import ConflictError
from coc_runner.domain.models import (
    ActorType,
    AuditActionType,
    AuditLogEntry,
    EventType,
    SessionEvent,
    SessionState,
    VisibilityScope,
)
from coc_runner.infrastructure.models import SessionRecord, SessionSnapshotRecord


class SessionRepository(Protocol):
    def create(self, session: SessionState, *, reason: str) -> None:
        ...

    def get(self, session_id: str) -> SessionState | None:
        ...

    def save(self, session: SessionState, *, reason: str, expected_version: int) -> None:
        ...

    def rollback(self, session_id: str, *, target_version: int, event_text: str) -> SessionState:
        ...


class SqlAlchemySessionRepository:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    def create(self, session: SessionState, *, reason: str) -> None:
        with self.session_factory.begin() as db:
            existing = db.get(SessionRecord, session.session_id)
            if existing is not None:
                raise ValueError(f"session {session.session_id} already exists")
            serialized = self._serialize_session(session)
            now = datetime.now(timezone.utc)
            db.add(
                SessionRecord(
                    session_id=session.session_id,
                    current_version=session.state_version,
                    language_preference=session.language_preference.value,
                    status=session.status.value,
                    session_json=serialized,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(self._build_snapshot_record(session, reason=reason, serialized=serialized))

    def get(self, session_id: str) -> SessionState | None:
        with self.session_factory() as db:
            record = db.get(SessionRecord, session_id)
            if record is None:
                return None
            return SessionState.model_validate_json(record.session_json)

    def save(self, session: SessionState, *, reason: str, expected_version: int) -> None:
        with self.session_factory.begin() as db:
            record = db.get(SessionRecord, session.session_id)
            if record is None:
                raise LookupError(f"session {session.session_id} was not found")
            if record.current_version != expected_version:
                raise ConflictError(
                    f"expected session version {expected_version}, found {record.current_version}"
                )
            serialized = self._serialize_session(session)
            record.current_version = session.state_version
            record.language_preference = session.language_preference.value
            record.status = session.status.value
            record.session_json = serialized
            record.updated_at = datetime.now(timezone.utc)
            db.add(self._build_snapshot_record(session, reason=reason, serialized=serialized))

    def rollback(self, session_id: str, *, target_version: int, event_text: str) -> SessionState:
        with self.session_factory.begin() as db:
            record = db.get(SessionRecord, session_id)
            if record is None:
                raise LookupError(f"session {session_id} was not found")
            current_session = SessionState.model_validate_json(record.session_json)

            snapshot_statement = (
                select(SessionSnapshotRecord)
                .where(
                    SessionSnapshotRecord.session_id == session_id,
                    SessionSnapshotRecord.version == target_version,
                )
                .limit(1)
            )
            snapshot_record = db.execute(snapshot_statement).scalar_one_or_none()
            if snapshot_record is None:
                raise ValueError(
                    f"session {session_id} does not have snapshot version {target_version}"
                )

            restored = SessionState.model_validate_json(snapshot_record.session_json)
            restored.audit_log = list(current_session.audit_log)
            new_version = record.current_version + 1
            now = datetime.now(timezone.utc)
            restored.timeline.append(
                SessionEvent(
                    event_type=EventType.ROLLBACK,
                    actor_type=ActorType.SYSTEM,
                    visibility_scope=VisibilityScope.PUBLIC,
                    text=event_text,
                    structured_payload={
                        "rolled_back_from_version": record.current_version,
                        "rolled_back_to_version": target_version,
                    },
                    created_at=now,
                    language_preference=restored.language_preference,
                )
            )
            restored.state_version = new_version
            restored.updated_at = now
            restored.audit_log.append(
                AuditLogEntry(
                    action=AuditActionType.ROLLBACK,
                    session_version=new_version,
                    details={
                        "rolled_back_from_version": record.current_version,
                        "rolled_back_to_version": target_version,
                    },
                    created_at=now,
                )
            )

            serialized = self._serialize_session(restored)
            record.current_version = restored.state_version
            record.language_preference = restored.language_preference.value
            record.status = restored.status.value
            record.session_json = serialized
            record.updated_at = now
            db.add(
                self._build_snapshot_record(
                    restored,
                    reason=f"rollback_to_{target_version}",
                    serialized=serialized,
                )
            )
            return restored

    @staticmethod
    def _serialize_session(session: SessionState) -> str:
        return json.dumps(session.model_dump(mode="json"), ensure_ascii=False)

    @staticmethod
    def _build_snapshot_record(
        session: SessionState,
        *,
        reason: str,
        serialized: str,
    ) -> SessionSnapshotRecord:
        return SessionSnapshotRecord(
            snapshot_id=f"snapshot-{uuid4().hex}",
            session_id=session.session_id,
            version=session.state_version,
            reason=reason,
            session_json=serialized,
            created_at=datetime.now(timezone.utc),
        )
