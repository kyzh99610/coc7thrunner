from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from coc_runner.config import Settings
from coc_runner.main import create_app


@pytest.fixture
def client() -> TestClient:
    base_dir = Path("test-artifacts")
    base_dir.mkdir(exist_ok=True)
    run_dir = base_dir / f"coc_runner_test_{uuid4().hex}"
    run_dir.mkdir()
    db_path = run_dir / "coc_runner_test.db"
    app = create_app(Settings(db_url=f"sqlite:///{db_path.as_posix()}"))
    with TestClient(app) as test_client:
        yield test_client
    shutil.rmtree(run_dir, ignore_errors=True)
