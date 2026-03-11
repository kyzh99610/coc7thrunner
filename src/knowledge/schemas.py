from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from coc_runner.compat import StrEnum
from coc_runner.domain.models import VisibilityScope


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgeSourceKind(StrEnum):
    RULEBOOK = "rulebook"
    CHARACTER_SHEET = "character_sheet"
    HOUSE_RULE = "house_rule"
    MODULE = "module"
    CAMPAIGN_NOTE = "campaign_note"


class KnowledgeSourceFormat(StrEnum):
    PLAIN_TEXT = "plain_text"
    MARKDOWN = "markdown"
    PDF = "pdf"
    JSON = "json"
    CSV = "csv"
    XLSX = "xlsx"


class KnowledgeIngestStatus(StrEnum):
    REGISTERED = "registered"
    INGESTED = "ingested"


class KnowledgeSourceRegistration(BaseModel):
    source_id: str
    source_kind: KnowledgeSourceKind = KnowledgeSourceKind.RULEBOOK
    source_format: KnowledgeSourceFormat = KnowledgeSourceFormat.PLAIN_TEXT
    document_identity: str
    source_path: str | None = None
    source_title_zh: str | None = None
    ruleset: str = "coc7e"
    default_visibility: VisibilityScope = VisibilityScope.PUBLIC
    allowed_player_ids: list[str] = Field(default_factory=list)
    default_priority: int = Field(default=0, ge=0)
    is_authoritative: bool = True


class KnowledgeSourceState(KnowledgeSourceRegistration):
    ingest_status: KnowledgeIngestStatus = KnowledgeIngestStatus.REGISTERED
    chunk_ids: list[str] = Field(default_factory=list)
    chunk_count: int = Field(default=0, ge=0)
    raw_text: str | None = None
    normalized_text: str | None = None
    character_sheet_extraction: CharacterSheetExtraction | None = None
    character_sheet_review: "CharacterImportReview | None" = None
    registered_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_ingested_at: datetime | None = None


class ParsedSourcePage(BaseModel):
    page_number: int = Field(ge=1)
    text: str = ""
    heading: str | None = None


class ParsedSourceDocument(BaseModel):
    source_id: str
    document_identity: str
    source_title_zh: str | None = None
    source_format: KnowledgeSourceFormat = KnowledgeSourceFormat.PLAIN_TEXT
    raw_text: str = ""
    normalized_text: str = ""
    pages: list[ParsedSourcePage] = Field(default_factory=list)


class RuleChunk(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    chunk_id: str
    source_id: str
    ruleset: str = "coc7e"
    topic_key: str = Field(min_length=1)
    taxonomy_category: str
    taxonomy_subcategory: str
    document_identity: str
    source_title_zh: str | None = None
    chapter: str | None = None
    page_reference: int | None = Field(default=None, ge=1)
    content: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    priority: int = Field(ge=0)
    is_authoritative: bool
    title_zh: str = Field(min_length=1)
    short_citation: str | None = None
    # TODO: Keep aligned with coc_runner.domain.models.VisibilityScope until knowledge contracts split out.
    visibility: VisibilityScope = VisibilityScope.PUBLIC
    allowed_player_ids: list[str] = Field(default_factory=list)
    overrides_topic: str | None = None
    machine_flags: list[str] = Field(default_factory=list)
    core_clue_flag: bool = False
    alternate_paths: list[str] = Field(default_factory=list)


class CharacterSheetExtraction(BaseModel):
    REQUIRED_CORE_STATS: ClassVar[tuple[str, ...]] = (
        "strength",
        "constitution",
        "size",
        "dexterity",
        "appearance",
        "intelligence",
        "power",
        "education",
    )

    character_id: str
    investigator_name: str = Field(min_length=1)
    player_name: str | None = None
    occupation: str | None = None
    occupation_sequence_id: str | None = None
    era: str = "1920s"
    age: int | None = Field(default=None, ge=0)
    sex: str | None = None
    residence: str | None = None
    hometown: str | None = None
    core_stats: dict[str, int] = Field(default_factory=dict)
    derived_stats: dict[str, int | str] = Field(default_factory=dict)
    skills: dict[str, int] = Field(default_factory=dict)
    starting_inventory: list[str] = Field(default_factory=list)
    background_traits: str | None = None
    secrets: str | None = None
    campaign_notes: str | None = None
    template_profile: str | None = None
    source_metadata: dict[str, str] = Field(default_factory=dict)
    field_provenance: dict[str, "ImportFieldProvenance"] = Field(default_factory=dict)
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    ambiguous_fields: list[str] = Field(default_factory=list)

    @field_validator("core_stats", "skills")
    @classmethod
    def validate_stat_maps(cls, value: dict[str, int]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for key, score in value.items():
            if not key.strip():
                raise ValueError("character sheet keys must not be blank")
            normalized[key] = int(score)
        return normalized

    @field_validator("derived_stats")
    @classmethod
    def validate_derived_stats(
        cls,
        value: dict[str, int | str],
    ) -> dict[str, int | str]:
        normalized: dict[str, int | str] = {}
        for key, stat_value in value.items():
            if not key.strip():
                raise ValueError("derived stat keys must not be blank")
            normalized[key] = stat_value
        return normalized

    @model_validator(mode="after")
    def validate_required_core_stats(self) -> "CharacterSheetExtraction":
        missing_fields = [
            field_name
            for field_name in self.REQUIRED_CORE_STATS
            if field_name not in self.core_stats
        ]
        if missing_fields:
            raise ValueError(
                f"character sheet is missing required core_stats: {', '.join(missing_fields)}"
            )
        return self


class RetrievedChunk(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    chunk_id: str
    text: str
    topic_key: str
    resolved_topic: str
    page_reference: int | None = None
    short_citation: str | None = None
    visibility: VisibilityScope
    is_authoritative: bool
    priority: int
    core_clue_flag: bool = False
    alternate_paths: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class RuleQueryResult(BaseModel):
    original_query: str
    normalized_query: str | None = None
    matched_chunks: list[RetrievedChunk] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    conflicts_found: bool = False
    conflict_explanation: str | None = None
    human_review_recommended: bool = False
    human_review_reason: str | None = None
    deterministic_resolution_required: bool
    deterministic_handoff_topic: str | None = None
    chinese_answer_draft: str | None = None
    structured_payload: dict[str, object] = Field(default_factory=dict)


class ImportFieldProvenance(BaseModel):
    source_workbook: str
    source_sheet: str
    source_anchor: str | None = None


class CharacterImportReview(BaseModel):
    template_profile_used: str | None = None
    reliably_extracted_fields: list[str] = Field(default_factory=list)
    ambiguous_fields: list[str] = Field(default_factory=list)
    manual_review_required: bool = False
    warnings: list[str] = Field(default_factory=list)


class TextIngestRequest(BaseModel):
    source_id: str
    content: str = Field(min_length=1)


class TextIngestResponse(BaseModel):
    message: str
    source: KnowledgeSourceState
    persisted_chunk_count: int = Field(ge=0)


class KnowledgeSourceResponse(BaseModel):
    message: str
    source: KnowledgeSourceState


class FileIngestRequest(BaseModel):
    source_id: str


class FileIngestResponse(BaseModel):
    message: str
    source: KnowledgeSourceState
    persisted_chunk_count: int = Field(ge=0)


class CharacterSheetImportRequest(BaseModel):
    source_id: str


class CharacterSheetImportResponse(BaseModel):
    message: str
    source: KnowledgeSourceState
    extraction: CharacterSheetExtraction
    review: CharacterImportReview


class RuleQueryRequest(BaseModel):
    query_text: str = Field(min_length=1)
    viewer_role: str = "investigator"
    viewer_id: str | None = None
    minimum_priority: int | None = Field(default=None, ge=0)
    deterministic_resolution_required: bool = False


KnowledgeSourceState.model_rebuild()
CharacterSheetExtraction.model_rebuild()
