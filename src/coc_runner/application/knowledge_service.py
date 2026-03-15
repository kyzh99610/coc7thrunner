from __future__ import annotations

from coc_runner.application.scenario_card_registration import (
    build_scenario_card_source_registration,
    is_supported_scenario_template_card,
    scan_scenario_character_card_files,
)
from coc_runner.application.template_card_import import parse_coc7th_template_card_source
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
    KnowledgeSourceFormat,
    RuleChunk,
    RuleQueryRequest,
    RuleQueryResult,
    ScenarioCardRegistrationStatus,
    ScenarioCardSourceRegistrationItem,
    ScenarioCardSourceRegistrationRequest,
    ScenarioCardSourceRegistrationResponse,
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

    def register_scenario_character_sources(
        self,
        request: ScenarioCardSourceRegistrationRequest,
    ) -> ScenarioCardSourceRegistrationResponse:
        try:
            scan_result = scan_scenario_character_card_files(request.scenario_root_path)
        except FileNotFoundError as exc:
            raise LookupError(
                self._message(
                    "scenario_card_root_not_found",
                    scenario_root_path=request.scenario_root_path,
                )
            ) from exc
        except NotADirectoryError as exc:
            raise ValueError(
                self._message(
                    "scenario_card_root_not_directory",
                    scenario_root_path=request.scenario_root_path,
                )
            ) from exc
        items: list[ScenarioCardSourceRegistrationItem] = []
        for card in scan_result.cards:
            if card.failure_message is not None:
                items.append(
                    ScenarioCardSourceRegistrationItem(
                        category=card.category,
                        file_name=card.file_name,
                        relative_path=card.relative_path,
                        file_path=str(card.file_path),
                        status=ScenarioCardRegistrationStatus.FAILED,
                        template_profile_detected=card.template_profile_detected,
                        message=card.failure_message,
                    )
                )
                continue
            if not is_supported_scenario_template_card(card):
                items.append(
                    ScenarioCardSourceRegistrationItem(
                        category=card.category,
                        file_name=card.file_name,
                        relative_path=card.relative_path,
                        file_path=str(card.file_path),
                        status=ScenarioCardRegistrationStatus.FAILED,
                        template_profile_detected=card.template_profile_detected,
                        message=self._message(
                            "scenario_card_template_not_supported",
                            file_name=card.file_name,
                        ),
                    )
                )
                continue

            register_request = build_scenario_card_source_registration(
                card,
                scenario_root_path=scan_result.scenario_root_path,
            )
            existing_source = self.repository.get_source(register_request.source_id)
            if existing_source is not None:
                items.append(
                    ScenarioCardSourceRegistrationItem(
                        category=card.category,
                        file_name=card.file_name,
                        relative_path=card.relative_path,
                        file_path=str(card.file_path),
                        status=ScenarioCardRegistrationStatus.SKIPPED,
                        source_id=existing_source.source_id,
                        source_title_zh=existing_source.source_title_zh,
                        template_profile_detected=card.template_profile_detected,
                        message=self._message("scenario_card_source_already_registered"),
                    )
                )
                continue

            created_source = self.ingestor.register_source(register_request)
            items.append(
                ScenarioCardSourceRegistrationItem(
                    category=card.category,
                    file_name=card.file_name,
                    relative_path=card.relative_path,
                    file_path=str(card.file_path),
                    status=ScenarioCardRegistrationStatus.REGISTERED,
                    source_id=created_source.source_id,
                    source_title_zh=created_source.source_title_zh,
                    template_profile_detected=card.template_profile_detected,
                    message=self._message("source_registered"),
                )
            )

        registered_count = sum(
            item.status == ScenarioCardRegistrationStatus.REGISTERED for item in items
        )
        skipped_count = sum(
            item.status == ScenarioCardRegistrationStatus.SKIPPED for item in items
        )
        failed_count = sum(
            item.status == ScenarioCardRegistrationStatus.FAILED for item in items
        )
        return ScenarioCardSourceRegistrationResponse(
            message=self._message(
                "scenario_card_sources_registered",
                registered_count=registered_count,
                skipped_count=skipped_count,
                failed_count=failed_count,
            ),
            scenario_root_path=str(scan_result.scenario_root_path),
            sidecars_directory_present=scan_result.sidecars_directory_present,
            items=items,
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
        if source.source_format == KnowledgeSourceFormat.XLSX:
            try:
                extraction = parse_coc7th_template_card_source(source)
                updated_source = source.model_copy(
                    update={"character_sheet_extraction": extraction}
                )
            except ValueError:
                updated_source, extraction = self.ingestor.import_character_sheet(source)
        else:
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

    def list_sources(self) -> list[KnowledgeSourceState]:
        return self.repository.list_sources()

    def get_source_preview(
        self,
        source_id: str,
        *,
        limit: int = 3,
    ) -> tuple[KnowledgeSourceState, list[RuleChunk]]:
        source = self._load_source(source_id)
        return source, self.repository.list_chunks(source_id=source_id)[:limit]

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
            "scenario_card_root_not_found": "未找到 scenario 母文件夹 {scenario_root_path}",
            "scenario_card_root_not_directory": "scenario_root_path {scenario_root_path} 不是可扫描的目录",
            "scenario_card_sources_registered": "已按 scenario 目录完成角色卡资料扫描：{registered_count} 条已登记，{skipped_count} 条跳过，{failed_count} 条失败。",
            "scenario_card_source_already_registered": "知识源已存在，当前不会自动同步或覆盖已登记 source。",
            "scenario_card_template_not_supported": "文件 {file_name} 不是受支持的固定模板卡家族，当前不会登记。",
        }
        en_messages = {
            "source_registered": "Knowledge source registered",
            "text_ingested": "Knowledge text ingested",
            "file_ingested": "Knowledge file ingested",
            "character_sheet_imported": "Character sheet imported",
            "source_not_found": "Knowledge source {source_id} was not found",
            "scenario_card_root_not_found": "Scenario root {scenario_root_path} was not found",
            "scenario_card_root_not_directory": "scenario_root_path {scenario_root_path} is not a directory",
            "scenario_card_sources_registered": "Scenario card scan completed: {registered_count} registered, {skipped_count} skipped, {failed_count} failed.",
            "scenario_card_source_already_registered": "Knowledge source already exists; this scan does not auto-sync or overwrite existing sources.",
            "scenario_card_template_not_supported": "File {file_name} is not a supported fixed template-card family and was not registered.",
        }
        catalog = (
            zh_messages
            if self.default_language == LanguagePreference.ZH_CN
            else en_messages
        )
        return catalog[key].format(**values)
