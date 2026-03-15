from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from knowledge.schemas import RuleQueryResult


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "knowledge"
UPLOADED_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "coc7th rules and templates"
UPLOADED_TEMPLATE_SAMPLE_DIR = UPLOADED_TEMPLATE_DIR / "sample templates"


def _register_source(
    client: TestClient,
    *,
    source_id: str,
    source_kind: str,
    source_format: str,
    source_title_zh: str,
    document_identity: str,
    source_path: Path,
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
            "source_path": str(source_path),
            "default_priority": default_priority,
            "default_visibility": default_visibility,
            "allowed_player_ids": allowed_player_ids or [],
            "is_authoritative": is_authoritative,
        },
    )
    assert response.status_code == 201
    return response.json()["source"]


def _get_source_state(client: TestClient, source_id: str) -> dict:
    repository = client.app.state.knowledge_service.repository
    source = repository.get_source(source_id)
    assert source is not None
    return source.model_dump(mode="json")


def test_ingest_pdf_rule_content_and_query_it(client: TestClient) -> None:
    _register_source(
        client,
        source_id="pdf-core-sample",
        source_kind="rulebook",
        source_format="pdf",
        source_title_zh="核心规则 PDF 节选",
        document_identity="pdf-core-sample",
        source_path=FIXTURE_DIR / "simple_rule_excerpt.pdf",
        default_priority=35,
    )

    ingest_response = client.post(
        "/knowledge/ingest-file",
        json={"source_id": "pdf-core-sample"},
    )
    assert ingest_response.status_code == 200
    assert ingest_response.json()["persisted_chunk_count"] == 1

    query_response = client.post(
        "/rules/query",
        json={"query_text": "Library Use"},
    )
    assert query_response.status_code == 200
    payload = query_response.json()

    assert payload["matched_chunks"][0]["page_reference"] == 1
    assert "Library Use checks search archives" in payload["matched_chunks"][0]["text"]
    assert payload["matched_chunks"][0]["short_citation"] is not None
    assert payload["citations"]
    assert payload["chinese_answer_draft"].startswith("优先参考")


def test_multi_page_pdf_preserves_page_reference_and_heading(client: TestClient) -> None:
    _register_source(
        client,
        source_id="pdf-multi-page",
        source_kind="rulebook",
        source_format="pdf",
        source_title_zh="多页 PDF 规则样例",
        document_identity="pdf-multi-page",
        source_path=FIXTURE_DIR / "multi_page_rule_excerpt.pdf",
        default_priority=40,
    )

    ingest_response = client.post(
        "/knowledge/ingest-file",
        json={"source_id": "pdf-multi-page"},
    )
    assert ingest_response.status_code == 200
    assert ingest_response.json()["persisted_chunk_count"] == 2

    query_response = client.post(
        "/rules/query",
        json={"query_text": "推动检定失败后会怎样"},
    )
    assert query_response.status_code == 200
    payload = query_response.json()

    assert payload["matched_chunks"][0]["page_reference"] == 1
    assert payload["matched_chunks"][0]["short_citation"] == "《多页 PDF 规则样例》第1页·推动检定"
    assert payload["citations"][0] == "《多页 PDF 规则样例》第1页·推动检定"
    assert "更严重的后果" in payload["matched_chunks"][0]["text"]


def test_house_rule_file_overrides_core_rule_on_same_topic(client: TestClient) -> None:
    _register_source(
        client,
        source_id="core-rule-file",
        source_kind="rulebook",
        source_format="markdown",
        source_title_zh="核心规则样例",
        document_identity="core-rule-file",
        source_path=FIXTURE_DIR / "core_rule_sample.md",
        default_priority=30,
    )
    _register_source(
        client,
        source_id="house-rule-file",
        source_kind="house_rule",
        source_format="markdown",
        source_title_zh="房规则样例",
        document_identity="house-rule-file",
        source_path=FIXTURE_DIR / "house_rule_sample.md",
        default_priority=80,
    )

    assert client.post("/knowledge/ingest-file", json={"source_id": "core-rule-file"}).status_code == 200
    assert client.post("/knowledge/ingest-file", json={"source_id": "house-rule-file"}).status_code == 200

    query_response = client.post(
        "/rules/query",
        json={
            "query_text": "侦察检定",
            "deterministic_resolution_required": True,
        },
    )
    assert query_response.status_code == 200
    payload = RuleQueryResult.model_validate(query_response.json())

    assert payload.deterministic_resolution_required is True
    assert payload.normalized_query == "侦查检定"
    assert len(payload.matched_chunks) == 1
    assert payload.matched_chunks[0].priority == 80
    assert payload.matched_chunks[0].resolved_topic == "term:spot_hidden"
    assert "房规" in (payload.chinese_answer_draft or "")


def test_import_character_sheet_sample_successfully(client: TestClient) -> None:
    _register_source(
        client,
        source_id="character-sheet-json",
        source_kind="character_sheet",
        source_format="json",
        source_title_zh="林舟角色卡",
        document_identity="character-sheet-json",
        source_path=FIXTURE_DIR / "character_sheet_sample.json",
        default_priority=0,
        is_authoritative=False,
    )

    import_response = client.post(
        "/knowledge/import-character-sheet",
        json={"source_id": "character-sheet-json"},
    )
    assert import_response.status_code == 200
    payload = import_response.json()

    assert payload["extraction"]["investigator_name"] == "林舟"
    assert payload["extraction"]["core_stats"]["strength"] == 50
    assert set(payload["extraction"]["core_stats"].keys()) == {
        "strength",
        "constitution",
        "size",
        "dexterity",
        "appearance",
        "intelligence",
        "power",
        "education",
    }
    assert payload["extraction"]["extraction_confidence"] == 0.98
    assert payload["source"]["character_sheet_extraction"]["skills"]["图书馆使用"] == 70


def test_import_character_sheet_csv_handles_missing_optional_values(client: TestClient) -> None:
    _register_source(
        client,
        source_id="character-sheet-csv",
        source_kind="character_sheet",
        source_format="csv",
        source_title_zh="周岚角色卡",
        document_identity="character-sheet-csv",
        source_path=FIXTURE_DIR / "character_sheet_sample.csv",
        default_priority=0,
        is_authoritative=False,
    )

    import_response = client.post(
        "/knowledge/import-character-sheet",
        json={"source_id": "character-sheet-csv"},
    )
    assert import_response.status_code == 200
    payload = import_response.json()

    assert payload["extraction"]["investigator_name"] == "周岚"
    assert payload["extraction"]["background_traits"] is None
    assert payload["extraction"]["secrets"] is None
    assert payload["extraction"]["extraction_confidence"] == 0.72
    assert payload["extraction"]["skills"]["医学"] == 70


def test_import_character_sheet_xlsx_successfully(client: TestClient) -> None:
    _register_source(
        client,
        source_id="character-sheet-xlsx",
        source_kind="character_sheet",
        source_format="xlsx",
        source_title_zh="许明角色卡",
        document_identity="character-sheet-xlsx",
        source_path=FIXTURE_DIR / "character_sheet_sample.xlsx",
        default_priority=0,
        is_authoritative=False,
    )

    import_response = client.post(
        "/knowledge/import-character-sheet",
        json={"source_id": "character-sheet-xlsx"},
    )
    assert import_response.status_code == 200
    payload = import_response.json()

    assert payload["extraction"]["investigator_name"] == "许明"
    assert payload["extraction"]["core_stats"]["strength"] == 55
    assert payload["extraction"]["derived_stats"]["san"] == 65
    assert payload["extraction"]["skills"]["侦查"] == 70
    assert payload["extraction"]["ambiguous_fields"] == ["san:理智值来源待确认"]
    assert payload["extraction"]["background_traits"] is None
    assert payload["extraction"]["template_profile"] is None
    assert payload["extraction"]["source_metadata"] == {}
    assert payload["extraction"]["extraction_confidence"] == 0.83
    assert set(payload["extraction"]["core_stats"].keys()) == {
        "strength",
        "constitution",
        "size",
        "dexterity",
        "appearance",
        "intelligence",
        "power",
        "education",
    }


def test_invalid_character_sheet_xlsx_returns_400(client: TestClient) -> None:
    _register_source(
        client,
        source_id="character-sheet-xlsx-invalid",
        source_kind="character_sheet",
        source_format="xlsx",
        source_title_zh="损坏角色卡",
        document_identity="character-sheet-xlsx-invalid",
        source_path=FIXTURE_DIR / "invalid_character_sheet_sample.xlsx",
        default_priority=0,
        is_authoritative=False,
    )
    before_source_state = _get_source_state(client, "character-sheet-xlsx-invalid")

    import_response = client.post(
        "/knowledge/import-character-sheet",
        json={"source_id": "character-sheet-xlsx-invalid"},
    )
    assert import_response.status_code == 400
    detail = import_response.json()["detail"]
    assert detail["code"] == "knowledge_character_import_invalid"
    assert detail["scope"] == "knowledge_character_import"
    assert detail["source_id"] == "character-sheet-xlsx-invalid"
    assert "required" in detail["message"] or "confidence" in detail["message"]
    assert _get_source_state(client, "character-sheet-xlsx-invalid") == before_source_state


def test_import_integrated_character_workbook_template_successfully(client: TestClient) -> None:
    _register_source(
        client,
        source_id="character-sheet-template-bruce",
        source_kind="character_sheet",
        source_format="xlsx",
        source_title_zh="布鲁斯角色卡",
        document_identity="character-sheet-template-bruce",
        source_path=UPLOADED_TEMPLATE_SAMPLE_DIR / "Bruce vain.xlsx",
        default_priority=0,
        is_authoritative=False,
    )

    import_response = client.post(
        "/knowledge/import-character-sheet",
        json={"source_id": "character-sheet-template-bruce"},
    )
    assert import_response.status_code == 200
    payload = import_response.json()

    extraction = payload["extraction"]
    review = payload["review"]
    assert extraction["template_profile"] == "coc7th_integrated_workbook_v1"
    assert extraction["source_metadata"]["xlsx_import_mode"] == "integrated_template"
    assert extraction["source_metadata"]["main_sheet_name"] == "人物卡"
    assert extraction["source_metadata"]["summary_sheet_name"] == "简化卡 骰娘导入"
    assert extraction["investigator_name"] == "布鲁斯·维恩"
    assert extraction["player_name"] == "WoW"
    assert extraction["occupation"] == "总裁"
    assert extraction["occupation_sequence_id"] == "207"
    assert extraction["era"] == "现代"
    assert extraction["age"] == 22
    assert extraction["residence"] == "芝加哥"
    assert extraction["hometown"] == "美国"
    assert extraction["core_stats"]["strength"] == 50
    assert extraction["core_stats"]["power"] == 60
    assert extraction["core_stats"]["size"] == 85
    assert extraction["derived_stats"]["hp"] == 14
    assert extraction["derived_stats"]["san"] == 60
    assert extraction["derived_stats"]["mp"] == 12
    assert extraction["derived_stats"]["luck"] == 80
    assert extraction["derived_stats"]["mov"] == 7
    assert extraction["derived_stats"]["armor"] == 5
    assert extraction["skills"]["侦查"] == 70
    assert extraction["skills"]["聆听"] == 50
    assert extraction["skills"]["斗殴"] == 60
    assert extraction["skills"]["剑"] == 60
    assert extraction["skills"]["德语"] == 31
    assert not any(skill_name.startswith("技艺") for skill_name in extraction["skills"])
    assert "描述：身高190，黑发黑眼" in (extraction["background_traits"] or "")
    assert "信仰：中立·中立" in (extraction["background_traits"] or "")
    assert extraction["secrets"] == "小秘密：腹部的一条刀疤，被抢劫捅的"
    assert "布鲁斯出生在一个富裕的家庭里面" in (extraction["campaign_notes"] or "")
    assert extraction["starting_inventory"]
    assert extraction["extraction_confidence"] >= 0.9
    assert "skill_placeholder:技艺①" in extraction["ambiguous_fields"]
    assert review["template_profile_used"] == "coc7th_integrated_workbook_v1"
    assert review["manual_review_required"] is True
    assert "investigator_name" in review["reliably_extracted_fields"]
    assert "core_stats" in review["reliably_extracted_fields"]
    assert "skills" in review["reliably_extracted_fields"]
    assert "starting_inventory" in review["reliably_extracted_fields"]
    assert "skill_placeholder:技艺①" in review["ambiguous_fields"]
    assert any("模板占位技能" in warning for warning in review["warnings"])
    assert any("辅助表当前仅作补充参考" in warning for warning in review["warnings"])
    assert any("职业序号 207" in warning for warning in review["warnings"])
    assert any("本职技能可能包括" in warning for warning in review["warnings"])


def test_integrated_character_workbook_provenance_is_present_for_key_fields(
    client: TestClient,
) -> None:
    _register_source(
        client,
        source_id="character-sheet-template-provenance",
        source_kind="character_sheet",
        source_format="xlsx",
        source_title_zh="布鲁斯角色卡来源",
        document_identity="character-sheet-template-provenance",
        source_path=UPLOADED_TEMPLATE_SAMPLE_DIR / "Bruce vain.xlsx",
        default_priority=0,
        is_authoritative=False,
    )

    import_response = client.post(
        "/knowledge/import-character-sheet",
        json={"source_id": "character-sheet-template-provenance"},
    )
    assert import_response.status_code == 200
    extraction = import_response.json()["extraction"]
    provenance = extraction["field_provenance"]

    assert provenance["investigator_name"] == {
        "source_workbook": "Bruce vain.xlsx",
        "source_sheet": "人物卡",
        "source_anchor": "E3",
    }
    assert provenance["occupation"] == {
        "source_workbook": "Bruce vain.xlsx",
        "source_sheet": "人物卡",
        "source_anchor": "E5",
    }
    assert provenance["core_stats.strength"] == {
        "source_workbook": "Bruce vain.xlsx",
        "source_sheet": "人物卡",
        "source_anchor": "U3",
    }
    assert provenance["derived_stats.hp"] == {
        "source_workbook": "Bruce vain.xlsx",
        "source_sheet": "人物卡",
        "source_anchor": "E10",
    }
    assert provenance["derived_stats.luck"] == {
        "source_workbook": "Bruce vain.xlsx",
        "source_sheet": "人物卡",
        "source_anchor": "AG7",
    }
    assert provenance["starting_inventory"] == {
        "source_workbook": "Bruce vain.xlsx",
        "source_sheet": "简化卡 骰娘导入",
        "source_anchor": "B22:K25",
    }
    assert provenance["skills.侦查"]["source_workbook"] == "Bruce vain.xlsx"
    assert provenance["skills.侦查"]["source_sheet"] == "人物卡"
    assert "/" in provenance["skills.侦查"]["source_anchor"]


def test_integrated_character_workbook_placeholder_skills_are_flagged_not_imported(
    client: TestClient,
) -> None:
    _register_source(
        client,
        source_id="character-sheet-template-placeholders",
        source_kind="character_sheet",
        source_format="xlsx",
        source_title_zh="布鲁斯角色卡占位技能",
        document_identity="character-sheet-template-placeholders",
        source_path=UPLOADED_TEMPLATE_SAMPLE_DIR / "Bruce vain.xlsx",
        default_priority=0,
        is_authoritative=False,
    )

    import_response = client.post(
        "/knowledge/import-character-sheet",
        json={"source_id": "character-sheet-template-placeholders"},
    )
    assert import_response.status_code == 200
    extraction = import_response.json()["extraction"]

    assert "skill_placeholder:技艺①" in extraction["ambiguous_fields"]
    assert any(field.startswith("skill_placeholder:技艺") for field in extraction["ambiguous_fields"])
    assert all(not skill_name.startswith("技艺") for skill_name in extraction["skills"])


def test_blank_integrated_character_workbook_fails_clearly(client: TestClient) -> None:
    _register_source(
        client,
        source_id="character-sheet-template-blank",
        source_kind="character_sheet",
        source_format="xlsx",
        source_title_zh="空白规则卡",
        document_identity="character-sheet-template-blank",
        source_path=UPLOADED_TEMPLATE_DIR / "COC七版规则空白卡.xlsx",
        default_priority=0,
        is_authoritative=False,
    )
    before_source_state = _get_source_state(client, "character-sheet-template-blank")

    import_response = client.post(
        "/knowledge/import-character-sheet",
        json={"source_id": "character-sheet-template-blank"},
    )
    assert import_response.status_code == 400
    detail = import_response.json()["detail"]
    assert detail["code"] == "knowledge_character_import_invalid"
    assert detail["scope"] == "knowledge_character_import"
    assert detail["source_id"] == "character-sheet-template-blank"
    assert detail["message"].startswith("integrated workbook field investigator_name is missing")
    assert _get_source_state(client, "character-sheet-template-blank") == before_source_state


def test_invalid_character_sheet_import_returns_400(client: TestClient) -> None:
    _register_source(
        client,
        source_id="character-sheet-invalid",
        source_kind="character_sheet",
        source_format="json",
        source_title_zh="无效角色卡",
        document_identity="character-sheet-invalid",
        source_path=FIXTURE_DIR / "invalid_character_sheet_sample.json",
        default_priority=0,
        is_authoritative=False,
    )
    before_source_state = _get_source_state(client, "character-sheet-invalid")

    import_response = client.post(
        "/knowledge/import-character-sheet",
        json={"source_id": "character-sheet-invalid"},
    )
    assert import_response.status_code == 400
    detail = import_response.json()["detail"]
    assert detail["code"] == "knowledge_character_import_invalid"
    assert detail["scope"] == "knowledge_character_import"
    assert detail["source_id"] == "character-sheet-invalid"
    assert "core_stats" in detail["message"] or "investigator_name" in detail["message"]
    assert _get_source_state(client, "character-sheet-invalid") == before_source_state


def test_rules_query_returns_correct_shape_using_persisted_file_data(client: TestClient) -> None:
    _register_source(
        client,
        source_id="module-file",
        source_kind="module",
        source_format="markdown",
        source_title_zh="雾港旅店",
        document_identity="module-file",
        source_path=FIXTURE_DIR / "scenario_module_sample.md",
        default_priority=45,
    )

    assert client.post("/knowledge/ingest-file", json={"source_id": "module-file"}).status_code == 200
    response = client.post(
        "/rules/query",
        json={"query_text": "聆听检定"},
    )
    assert response.status_code == 200

    result = RuleQueryResult.model_validate(response.json())
    assert result.matched_chunks
    assert result.matched_chunks[0].topic_key == "term:listen"
    assert result.structured_payload["persisted_source_count"] >= 1
    assert (result.chinese_answer_draft or "").startswith("优先参考")


def test_visibility_constraints_hold_with_persisted_file_data(client: TestClient) -> None:
    _register_source(
        client,
        source_id="module-shared-file",
        source_kind="module",
        source_format="markdown",
        source_title_zh="雾港旅店共享线索",
        document_identity="module-shared-file",
        source_path=FIXTURE_DIR / "scenario_module_sample.md",
        default_priority=50,
        default_visibility="shared_subset",
        allowed_player_ids=["investigator-1"],
    )
    assert client.post("/knowledge/ingest-file", json={"source_id": "module-shared-file"}).status_code == 200

    investigator_one = client.post(
        "/rules/query",
        json={"query_text": "地下室", "viewer_role": "investigator", "viewer_id": "investigator-1"},
    )
    investigator_two = client.post(
        "/rules/query",
        json={"query_text": "地下室", "viewer_role": "investigator", "viewer_id": "investigator-2"},
    )
    keeper = client.post(
        "/rules/query",
        json={"query_text": "地下室", "viewer_role": "keeper"},
    )

    assert investigator_one.status_code == 200
    assert investigator_two.status_code == 200
    assert keeper.status_code == 200
    assert investigator_one.json()["matched_chunks"]
    assert investigator_two.json()["matched_chunks"] == []
    assert keeper.json()["matched_chunks"]
