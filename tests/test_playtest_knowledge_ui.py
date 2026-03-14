from __future__ import annotations

from fastapi.testclient import TestClient


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


def test_playtest_knowledge_index_shows_natural_empty_state_without_sources(
    client: TestClient,
) -> None:
    response = client.get("/playtest/knowledge")

    assert response.status_code == 200
    html = response.text
    assert "准备资料" in html
    assert "当前还没有已登记的知识资料。" in html


def test_playtest_knowledge_detail_missing_source_renders_structured_error_page(
    client: TestClient,
) -> None:
    response = client.get("/playtest/knowledge/missing-source")

    assert response.status_code == 404
    html = response.text
    assert "操作失败" in html
    assert "未找到知识源 missing-source" in html
    assert "knowledge_source_not_found" in html
