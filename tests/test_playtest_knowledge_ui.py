from __future__ import annotations

from fastapi.testclient import TestClient


def _source_count(client: TestClient) -> int:
    return len(client.app.state.knowledge_service.list_sources())


def _register_source(
    client: TestClient,
    *,
    source_id: str,
    source_title_zh: str,
    source_kind: str = "rulebook",
    source_format: str = "plain_text",
    document_identity: str | None = None,
    default_priority: int = 10,
    is_authoritative: bool = True,
) -> None:
    response = client.post(
        "/knowledge/register-source",
        json={
            "source_id": source_id,
            "source_kind": source_kind,
            "source_format": source_format,
            "source_title_zh": source_title_zh,
            "document_identity": document_identity or source_id,
            "default_priority": default_priority,
            "default_visibility": "public",
            "allowed_player_ids": [],
            "is_authoritative": is_authoritative,
        },
    )
    assert response.status_code == 201


def _ingest_text(client: TestClient, *, source_id: str, content: str) -> None:
    response = client.post(
        "/knowledge/ingest-text",
        json={"source_id": source_id, "content": content},
    )
    assert response.status_code == 200


def test_playtest_knowledge_index_lists_sources_with_detail_links_and_metadata(
    client: TestClient,
) -> None:
    _register_source(
        client,
        source_id="guesthouse-rules",
        source_title_zh="旅店规则摘录",
        source_format="markdown",
        default_priority=30,
    )
    _ingest_text(
        client,
        source_id="guesthouse-rules",
        content="# 侦查\n侦查检定用于发现隐藏线索。",
    )
    _register_source(
        client,
        source_id="prep-notes",
        source_title_zh="跑团准备便笺",
        source_kind="campaign_note",
        source_format="plain_text",
        is_authoritative=False,
    )

    response = client.get("/playtest/knowledge")

    assert response.status_code == 200
    html = response.text
    assert "准备资料" in html
    assert 'href="/playtest/sessions"' in html
    assert 'action="/playtest/knowledge/register-source"' in html
    assert 'name="source_id"' in html
    assert 'name="source_title_zh"' in html
    assert 'name="source_kind"' in html
    assert 'name="source_format"' in html
    assert "guesthouse-rules" in html
    assert "prep-notes" in html
    assert "旅店规则摘录" in html
    assert "跑团准备便笺" in html
    assert "已入库" in html
    assert "已登记" in html
    assert "规则书" in html
    assert "跑团笔记" in html
    assert 'href="/playtest/knowledge/guesthouse-rules"' in html
    assert 'href="/playtest/knowledge/prep-notes"' in html


def test_playtest_knowledge_detail_page_shows_read_only_metadata_and_content_preview(
    client: TestClient,
) -> None:
    _register_source(
        client,
        source_id="archive-rules",
        source_title_zh="档案馆调查规则",
        source_format="markdown",
        default_priority=40,
    )
    _ingest_text(
        client,
        source_id="archive-rules",
        content=(
            "# 侦查\n"
            "侦查检定用于发现隐藏线索。\n\n"
            "## 聆听\n"
            "聆听判定用于察觉可疑声音。"
        ),
    )

    response = client.get("/playtest/knowledge/archive-rules")

    assert response.status_code == 200
    html = response.text
    assert "档案馆调查规则" in html
    assert "archive-rules" in html
    assert "资料摘要" in html
    assert "内容预览" in html
    assert "Markdown" in html
    assert "已入库" in html
    assert "侦查检定用于发现隐藏线索。" in html
    assert 'href="/playtest/knowledge"' in html
    assert f'action="/playtest/knowledge/archive-rules/ingest-text"' in html
    assert 'name="content"' in html


def test_playtest_knowledge_index_shows_natural_empty_state_without_sources(
    client: TestClient,
) -> None:
    response = client.get("/playtest/knowledge")

    assert response.status_code == 200
    html = response.text
    assert "准备资料" in html
    assert "当前还没有已登记的知识资料。" in html
    assert 'action="/playtest/knowledge/register-source"' in html


def test_playtest_knowledge_detail_missing_source_renders_structured_error_page(
    client: TestClient,
) -> None:
    response = client.get("/playtest/knowledge/missing-source")

    assert response.status_code == 404
    html = response.text
    assert "操作失败" in html
    assert "未找到知识源 missing-source" in html
    assert "knowledge_source_not_found" in html


def test_playtest_knowledge_index_register_source_form_creates_source_and_lists_it(
    client: TestClient,
) -> None:
    source_count_before_create = _source_count(client)

    response = client.post(
        "/playtest/knowledge/register-source",
        data={
            "source_id": "field-notes",
            "source_title_zh": "旅店现场笔记",
            "source_kind": "campaign_note",
            "source_format": "plain_text",
        },
    )

    assert response.status_code == 200
    html = response.text
    assert "知识源已注册" in html
    assert "field-notes" in html
    assert "旅店现场笔记" in html
    assert "跑团笔记" in html
    assert _source_count(client) == source_count_before_create + 1


def test_playtest_knowledge_index_register_source_failure_shows_error_without_creating_source(
    client: TestClient,
) -> None:
    _register_source(
        client,
        source_id="duplicate-source",
        source_title_zh="重复知识源",
        document_identity="duplicate-source",
    )
    source_count_before_failure = _source_count(client)

    response = client.post(
        "/playtest/knowledge/register-source",
        data={
            "source_id": "duplicate-source",
            "source_title_zh": "重复知识源",
            "source_kind": "rulebook",
            "source_format": "plain_text",
        },
    )

    assert response.status_code == 400
    html = response.text
    assert "操作失败" in html
    assert "knowledge_source_registration_invalid" in html
    assert "duplicate-source" in html
    assert _source_count(client) == source_count_before_failure


def test_playtest_knowledge_detail_ingest_text_form_updates_preview_after_success(
    client: TestClient,
) -> None:
    _register_source(
        client,
        source_id="draft-rules",
        source_title_zh="旅店草案规则",
        document_identity="draft-rules",
    )

    response = client.post(
        "/playtest/knowledge/draft-rules/ingest-text",
        data={"content": "# 侦查\n侦查检定用于发现地板缝里的隐藏纸条。"},
    )

    assert response.status_code == 200
    html = response.text
    assert "文本知识已入库" in html
    assert "继续去创建 session" in html
    assert 'href="/playtest/sessions/create"' in html
    assert "旅店草案规则" in html
    assert "侦查检定用于发现地板缝里的隐藏纸条。" in html
    assert "已入库" in html


def test_playtest_knowledge_detail_ingest_text_failure_shows_error_without_silent_drop(
    client: TestClient,
) -> None:
    _register_source(
        client,
        source_id="draft-rules",
        source_title_zh="旅店草案规则",
        document_identity="draft-rules",
    )

    response = client.post(
        "/playtest/knowledge/draft-rules/ingest-text",
        data={"content": ""},
    )

    assert response.status_code == 422
    html = response.text
    assert "操作失败" in html
    assert "request_validation_failed" in html
    assert "继续去创建 session" not in html
    assert "旅店草案规则" in html
