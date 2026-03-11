from __future__ import annotations

from knowledge.ingest import KnowledgeIngestor
from knowledge.retrieval import KnowledgeRetriever
from knowledge.schemas import (
    CharacterImportReview,
    CharacterSheetExtraction,
    CharacterSheetImportRequest,
    CharacterSheetImportResponse,
    FileIngestRequest,
    FileIngestResponse,
    KnowledgeSourceRegistration,
    KnowledgeSourceResponse,
    KnowledgeSourceState,
    RuleQueryRequest,
    RuleQueryResult,
    TextIngestRequest,
    TextIngestResponse,
)

from coc_runner.domain.models import LanguagePreference
from coc_runner.infrastructure.knowledge_repositories import KnowledgeRepository


class KnowledgeService:
    def __init__(
        self,
        repository: KnowledgeRepository,
        *,
        default_language: LanguagePreference = LanguagePreference.ZH_CN,
    ) -> None:
        self.repository = repository
        self.default_language = default_language
        self.ingestor = KnowledgeIngestor(storage=repository)

    def register_source(
        self,
        request: KnowledgeSourceRegistration,
    ) -> KnowledgeSourceResponse:
        source = self.ingestor.register_source(request)
        return KnowledgeSourceResponse(
            message=self._message("source_registered"),
            source=source,
        )

    def ingest_text(
        self,
        request: TextIngestRequest,
    ) -> TextIngestResponse:
        source = self._load_source(request.source_id)
        updated_source, persisted_chunks = self.ingestor.ingest_text(source, request.content)
        return TextIngestResponse(
            message=self._message("text_ingested"),
            source=updated_source,
            persisted_chunk_count=len(persisted_chunks),
        )

    def ingest_file(
        self,
        request: FileIngestRequest,
    ) -> FileIngestResponse:
        source = self._load_source(request.source_id)
        updated_source, persisted_chunks = self.ingestor.ingest_file(source)
        return FileIngestResponse(
            message=self._message("file_ingested"),
            source=updated_source,
            persisted_chunk_count=len(persisted_chunks),
        )

    def import_character_sheet(
        self,
        request: CharacterSheetImportRequest,
    ) -> CharacterSheetImportResponse:
        source = self._load_source(request.source_id)
        updated_source, extraction = self.ingestor.import_character_sheet(source)
        review = self._build_character_import_review(extraction)
        updated_source = updated_source.model_copy(
            update={"character_sheet_review": review}
        )
        self.repository.save_source(updated_source)
        return CharacterSheetImportResponse(
            message=self._message("character_sheet_imported"),
            source=updated_source,
            extraction=extraction,
            review=review,
        )

    def get_source(self, source_id: str) -> KnowledgeSourceState:
        return self._load_source(source_id)

    def query_rules(
        self,
        request: RuleQueryRequest,
    ) -> RuleQueryResult:
        persisted_chunks = self.repository.list_chunks()
        retriever = KnowledgeRetriever(persisted_chunks)
        result = retriever.query_rules(
            request.query_text,
            viewer_role=request.viewer_role,
            viewer_id=request.viewer_id,
            minimum_priority=request.minimum_priority,
            deterministic_resolution_required=request.deterministic_resolution_required,
        )
        result.structured_payload = {
            **result.structured_payload,
            "persisted_source_count": len(
                {chunk.source_id for chunk in persisted_chunks}
            ),
        }
        return result

    def _load_source(self, source_id: str) -> KnowledgeSourceState:
        source = self.repository.get_source(source_id)
        if source is None:
            raise LookupError(self._message("source_not_found", source_id=source_id))
        return source

    def _build_character_import_review(
        self,
        extraction: CharacterSheetExtraction,
    ) -> CharacterImportReview:
        warnings: list[str] = []
        ambiguous_fields = list(extraction.ambiguous_fields)
        placeholder_skills = sorted(
            {
                field.removeprefix("skill_placeholder:")
                for field in ambiguous_fields
                if field.startswith("skill_placeholder:")
            }
        )
        if placeholder_skills:
            warnings.append(
                "检测到未填写的模板占位技能："
                + "、".join(placeholder_skills)
                + "，需要人工确认后再补全。"
            )

        formula_dependent_fields = sorted(
            {
                field.split(":", maxsplit=2)[1]
                for field in ambiguous_fields
                if field.startswith("formula_without_cached:")
            }
        )
        if formula_dependent_fields:
            warnings.append(
                "以下字段依赖公式但缺少缓存值："
                + "、".join(formula_dependent_fields)
                + "。请在 Excel 中重新计算后复核。"
            )

        if any(field.startswith("summary_sheet:") for field in ambiguous_fields):
            warnings.append("未检测到简化卡摘要页，本次导入仅依据人物卡主表。")

        if extraction.template_profile == self.ingestor.INTEGRATED_CHARACTER_WORKBOOK_PROFILE:
            warnings.append("简化卡与辅助表当前仅作补充参考，仍需 KP 人工复核关键字段。")

        occupation_mismatch_warnings = [
            field for field in ambiguous_fields if field.startswith("occupation_helper_mismatch:")
        ]
        for field in occupation_mismatch_warnings:
            _, sequence_id, helper_name, actual_name = field.split(":", maxsplit=3)
            warnings.append(
                f"职业序号 {sequence_id} 在辅助表中对应“{helper_name}”，但人物卡主表填写为“{actual_name}”。"
            )

        for field in ambiguous_fields:
            if field.startswith("occupation_helper_sequence_not_found:"):
                sequence_id = field.split(":", maxsplit=1)[1]
                warnings.append(f"职业列表中未找到职业序号 {sequence_id}，请人工核对职业模板。")
            if field.startswith("occupation_helper_name_not_found:"):
                occupation_name = field.split(":", maxsplit=1)[1]
                warnings.append(f"辅助表中未找到职业“{occupation_name}”，请人工核对。")
            if field.startswith("occupation_skill_expectation_missing:"):
                missing_skills = [skill for skill in field.split(":")[1:] if skill]
                if missing_skills:
                    warnings.append(
                        "辅助表职业模板期望本职技能可能包括："
                        + "、".join(missing_skills[:4])
                        + "。当前人物卡未可靠匹配这些技能，建议人工复核。"
                    )

        return CharacterImportReview(
            template_profile_used=extraction.template_profile,
            reliably_extracted_fields=self._collect_reliably_extracted_fields(extraction),
            ambiguous_fields=ambiguous_fields,
            manual_review_required=bool(
                extraction.template_profile is not None or ambiguous_fields or warnings
            ),
            warnings=warnings,
        )

    @staticmethod
    def _collect_reliably_extracted_fields(
        extraction: CharacterSheetExtraction,
    ) -> list[str]:
        reliable_fields: list[str] = []
        for field_name in (
            "investigator_name",
            "player_name",
            "occupation",
            "occupation_sequence_id",
            "era",
            "age",
            "sex",
            "residence",
            "hometown",
        ):
            value = getattr(extraction, field_name)
            if value not in {None, ""}:
                reliable_fields.append(field_name)
        for field_name in (
            "core_stats",
            "derived_stats",
            "skills",
            "starting_inventory",
            "background_traits",
            "secrets",
            "campaign_notes",
        ):
            value = getattr(extraction, field_name)
            if value:
                reliable_fields.append(field_name)
        return reliable_fields

    def _message(self, key: str, **values: object) -> str:
        zh_messages = {
            "source_registered": "知识源已注册",
            "text_ingested": "文本知识已入库",
            "file_ingested": "文件知识已入库",
            "character_sheet_imported": "角色卡已导入",
            "source_not_found": "未找到知识源 {source_id}",
        }
        en_messages = {
            "source_registered": "Knowledge source registered",
            "text_ingested": "Knowledge text ingested",
            "file_ingested": "Knowledge file ingested",
            "character_sheet_imported": "Character sheet imported",
            "source_not_found": "Knowledge source {source_id} was not found",
        }
        catalog = (
            zh_messages
            if self.default_language == LanguagePreference.ZH_CN
            else en_messages
        )
        return catalog[key].format(**values)
