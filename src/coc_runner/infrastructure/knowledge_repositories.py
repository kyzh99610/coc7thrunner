from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import delete, select

from knowledge.schemas import KnowledgeSourceState, RuleChunk

from coc_runner.infrastructure.models import KnowledgeSourceRecord, RuleChunkRecord


class KnowledgeRepository(Protocol):
    def create_source(self, source: KnowledgeSourceState) -> None:
        ...

    def save_source(self, source: KnowledgeSourceState) -> None:
        ...

    def get_source(self, source_id: str) -> KnowledgeSourceState | None:
        ...

    def replace_chunks(self, source_id: str, chunks: list[RuleChunk]) -> None:
        ...

    def list_chunks(self, *, source_id: str | None = None) -> list[RuleChunk]:
        ...


class SqlAlchemyKnowledgeRepository:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    def create_source(self, source: KnowledgeSourceState) -> None:
        with self.session_factory.begin() as db:
            existing = db.get(KnowledgeSourceRecord, source.source_id)
            if existing is not None:
                raise ValueError(f"knowledge source {source.source_id} already exists")
            serialized = self._serialize_model(source)
            now = datetime.now(timezone.utc)
            db.add(
                KnowledgeSourceRecord(
                    source_id=source.source_id,
                    source_kind=source.source_kind.value,
                    source_format=source.source_format.value,
                    ruleset=source.ruleset,
                    document_identity=source.document_identity,
                    source_title_zh=source.source_title_zh,
                    source_json=serialized,
                    created_at=now,
                    updated_at=now,
                )
            )

    def save_source(self, source: KnowledgeSourceState) -> None:
        with self.session_factory.begin() as db:
            record = db.get(KnowledgeSourceRecord, source.source_id)
            if record is None:
                raise LookupError(f"knowledge source {source.source_id} was not found")
            record.source_kind = source.source_kind.value
            record.source_format = source.source_format.value
            record.ruleset = source.ruleset
            record.document_identity = source.document_identity
            record.source_title_zh = source.source_title_zh
            record.source_json = self._serialize_model(source)
            record.updated_at = datetime.now(timezone.utc)

    def get_source(self, source_id: str) -> KnowledgeSourceState | None:
        with self.session_factory() as db:
            record = db.get(KnowledgeSourceRecord, source_id)
            if record is None:
                return None
            return KnowledgeSourceState.model_validate_json(record.source_json)

    def replace_chunks(self, source_id: str, chunks: list[RuleChunk]) -> None:
        with self.session_factory.begin() as db:
            db.execute(delete(RuleChunkRecord).where(RuleChunkRecord.source_id == source_id))
            now = datetime.now(timezone.utc)
            for chunk in chunks:
                db.add(
                    RuleChunkRecord(
                        chunk_id=chunk.chunk_id,
                        source_id=chunk.source_id,
                        topic_key=chunk.topic_key,
                        overrides_topic=chunk.overrides_topic,
                        priority=chunk.priority,
                        visibility=chunk.visibility.value,
                        is_authoritative=chunk.is_authoritative,
                        chunk_json=self._serialize_model(chunk),
                        created_at=now,
                        updated_at=now,
                    )
                )

    def list_chunks(self, *, source_id: str | None = None) -> list[RuleChunk]:
        with self.session_factory() as db:
            statement = select(RuleChunkRecord)
            if source_id is not None:
                statement = statement.where(RuleChunkRecord.source_id == source_id)
            statement = statement.order_by(RuleChunkRecord.priority.desc(), RuleChunkRecord.chunk_id.asc())
            records = db.execute(statement).scalars().all()
            return [RuleChunk.model_validate_json(record.chunk_json) for record in records]

    @staticmethod
    def _serialize_model(model: KnowledgeSourceState | RuleChunk) -> str:
        return json.dumps(model.model_dump(mode="json"), ensure_ascii=False)
