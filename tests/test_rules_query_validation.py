from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "knowledge"


def _register_source(
    client: TestClient,
    *,
    source_id: str,
    source_kind: str,
    source_title_zh: str,
    source_path: Path,
    default_priority: int,
    default_visibility: str = "public",
    allowed_player_ids: list[str] | None = None,
) -> None:
    response = client.post(
        "/knowledge/register-source",
        json={
            "source_id": source_id,
            "source_kind": source_kind,
            "source_format": "markdown",
            "source_title_zh": source_title_zh,
            "document_identity": source_id,
            "source_path": str(source_path),
            "default_priority": default_priority,
            "default_visibility": default_visibility,
            "allowed_player_ids": allowed_player_ids or [],
            "is_authoritative": True,
        },
    )
    assert response.status_code == 201


def _seed_validation_sources(client: TestClient) -> None:
    _register_source(
        client,
        source_id="validation-core",
        source_kind="rulebook",
        source_title_zh="核心规则验证样例",
        source_path=FIXTURE_DIR / "query_validation_core_rules.md",
        default_priority=30,
    )
    _register_source(
        client,
        source_id="validation-house",
        source_kind="house_rule",
        source_title_zh="房规验证样例",
        source_path=FIXTURE_DIR / "query_validation_house_rules.md",
        default_priority=80,
    )
    assert client.post("/knowledge/ingest-file", json={"source_id": "validation-core"}).status_code == 200
    assert client.post("/knowledge/ingest-file", json={"source_id": "validation-house"}).status_code == 200


def test_real_query_hard_and_extreme_success_validation(client: TestClient) -> None:
    _seed_validation_sources(client)

    response = client.post(
        "/rules/query",
        json={
            "query_text": "困难成功和极难成功怎么算",
            "deterministic_resolution_required": True,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["deterministic_resolution_required"] is True
    assert payload["deterministic_handoff_topic"] in {"term:hard_success", "term:extreme_success"}
    assert payload["citations"]
    assert any(
        "一半" in chunk["text"] or "五分之一" in chunk["text"]
        for chunk in payload["matched_chunks"]
    )


def test_real_query_pushed_roll_failure_validation(client: TestClient) -> None:
    _seed_validation_sources(client)

    response = client.post(
        "/rules/query",
        json={"query_text": "推动检定失败后怎么处理"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["matched_chunks"]
    assert payload["matched_chunks"][0]["resolved_topic"] == "term:pushed_roll"
    assert "更严重的后果" in payload["matched_chunks"][0]["text"]


def test_real_query_spot_hidden_and_listen_validation(client: TestClient) -> None:
    _seed_validation_sources(client)

    response = client.post(
        "/rules/query",
        json={"query_text": "侦查和聆听在不同场景中的作用"},
    )
    payload = response.json()

    assert response.status_code == 200
    resolved_topics = {chunk["resolved_topic"] for chunk in payload["matched_chunks"]}
    assert "term:spot_hidden" in resolved_topics
    assert "term:listen" in resolved_topics
    assert payload["normalized_query"] is None


def test_real_query_sanity_review_recommends_human_review(client: TestClient) -> None:
    _seed_validation_sources(client)

    response = client.post(
        "/rules/query",
        json={"query_text": "理智检定何时需要人工审阅"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["human_review_recommended"] is True
    assert payload["human_review_reason"] is not None
    assert payload["matched_chunks"][0]["resolved_topic"] == "term:sanity_check"


def test_real_query_house_rule_override_validation(client: TestClient) -> None:
    _seed_validation_sources(client)

    response = client.post(
        "/rules/query",
        json={
            "query_text": "我的 house rule 如何覆盖官方规则",
            "deterministic_resolution_required": True,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["normalized_query"] == "我的 房规 如何覆盖官方规则"
    assert payload["matched_chunks"][0]["priority"] == 80
    assert "房规优先于官方规则" in payload["matched_chunks"][0]["text"]
    assert payload["citations"]


def test_persisted_visibility_constraints_hold_for_validation_queries(client: TestClient) -> None:
    _register_source(
        client,
        source_id="validation-kp",
        source_kind="module",
        source_title_zh="KP 私有验证规则",
        source_path=FIXTURE_DIR / "query_validation_core_rules.md",
        default_priority=60,
        default_visibility="kp_only",
    )
    _register_source(
        client,
        source_id="validation-shared",
        source_kind="module",
        source_title_zh="共享子集验证规则",
        source_path=FIXTURE_DIR / "query_validation_core_rules.md",
        default_priority=50,
        default_visibility="shared_subset",
        allowed_player_ids=["investigator-1"],
    )
    assert client.post("/knowledge/ingest-file", json={"source_id": "validation-kp"}).status_code == 200
    assert client.post("/knowledge/ingest-file", json={"source_id": "validation-shared"}).status_code == 200

    investigator = client.post(
        "/rules/query",
        json={"query_text": "理智检定", "viewer_role": "investigator", "viewer_id": "investigator-2"},
    )
    shared_investigator = client.post(
        "/rules/query",
        json={"query_text": "理智检定", "viewer_role": "investigator", "viewer_id": "investigator-1"},
    )
    keeper = client.post(
        "/rules/query",
        json={"query_text": "理智检定", "viewer_role": "keeper"},
    )

    assert investigator.status_code == 200
    assert investigator.json()["matched_chunks"] == []
    assert shared_investigator.json()["matched_chunks"]
    assert keeper.json()["matched_chunks"]
