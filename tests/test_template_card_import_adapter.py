from __future__ import annotations

from pathlib import Path

import pytest

from coc_runner.application.template_card_import import (
    COC7TH_TEMPLATE_CARD_PROFILE,
    build_character_hook_seed_request_from_template_card,
    inspect_coc7th_template_workbook,
    parse_coc7th_template_card_source,
)
from knowledge.schemas import KnowledgeSourceFormat, KnowledgeSourceKind, KnowledgeSourceState


TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "coc7th rules and templates"
CANONICAL_TEMPLATE_PATH = TEMPLATE_DIR / "COC七版规则空白卡.xlsx"
SAMPLE_TEMPLATE_DIR = TEMPLATE_DIR / "sample templates"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "knowledge"


def _build_character_sheet_source(path: Path, *, source_id: str) -> KnowledgeSourceState:
    return KnowledgeSourceState(
        source_id=source_id,
        source_kind=KnowledgeSourceKind.CHARACTER_SHEET,
        source_format=KnowledgeSourceFormat.XLSX,
        source_title_zh="模板角色卡",
        document_identity=source_id,
        source_path=str(path),
        default_priority=0,
        is_authoritative=False,
    )


def test_inspect_canonical_coc7th_template_workbook_reports_expected_structure() -> None:
    inspection = inspect_coc7th_template_workbook(CANONICAL_TEMPLATE_PATH)

    assert inspection.detected_template_profile == COC7TH_TEMPLATE_CARD_PROFILE
    assert inspection.sheet_names == [
        "人物卡",
        "简化卡",
        "本职技能",
        "技能注释",
        "附表",
        "职业列表",
        "属性注释",
        "资产及物价参考",
        "武器列表",
        "防具表 载具表",
        "疯狂表",
        "更新说明",
    ]
    assert inspection.main_sheet_name == "人物卡"
    assert inspection.summary_sheet_names == ["简化卡"]
    assert inspection.helper_sheet_names == ["职业列表", "附表", "本职技能"]
    assert inspection.defined_names["STR"] == "人物卡!$U$3"
    assert inspection.defined_names["EDU"] == "人物卡!$AG$5"
    assert inspection.defined_names["Luck"] == "人物卡!$AG$7"


def test_parse_coc7th_template_card_extracts_stable_fields_from_integrated_sample() -> None:
    extraction = parse_coc7th_template_card_source(
        _build_character_sheet_source(
            SAMPLE_TEMPLATE_DIR / "Bruce vain.xlsx",
            source_id="template-card-bruce",
        )
    )

    assert extraction.template_profile == COC7TH_TEMPLATE_CARD_PROFILE
    assert extraction.investigator_name == "布鲁斯·维恩"
    assert extraction.occupation == "总裁"
    assert extraction.age == 22
    assert extraction.core_stats == {
        "strength": 50,
        "dexterity": 50,
        "power": 60,
        "constitution": 60,
        "appearance": 70,
        "education": 75,
        "size": 85,
        "intelligence": 75,
    }
    assert extraction.derived_stats["hp"] == 14
    assert extraction.derived_stats["mp"] == 12
    assert extraction.derived_stats["san"] == 60
    assert extraction.derived_stats["luck"] == 80
    assert extraction.skills["侦查"] == 70
    assert extraction.skills["德语"] == 31
    assert extraction.starting_inventory
    assert extraction.secrets == "小秘密：腹部的一条刀疤，被抢劫捅的"
    assert extraction.field_provenance["investigator_name"].source_anchor == "E3"
    assert extraction.field_provenance["derived_stats.luck"].source_anchor == "AG7"


def test_parse_coc7th_template_card_rejects_non_template_workbook() -> None:
    source = _build_character_sheet_source(
        FIXTURE_DIR / "character_sheet_sample.xlsx",
        source_id="non-template-xlsx",
    )

    with pytest.raises(ValueError, match="固定模板卡"):
        parse_coc7th_template_card_source(source)


def test_template_card_adapter_builds_character_hook_seed_request_from_extraction() -> None:
    extraction = parse_coc7th_template_card_source(
        _build_character_sheet_source(
            SAMPLE_TEMPLATE_DIR / "Bruce vain.xlsx",
            source_id="template-card-hook-seed",
        )
    )

    request = build_character_hook_seed_request_from_template_card(
        extraction,
        operator_id="keeper-1",
        seed_hint="模板卡执念",
    )

    assert request.operator_id == "keeper-1"
    assert request.occupation == "总裁"
    assert request.seed_hint == "模板卡执念"
    assert request.notes is not None
    assert "小秘密：腹部的一条刀疤" in request.notes
