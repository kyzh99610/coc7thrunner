from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
import re

from knowledge.schemas import (
    KnowledgeSourceFormat,
    KnowledgeSourceKind,
    KnowledgeSourceRegistration,
    ScenarioCardCategory,
)

from coc_runner.application.template_card_import import (
    COC7TH_TEMPLATE_CARD_PROFILE,
    inspect_coc7th_template_workbook,
)


_SCENARIO_CARD_DIRECTORIES: tuple[tuple[ScenarioCardCategory, str], ...] = (
    (ScenarioCardCategory.INVESTIGATOR, "investigators"),
    (ScenarioCardCategory.OWNED_NPC, "owned_npcs"),
)
_NON_ALNUM_PATTERN = re.compile(r"[^a-z0-9]+")


@dataclass(slots=True)
class DiscoveredScenarioCard:
    category: ScenarioCardCategory
    file_path: Path
    relative_path: str
    file_name: str
    file_stem: str
    template_profile_detected: str | None = None
    failure_message: str | None = None


@dataclass(slots=True)
class ScenarioCardScanResult:
    scenario_root_path: Path
    sidecars_directory_present: bool
    cards: list[DiscoveredScenarioCard]


def scan_scenario_character_card_files(scenario_root_path: str | Path) -> ScenarioCardScanResult:
    resolved_root = Path(scenario_root_path).expanduser().resolve()
    if not resolved_root.exists():
        raise FileNotFoundError(f"scenario root {resolved_root} was not found")
    if not resolved_root.is_dir():
        raise NotADirectoryError(f"scenario root {resolved_root} is not a directory")

    cards: list[DiscoveredScenarioCard] = []
    for category, directory_name in _SCENARIO_CARD_DIRECTORIES:
        category_directory = resolved_root / directory_name
        if not category_directory.is_dir():
            continue
        for file_path in sorted(category_directory.iterdir(), key=lambda path: path.name.lower()):
            if not file_path.is_file() or file_path.suffix.lower() != ".xlsx":
                continue
            relative_path = file_path.relative_to(resolved_root).as_posix()
            try:
                inspection = inspect_coc7th_template_workbook(file_path)
                cards.append(
                    DiscoveredScenarioCard(
                        category=category,
                        file_path=file_path,
                        relative_path=relative_path,
                        file_name=file_path.name,
                        file_stem=file_path.stem,
                        template_profile_detected=inspection.detected_template_profile,
                    )
                )
            except Exception as exc:
                cards.append(
                    DiscoveredScenarioCard(
                        category=category,
                        file_path=file_path,
                        relative_path=relative_path,
                        file_name=file_path.name,
                        file_stem=file_path.stem,
                        failure_message=f"固定模板卡检查失败：{exc}",
                    )
                )
    return ScenarioCardScanResult(
        scenario_root_path=resolved_root,
        sidecars_directory_present=(resolved_root / "sidecars").is_dir(),
        cards=cards,
    )


def build_scenario_card_source_registration(
    card: DiscoveredScenarioCard,
    *,
    scenario_root_path: Path,
) -> KnowledgeSourceRegistration:
    source_id = _build_scenario_card_source_id(
        category=card.category,
        scenario_root_path=scenario_root_path,
        relative_path=card.relative_path,
        file_stem=card.file_stem,
    )
    category_label = _scenario_card_category_label(card.category)
    scenario_root_name = scenario_root_path.name.strip() or "scenario"
    return KnowledgeSourceRegistration(
        source_id=source_id,
        source_kind=KnowledgeSourceKind.CHARACTER_SHEET,
        source_format=KnowledgeSourceFormat.XLSX,
        document_identity=f"scenario-card:{scenario_root_name}:{card.category.value}:{card.relative_path}",
        source_path=str(card.file_path),
        source_title_zh=f"{category_label}：{card.file_stem}",
        source_metadata={
            "registration_mode": "scenario_folder_scan",
            "scenario_root_name": scenario_root_name,
            "scenario_root_path": str(scenario_root_path),
            "card_category": card.category.value,
            "relative_path": card.relative_path,
            "file_name": card.file_name,
            "file_stem": card.file_stem,
            "template_profile_detected": card.template_profile_detected or "",
        },
        default_priority=0,
        is_authoritative=False,
    )


def is_supported_scenario_template_card(card: DiscoveredScenarioCard) -> bool:
    return (
        card.failure_message is None
        and card.template_profile_detected == COC7TH_TEMPLATE_CARD_PROFILE
    )


def _build_scenario_card_source_id(
    *,
    category: ScenarioCardCategory,
    scenario_root_path: Path,
    relative_path: str,
    file_stem: str,
) -> str:
    digest = sha1(
        f"{scenario_root_path.as_posix()}::{category.value}::{relative_path}".encode("utf-8")
    ).hexdigest()[:8]
    slug = _slugify_source_stem(file_stem) or "card"
    return f"scenario-card-{category.value}-{slug}-{digest}"


def _slugify_source_stem(file_stem: str) -> str:
    normalized = _NON_ALNUM_PATTERN.sub("-", file_stem.lower()).strip("-")
    return normalized[:40]


def _scenario_card_category_label(category: ScenarioCardCategory) -> str:
    if category == ScenarioCardCategory.OWNED_NPC:
        return "自车 NPC 角色卡"
    return "调查员角色卡"
