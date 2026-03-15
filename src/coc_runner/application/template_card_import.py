from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from pydantic import BaseModel, Field

from coc_runner.domain.models import ImportCharacterHookSeedRequest, LanguagePreference
from knowledge.ingest import KnowledgeIngestor
from knowledge.schemas import (
    CharacterSheetExtraction,
    ImportFieldProvenance,
    KnowledgeSourceFormat,
    KnowledgeSourceState,
)


COC7TH_TEMPLATE_CARD_PROFILE = KnowledgeIngestor.INTEGRATED_CHARACTER_WORKBOOK_PROFILE
_DEFINED_NAME_TARGET_PATTERN = re.compile(r"^'?([^']+)'?!\$?([A-Z]+\$?\d+)$")


class Coc7thTemplateWorkbookInspection(BaseModel):
    source_path: str
    sheet_names: list[str] = Field(default_factory=list)
    defined_names: dict[str, str] = Field(default_factory=dict)
    detected_template_profile: str | None = None
    main_sheet_name: str | None = None
    summary_sheet_names: list[str] = Field(default_factory=list)
    helper_sheet_names: list[str] = Field(default_factory=list)


def inspect_coc7th_template_workbook(source_path: str | Path) -> Coc7thTemplateWorkbookInspection:
    resolved_path = Path(source_path)
    with zipfile.ZipFile(resolved_path) as workbook:
        shared_strings = KnowledgeIngestor._read_xlsx_shared_strings(workbook)
        sheet_paths = KnowledgeIngestor._read_xlsx_sheet_paths(workbook)
        detected_template_profile = KnowledgeIngestor._detect_integrated_character_workbook_profile(
            workbook,
            sheet_paths=sheet_paths,
            shared_strings=shared_strings,
        )
        defined_names = _read_defined_names(workbook)
    return Coc7thTemplateWorkbookInspection(
        source_path=str(resolved_path),
        sheet_names=list(sheet_paths.keys()),
        defined_names=defined_names,
        detected_template_profile=detected_template_profile,
        main_sheet_name=(
            KnowledgeIngestor.INTEGRATED_CHARACTER_MAIN_SHEET
            if KnowledgeIngestor.INTEGRATED_CHARACTER_MAIN_SHEET in sheet_paths
            else None
        ),
        summary_sheet_names=[
            sheet_name
            for sheet_name in KnowledgeIngestor.INTEGRATED_CHARACTER_SUMMARY_SHEETS
            if sheet_name in sheet_paths
        ],
        helper_sheet_names=[
            sheet_name
            for sheet_name in KnowledgeIngestor.INTEGRATED_CHARACTER_HELPER_SHEETS
            if sheet_name in sheet_paths
        ],
    )


def parse_coc7th_template_card_source(source: KnowledgeSourceState) -> CharacterSheetExtraction:
    if source.source_format != KnowledgeSourceFormat.XLSX:
        raise ValueError("固定模板卡解析器只支持 xlsx 角色卡来源。")

    extraction = KnowledgeIngestor._parse_character_sheet_xlsx(source)
    if extraction.template_profile != COC7TH_TEMPLATE_CARD_PROFILE:
        raise ValueError("当前知识源不是受支持的固定模板卡，不能走 COC七版规则空白卡 专用 parser。")

    luck_value = _extract_defined_stat_value(
        Path(source.source_path or ""),
        defined_name="Luck",
        fallback_sheet=KnowledgeIngestor.INTEGRATED_CHARACTER_MAIN_SHEET,
        fallback_cell="AG7",
    )
    if luck_value is None:
        return extraction

    updated_derived_stats = dict(extraction.derived_stats)
    updated_derived_stats["luck"] = luck_value
    updated_field_provenance = dict(extraction.field_provenance)
    updated_field_provenance.setdefault(
        "derived_stats.luck",
        ImportFieldProvenance(
            source_workbook=Path(source.source_path or "").name,
            source_sheet=KnowledgeIngestor.INTEGRATED_CHARACTER_MAIN_SHEET,
            source_anchor="AG7",
        ),
    )
    return extraction.model_copy(
        update={
            "derived_stats": updated_derived_stats,
            "field_provenance": updated_field_provenance,
        }
    )


def build_character_hook_seed_request_from_template_card(
    extraction: CharacterSheetExtraction,
    *,
    operator_id: str,
    seed_hint: str | None = None,
    language_preference: LanguagePreference | None = None,
) -> ImportCharacterHookSeedRequest:
    occupation = (extraction.occupation or "").strip()
    if not occupation:
        raise ValueError("固定模板卡解析结果缺少稳定的职业字段，当前不能生成角色 hook seed。")

    notes = _trim_hook_notes(
        extraction.secrets,
        extraction.background_traits,
        extraction.campaign_notes,
    )
    return ImportCharacterHookSeedRequest(
        operator_id=operator_id,
        occupation=occupation,
        notes=notes,
        seed_hint=seed_hint,
        language_preference=language_preference,
    )


def _read_defined_names(workbook: zipfile.ZipFile) -> dict[str, str]:
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    workbook_root = ET.fromstring(workbook.read("xl/workbook.xml"))
    defined_names_parent = workbook_root.find("main:definedNames", namespace)
    if defined_names_parent is None:
        return {}
    defined_names: dict[str, str] = {}
    for defined_name in defined_names_parent.findall("main:definedName", namespace):
        name = defined_name.attrib.get("name")
        target = (defined_name.text or "").strip()
        if name and target:
            defined_names[name] = target
    return defined_names


def _extract_defined_stat_value(
    source_path: Path,
    *,
    defined_name: str,
    fallback_sheet: str,
    fallback_cell: str,
) -> int | None:
    with zipfile.ZipFile(source_path) as workbook:
        shared_strings = KnowledgeIngestor._read_xlsx_shared_strings(workbook)
        sheet_paths = KnowledgeIngestor._read_xlsx_sheet_paths(workbook)
        defined_names = _read_defined_names(workbook)
        resolved_sheet_name = fallback_sheet
        resolved_cell = fallback_cell
        target = defined_names.get(defined_name)
        if target is not None:
            match = _DEFINED_NAME_TARGET_PATTERN.match(target.replace("$", ""))
            if match is not None:
                resolved_sheet_name = match.group(1)
                resolved_cell = match.group(2)
        worksheet_path = sheet_paths.get(resolved_sheet_name)
        if worksheet_path is None:
            return None
        snapshot = KnowledgeIngestor._read_xlsx_sheet_snapshot(
            workbook,
            sheet_name=resolved_sheet_name,
            worksheet_path=worksheet_path,
            shared_strings=shared_strings,
        )
        value = KnowledgeIngestor._optional_sheet_scalar(snapshot, resolved_cell)
    return int(value) if isinstance(value, int) else None


def _trim_hook_notes(*parts: str | None, max_length: int = 200) -> str | None:
    normalized_parts = [part.strip() for part in parts if isinstance(part, str) and part.strip()]
    if not normalized_parts:
        return None
    joined = " ".join(normalized_parts)
    if len(joined) <= max_length:
        return joined
    return joined[: max_length - 1].rstrip() + "…"
