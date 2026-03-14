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
    SessionCheckpoint,
    SessionCheckpointSummary,
    SessionEvent,
    SessionState,
    VisibilityScope,
)
from coc_runner.infrastructure.models import (
    SessionCheckpointRecord,
    SessionRecord,
    SessionSnapshotRecord,
)


class SessionRepository(Protocol):
    def create(self, session: SessionState, *, reason: str) -> None:
        ...

    def list_sessions(self) -> list[SessionState]:
        ...

    def get(self, session_id: str) -> SessionState | None:
        ...

    def save(self, session: SessionState, *, reason: str, expected_version: int) -> None:
        ...

    def rollback(self, session_id: str, *, target_version: int, event_text: str) -> SessionState:
        ...

    def create_checkpoint(self, checkpoint: SessionCheckpoint) -> None:
        ...

    def list_checkpoints(self, source_session_id: str) -> list[SessionCheckpointSummary]:
        ...

    def has_checkpoints_for_session(self, source_session_id: str) -> bool:
        ...

    def get_checkpoint(self, source_session_id: str, checkpoint_id: str) -> SessionCheckpoint | None:
        ...

    def save_checkpoint_metadata(self, checkpoint: SessionCheckpoint) -> None:
        ...

    def delete_checkpoint(self, source_session_id: str, checkpoint_id: str) -> None:
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

    def list_sessions(self) -> list[SessionState]:
        with self.session_factory() as db:
            statement = (
                select(SessionRecord)
                .order_by(SessionRecord.updated_at.desc(), SessionRecord.session_id.desc())
            )
            records = db.execute(statement).scalars().all()
            return [SessionState.model_validate_json(record.session_json) for record in records]

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

    def create_checkpoint(self, checkpoint: SessionCheckpoint) -> None:
        with self.session_factory.begin() as db:
            existing = db.get(SessionCheckpointRecord, checkpoint.checkpoint_id)
            if existing is not None:
                raise ValueError(f"checkpoint {checkpoint.checkpoint_id} already exists")
            db.add(
                SessionCheckpointRecord(
                    checkpoint_id=checkpoint.checkpoint_id,
                    source_session_id=checkpoint.source_session_id,
                    source_session_version=checkpoint.source_session_version,
                    label=checkpoint.label,
                    note=checkpoint.note,
                    created_by=checkpoint.created_by,
                    snapshot_json=json.dumps(
                        checkpoint.snapshot_payload,
                        ensure_ascii=False,
                    ),
                    created_at=checkpoint.created_at,
                )
            )

    def list_checkpoints(self, source_session_id: str) -> list[SessionCheckpointSummary]:
        with self.session_factory() as db:
            statement = (
                select(SessionCheckpointRecord)
                .where(SessionCheckpointRecord.source_session_id == source_session_id)
                .order_by(
                    SessionCheckpointRecord.created_at.desc(),
                    SessionCheckpointRecord.checkpoint_id.desc(),
                )
            )
            records = db.execute(statement).scalars().all()
            return [self._build_checkpoint_summary(record) for record in records]

    def has_checkpoints_for_session(self, source_session_id: str) -> bool:
        with self.session_factory() as db:
            statement = (
                select(SessionCheckpointRecord.checkpoint_id)
                .where(SessionCheckpointRecord.source_session_id == source_session_id)
                .limit(1)
            )
            return db.execute(statement).scalar_one_or_none() is not None

    def get_checkpoint(self, source_session_id: str, checkpoint_id: str) -> SessionCheckpoint | None:
        with self.session_factory() as db:
            statement = (
                select(SessionCheckpointRecord)
                .where(
                    SessionCheckpointRecord.source_session_id == source_session_id,
                    SessionCheckpointRecord.checkpoint_id == checkpoint_id,
                )
                .limit(1)
            )
            record = db.execute(statement).scalar_one_or_none()
            if record is None:
                return None
            return SessionCheckpoint(
                checkpoint_id=record.checkpoint_id,
                source_session_id=record.source_session_id,
                source_session_version=record.source_session_version,
                label=record.label,
                note=record.note,
                created_by=record.created_by,
                created_at=record.created_at,
                snapshot_payload=json.loads(record.snapshot_json),
            )

    def save_checkpoint_metadata(self, checkpoint: SessionCheckpoint) -> None:
        with self.session_factory.begin() as db:
            statement = (
                select(SessionCheckpointRecord)
                .where(
                    SessionCheckpointRecord.source_session_id == checkpoint.source_session_id,
                    SessionCheckpointRecord.checkpoint_id == checkpoint.checkpoint_id,
                )
                .limit(1)
            )
            record = db.execute(statement).scalar_one_or_none()
            if record is None:
                raise LookupError(f"checkpoint {checkpoint.checkpoint_id} was not found")
            record.label = checkpoint.label
            record.note = checkpoint.note

    def delete_checkpoint(self, source_session_id: str, checkpoint_id: str) -> None:
        with self.session_factory.begin() as db:
            statement = (
                select(SessionCheckpointRecord)
                .where(
                    SessionCheckpointRecord.source_session_id == source_session_id,
                    SessionCheckpointRecord.checkpoint_id == checkpoint_id,
                )
                .limit(1)
            )
            record = db.execute(statement).scalar_one_or_none()
            if record is None:
                raise LookupError(f"checkpoint {checkpoint_id} was not found")
            db.delete(record)

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

    @staticmethod
    def _build_checkpoint_summary(record: SessionCheckpointRecord) -> SessionCheckpointSummary:
        return SessionCheckpointSummary(
            checkpoint_id=record.checkpoint_id,
            source_session_id=record.source_session_id,
            source_session_version=record.source_session_version,
            label=record.label,
            note=record.note,
            created_by=record.created_by,
            created_at=record.created_at,
        )
