from __future__ import annotations

from sqlalchemy import func, select
from fastapi.testclient import TestClient

from knowledge.schemas import RuleQueryResult

from coc_runner.infrastructure.models import KnowledgeSourceRecord, RuleChunkRecord


def _register_source(
    client: TestClient,
    *,
    source_id: str,
    source_kind: str = "rulebook",
    source_format: str = "plain_text",
    source_title_zh: str,
    document_identity: str,
    default_priority: int,
    default_visibility: str = "public",
    allowed_player_ids: list[str] | None = None,
    is_authoritative: bool = True,
) -> dict:
    response = client.post(
        "/knowledge/register-source",
        json={
            "source_id": source_id,
            "source_kind": source_kind,
            "source_format": source_format,
            "source_title_zh": source_title_zh,
            "document_identity": document_identity,
            "default_priority": default_priority,
            "default_visibility": default_visibility,
            "allowed_player_ids": allowed_player_ids or [],
            "is_authoritative": is_authoritative,
        },
    )
    assert response.status_code == 201
    return response.json()["source"]


def _ingest_text(client: TestClient, *, source_id: str, content: str) -> dict:
    response = client.post(
        "/knowledge/ingest-text",
        json={"source_id": source_id, "content": content},
    )
    assert response.status_code == 200
    return response.json()


def _count_sources(client: TestClient) -> int:
    repository = client.app.state.knowledge_service.repository
    with repository.session_factory() as db:
        return int(
            db.execute(select(func.count()).select_from(KnowledgeSourceRecord)).scalar_one()
        )


def _count_chunks(client: TestClient) -> int:
    repository = client.app.state.knowledge_service.repository
    with repository.session_factory() as db:
        return int(
            db.execute(select(func.count()).select_from(RuleChunkRecord)).scalar_one()
        )


def test_ingest_text_source_persists_chunks(client: TestClient) -> None:
    _register_source(
        client,
        source_id="core-md",
        source_title_zh="核心规则",
        document_identity="core-md",
        source_format="markdown",
        default_priority=30,
    )

    ingest_response = _ingest_text(
        client,
        source_id="core-md",
        content=(
            "# 侦察\n"
            "侦察检定用于发现隐藏线索。\n\n"
            "## 听觉\n"
            "听觉判定用于察觉可疑声音。"
        ),
    )

    assert ingest_response["persisted_chunk_count"] == 2
    assert ingest_response["source"]["ingest_status"] == "ingested"
    assert "侦查检定" in ingest_response["source"]["normalized_text"]

    source_response = client.get("/knowledge/sources/core-md")
    assert source_response.status_code == 200
    assert source_response.json()["chunk_count"] == 2

    repository = client.app.state.knowledge_service.repository
    persisted_chunks = repository.list_chunks(source_id="core-md")
    assert len(persisted_chunks) == 2
    assert persisted_chunks[0].topic_key.startswith("term:")


def test_register_source_duplicate_returns_structured_400_without_mutating_sources(
    client: TestClient,
) -> None:
    _register_source(
        client,
        source_id="duplicate-source",
        source_title_zh="重复知识源",
        document_identity="duplicate-source",
        default_priority=20,
    )
    before_source_count = _count_sources(client)

    duplicate_response = client.post(
        "/knowledge/register-source",
        json={
            "source_id": "duplicate-source",
            "source_kind": "rulebook",
            "source_format": "plain_text",
            "source_title_zh": "重复知识源",
            "document_identity": "duplicate-source",
            "default_priority": 20,
            "default_visibility": "public",
            "allowed_player_ids": [],
            "is_authoritative": True,
        },
    )

    assert duplicate_response.status_code == 400
    assert duplicate_response.json()["detail"] == {
        "code": "knowledge_source_registration_invalid",
        "message": "knowledge source duplicate-source already exists",
        "scope": "knowledge_source_registration",
        "source_id": "duplicate-source",
    }
    assert _count_sources(client) == before_source_count


def test_ingest_text_missing_source_returns_structured_404_without_mutating_chunks(
    client: TestClient,
) -> None:
    before_chunk_count = _count_chunks(client)

    response = client.post(
        "/knowledge/ingest-text",
        json={"source_id": "missing-source", "content": "聆听判定用于察觉远处声音。"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "knowledge_source_not_found",
        "message": "未找到知识源 missing-source",
        "scope": "knowledge_source_lookup",
        "source_id": "missing-source",
    }
    assert _count_chunks(client) == before_chunk_count


def test_get_source_missing_source_returns_structured_404(client: TestClient) -> None:
    response = client.get("/knowledge/sources/missing-source")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "knowledge_source_not_found",
        "message": "未找到知识源 missing-source",
        "scope": "knowledge_source_lookup",
        "source_id": "missing-source",
    }


def test_retrieve_only_visible_chunks(client: TestClient) -> None:
    _register_source(
        client,
        source_id="public-source",
        source_title_zh="公共规则",
        document_identity="public-source",
        default_priority=20,
    )
    _register_source(
        client,
        source_id="kp-source",
        source_title_zh="KP 私有规则",
        document_identity="kp-source",
        default_priority=40,
        default_visibility="kp_only",
    )
    _register_source(
        client,
        source_id="shared-source",
        source_title_zh="共享规则",
        document_identity="shared-source",
        default_priority=30,
        default_visibility="shared_subset",
        allowed_player_ids=["investigator-1"],
    )

    _ingest_text(client, source_id="public-source", content="聆听判定的公共说明。")
    _ingest_text(client, source_id="kp-source", content="斗殴检定的 KP 说明。")
    _ingest_text(client, source_id="shared-source", content="侦查检定的共享说明。")

    investigator_one = client.post(
        "/rules/query",
        json={"query_text": "说明", "viewer_role": "investigator", "viewer_id": "investigator-1"},
    )
    investigator_two = client.post(
        "/rules/query",
        json={"query_text": "说明", "viewer_role": "investigator", "viewer_id": "investigator-2"},
    )
    keeper = client.post(
        "/rules/query",
        json={"query_text": "说明", "viewer_role": "keeper"},
    )

    assert [chunk["text"] for chunk in investigator_one.json()["matched_chunks"]] == [
        "侦查检定的共享说明。",
        "聆听判定的公共说明。",
    ]
    assert [chunk["text"] for chunk in investigator_two.json()["matched_chunks"]] == [
        "聆听判定的公共说明。"
    ]
    assert [chunk["text"] for chunk in keeper.json()["matched_chunks"]] == [
        "斗殴检定的 KP 说明。",
        "侦查检定的共享说明。",
        "聆听判定的公共说明。",
    ]


def test_house_rule_priority_beats_core_rule_on_same_topic(client: TestClient) -> None:
    _register_source(
        client,
        source_id="core-spot-hidden",
        source_title_zh="核心规则",
        document_identity="core-spot-hidden",
        default_priority=30,
    )
    _register_source(
        client,
        source_id="house-spot-hidden",
        source_title_zh="房规",
        document_identity="house-spot-hidden",
        source_kind="house_rule",
        default_priority=80,
    )

    _ingest_text(client, source_id="core-spot-hidden", content="侦查检定用于发现隐藏线索。")
    _ingest_text(client, source_id="house-spot-hidden", content="侦察检定改为允许额外提示。")

    query_response = client.post("/rules/query", json={"query_text": "观察检定"})
    payload = query_response.json()

    assert query_response.status_code == 200
    assert payload["normalized_query"] == "侦查检定"
    assert len(payload["matched_chunks"]) == 1
    assert payload["matched_chunks"][0]["priority"] == 80
    assert payload["matched_chunks"][0]["text"] == "侦查检定改为允许额外提示。"


def test_rules_query_returns_rule_query_result(client: TestClient) -> None:
    _register_source(
        client,
        source_id="query-shape",
        source_title_zh="检索规则",
        document_identity="query-shape",
        default_priority=25,
    )
    _ingest_text(client, source_id="query-shape", content="聆听判定用于察觉远处声音。")

    response = client.post(
        "/rules/query",
        json={
            "query_text": "听觉判定",
            "deterministic_resolution_required": True,
        },
    )
    assert response.status_code == 200
    result = RuleQueryResult.model_validate(response.json())
    assert result.deterministic_resolution_required is True
    assert result.matched_chunks


def test_chinese_normalization_still_works_through_api_path(client: TestClient) -> None:
    _register_source(
        client,
        source_id="normalization-api",
        source_title_zh="归一化规则",
        document_identity="normalization-api",
        default_priority=35,
    )
    _ingest_text(client, source_id="normalization-api", content="侦察检定用于发现细节。")

    response = client.post("/rules/query", json={"query_text": "观察检定"})
    payload = response.json()

    assert response.status_code == 200
    assert payload["normalized_query"] == "侦查检定"
    assert payload["matched_chunks"][0]["text"] == "侦查检定用于发现细节。"
