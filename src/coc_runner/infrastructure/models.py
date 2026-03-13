from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from coc_runner.infrastructure.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SessionRecord(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    language_preference: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    session_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class SessionSnapshotRecord(Base):
    __tablename__ = "session_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    session_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class SessionCheckpointRecord(Base):
    __tablename__ = "session_checkpoints"

    checkpoint_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    source_session_id: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    source_session_version: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class KnowledgeSourceRecord(Base):
    __tablename__ = "knowledge_sources"

    source_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    source_format: Mapped[str] = mapped_column(String(40), nullable=False)
    ruleset: Mapped[str] = mapped_column(String(40), nullable=False)
    document_identity: Mapped[str] = mapped_column(String(200), nullable=False)
    source_title_zh: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class RuleChunkRecord(Base):
    __tablename__ = "rule_chunks"

    chunk_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    topic_key: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    overrides_topic: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    visibility: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    is_authoritative: Mapped[bool] = mapped_column(Boolean, nullable=False)
    chunk_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
