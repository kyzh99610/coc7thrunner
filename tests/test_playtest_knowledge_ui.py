from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient


TEMPLATE_SAMPLE_DIR = Path(__file__).resolve().parents[1] / "coc7th rules and templates" / "sample templates"


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


def _build_scenario_card_root() -> Path:
    scenario_root = Path("test-artifacts") / f"scenario_card_sources_{uuid4().hex}"
    investigators_dir = scenario_root / "investigators"
    owned_npcs_dir = scenario_root / "owned_npcs"
    sidecars_dir = scenario_root / "sidecars"
    module_npcs_dir = scenario_root / "module_npcs"
    investigators_dir.mkdir(parents=True)
    owned_npcs_dir.mkdir()
    sidecars_dir.mkdir()
    module_npcs_dir.mkdir()

    shutil.copy(
        TEMPLATE_SAMPLE_DIR / "Bruce vain.xlsx",
        investigators_dir / "Bruce vain.xlsx",
    )
    shutil.copy(
        TEMPLATE_SAMPLE_DIR / "Leon Von Jager.xlsx",
        investigators_dir / "Leon Von Jager.xlsx",
    )
    shutil.copy(
        TEMPLATE_SAMPLE_DIR / "Henrich·Gustav·von·Rothschild.xlsx",
        owned_npcs_dir / "Henrich·Gustav·von·Rothschild.xlsx",
    )
    shutil.copy(
        TEMPLATE_SAMPLE_DIR / "Bruce vain.xlsx",
        module_npcs_dir / "Module Keeper NPC.xlsx",
    )
    (sidecars_dir / "lobby.json").write_text(
        '{"scene_id":"scene.guesthouse_lobby","hook":"灯影压迫"}',
        encoding="utf-8",
    )
    (investigators_dir / "broken.xlsx").write_text("not-a-real-xlsx", encoding="utf-8")
    return scenario_root


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


def test_playtest_knowledge_index_can_batch_register_scenario_card_sources_from_scenario_root(
    client: TestClient,
) -> None:
    scenario_root = _build_scenario_card_root()
    try:
        response = client.post(
            "/playtest/knowledge/register-scenario-card-sources",
            data={"scenario_root_path": str(scenario_root)},
        )

        assert response.status_code == 200
        html = response.text
        assert "按 scenario 目录批量登记角色卡" in html
        assert 'action="/playtest/knowledge/register-scenario-card-sources"' in html
        assert 'name="scenario_root_path"' in html
        assert "只扫描 investigators/*.xlsx 与 owned_npcs/*.xlsx" in html
        assert "不会监控目录，也不会自动同步" in html
        assert "Bruce vain.xlsx" in html
        assert "Leon Von Jager.xlsx" in html
        assert "Henrich·Gustav·von·Rothschild.xlsx" in html
        assert "Module Keeper NPC.xlsx" not in html
        assert "broken.xlsx" in html
        assert "调查员角色卡" in html
        assert "自车 NPC 角色卡" in html
        assert "registered" in html
        assert "failed" in html
        assert "sidecars/ 目录已检测到，但本轮不会读取其中内容。" in html

        scenario_sources = [
            source
            for source in client.app.state.knowledge_service.list_sources()
            if source.source_metadata.get("scenario_root_path") == str(scenario_root.resolve())
        ]
        assert len(scenario_sources) == 3
        assert {
            source.source_metadata.get("card_category") for source in scenario_sources
        } == {"investigator", "owned_npc"}
        assert {
            source.source_metadata.get("relative_path") for source in scenario_sources
        } == {
            "investigators/Bruce vain.xlsx",
            "investigators/Leon Von Jager.xlsx",
            "owned_npcs/Henrich·Gustav·von·Rothschild.xlsx",
        }
        assert all(
            source.source_metadata.get("registration_mode") == "scenario_folder_scan"
            for source in scenario_sources
        )
        assert all(
            source.source_metadata.get("template_profile_detected") == "coc7th_integrated_workbook_v1"
            for source in scenario_sources
        )

        bruce_source = next(
            source
            for source in scenario_sources
            if source.source_metadata.get("file_name") == "Bruce vain.xlsx"
        )
        import_response = client.post(
            "/knowledge/import-character-sheet",
            json={"source_id": bruce_source.source_id},
        )
        assert import_response.status_code == 200
        extraction = import_response.json()["extraction"]
        assert extraction["template_profile"] == "coc7th_integrated_workbook_v1"
        assert extraction["investigator_name"] == "布鲁斯·维恩"
    finally:
        shutil.rmtree(scenario_root, ignore_errors=True)


def test_playtest_knowledge_index_scenario_card_batch_registration_is_light_scan_not_sync_system(
    client: TestClient,
) -> None:
    scenario_root = _build_scenario_card_root()
    try:
        first_response = client.post(
            "/playtest/knowledge/register-scenario-card-sources",
            data={"scenario_root_path": str(scenario_root)},
        )
        assert first_response.status_code == 200
        source_count_after_first_scan = _source_count(client)

        second_response = client.post(
            "/playtest/knowledge/register-scenario-card-sources",
            data={"scenario_root_path": str(scenario_root)},
        )

        assert second_response.status_code == 200
        html = second_response.text
        assert "当前不会自动同步或覆盖已登记 source。" in html
        assert "skipped" in html
        assert _source_count(client) == source_count_after_first_scan
    finally:
        shutil.rmtree(scenario_root, ignore_errors=True)
