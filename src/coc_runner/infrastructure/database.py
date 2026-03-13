from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, db_url: str) -> None:
        self.db_url = db_url
        self._ensure_sqlite_directory()
        connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        self.engine = create_engine(db_url, future=True, connect_args=connect_args)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        from coc_runner.infrastructure.models import (
            KnowledgeSourceRecord,
            RuleChunkRecord,
            SessionCheckpointRecord,
            SessionRecord,
            SessionSnapshotRecord,
        )

        Base.metadata.create_all(
            self.engine,
            tables=[
                SessionRecord.__table__,
                SessionSnapshotRecord.__table__,
                SessionCheckpointRecord.__table__,
                KnowledgeSourceRecord.__table__,
                RuleChunkRecord.__table__,
            ],
        )

    def _ensure_sqlite_directory(self) -> None:
        if not self.db_url.startswith("sqlite:///"):
            return
        raw_path = self.db_url.removeprefix("sqlite:///")
        path = Path(raw_path)
        if path != Path(":memory:"):
            path.parent.mkdir(parents=True, exist_ok=True)
