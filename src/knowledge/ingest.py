from __future__ import annotations

import csv
import json
import re
import zlib
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from xml.etree import ElementTree as ET
import zipfile

from knowledge.schemas import (
    CharacterSheetExtraction,
    ImportFieldProvenance,
    KnowledgeIngestStatus,
    KnowledgeSourceRegistration,
    KnowledgeSourceState,
    ParsedSourceDocument,
    ParsedSourcePage,
    RuleChunk,
    KnowledgeSourceFormat,
)
from knowledge.terminology import extract_term_matches, normalize_chinese_text


@dataclass(slots=True)
class XlsxCellSnapshot:
    value: str
    formula: str | None = None


@dataclass(slots=True)
class XlsxSheetSnapshot:
    name: str
    cells: dict[str, XlsxCellSnapshot]
    merged_ranges: list[str]


class KnowledgeStorage(Protocol):
    def create_source(self, source: KnowledgeSourceState) -> None:
        ...

    def save_source(self, source: KnowledgeSourceState) -> None:
        ...

    def replace_chunks(self, source_id: str, chunks: list[RuleChunk]) -> None:
        ...


class KnowledgeIngestor:
    """Text-first knowledge ingestion workflow for the MVP retrieval foundation."""

    INTEGRATED_CHARACTER_WORKBOOK_PROFILE = "coc7th_integrated_workbook_v1"
    INTEGRATED_CHARACTER_MAIN_SHEET = "人物卡"
    INTEGRATED_CHARACTER_SUMMARY_SHEETS = ("简化卡 骰娘导入", "简化卡")
    INTEGRATED_CHARACTER_HELPER_SHEETS = ("职业列表", "附表", "本职技能")
    INTEGRATED_CHARACTER_MAIN_ANCHORS = {
        "B3": "姓名",
        "B4": "玩家",
        "B5": "职业",
        "J4": "时代",
        "J5": "职业序号",
        "B10": ("生命值", "体力"),
        "K10": "理智",
        "T10": "魔法",
        "F15": "技能名称",
        "AB15": "技能名称",
    }

    def __init__(self, storage: KnowledgeStorage | None = None) -> None:
        self.storage = storage
        self._registered_sources: dict[str, KnowledgeSourceState] = {}
        self._persisted_chunks: dict[str, RuleChunk] = {}

    def register_source(
        self,
        source: KnowledgeSourceRegistration,
    ) -> KnowledgeSourceState:
        """Register source metadata before parsing or chunking begins."""

        state = KnowledgeSourceState.model_validate(source.model_dump())
        self._registered_sources[state.source_id] = state
        if self.storage is not None:
            self.storage.create_source(state)
        return state

    def parse_source(
        self,
        source: KnowledgeSourceState,
        *,
        raw_text: str,
    ) -> ParsedSourceDocument:
        """Parse a text or markdown source into the common parsed representation.

        TODO:
        - Add PDF parsing.
        - Add spreadsheet extraction.
        - Preserve page anchors once non-text parsers exist.
        """

        return ParsedSourceDocument(
            source_id=source.source_id,
            document_identity=source.document_identity,
            source_title_zh=source.source_title_zh,
            source_format=source.source_format,
            raw_text=raw_text,
            normalized_text="",
        )

    def parse_source_file(
        self,
        source: KnowledgeSourceState,
    ) -> ParsedSourceDocument:
        """Parse a registered file-backed source into text-first content.

        TODO:
        - Replace the shallow PDF extractor with a compliant parser.
        - Add richer layout recovery once rulebook-specific heuristics are available.
        """

        source_path = self._require_source_path(source)
        if source.source_format in {
            KnowledgeSourceFormat.PLAIN_TEXT,
            KnowledgeSourceFormat.MARKDOWN,
        }:
            raw_text = source_path.read_text(encoding="utf-8")
            return self.parse_source(source, raw_text=raw_text)

        if source.source_format == KnowledgeSourceFormat.PDF:
            raw_bytes = source_path.read_bytes()
            pages = self._extract_pdf_pages(raw_bytes)
            raw_text = "\n\n".join(page.text for page in pages if page.text.strip())
            return ParsedSourceDocument(
                source_id=source.source_id,
                document_identity=source.document_identity,
                source_title_zh=source.source_title_zh,
                source_format=source.source_format,
                raw_text=raw_text,
                normalized_text="",
                pages=pages,
            )

        raise ValueError(f"source format {source.source_format.value} does not support rule ingestion")

    def normalize_source_text(
        self,
        parsed_source: ParsedSourceDocument,
    ) -> ParsedSourceDocument:
        """Apply display-term normalization without introducing developer IDs."""

        normalized = parsed_source.model_copy(deep=True)
        normalized.normalized_text = normalize_chinese_text(parsed_source.raw_text)
        if normalized.pages:
            normalized.pages = [
                page.model_copy(
                    update={
                        "text": normalize_chinese_text(page.text),
                        "heading": (
                            normalize_chinese_text(page.heading)
                            if page.heading
                            else None
                        ),
                    }
                )
                for page in normalized.pages
            ]
        return normalized

    def chunk_source(
        self,
        source: KnowledgeSourceState,
        parsed_source: ParsedSourceDocument,
    ) -> list[RuleChunk]:
        """Split normalized text into deterministic sections and chunks."""

        if parsed_source.source_format == KnowledgeSourceFormat.PDF and parsed_source.pages:
            return self._chunk_pdf_pages(source, parsed_source)

        sections = self._split_sections(
            parsed_source.normalized_text,
            is_markdown=parsed_source.source_format.value == "markdown",
        )
        chunks: list[RuleChunk] = []
        for section_index, (section_title, section_content) in enumerate(sections, start=1):
            clean_content = section_content.strip()
            if not clean_content:
                continue
            title_zh = section_title or source.source_title_zh or source.document_identity
            chapter = section_title
            topic_key, taxonomy_category, taxonomy_subcategory, tags, machine_flags = (
                self._resolve_topic_metadata(title_zh, clean_content, source.source_id, section_index)
            )
            chunks.append(
                RuleChunk(
                    chunk_id=f"{source.source_id}-chunk-{section_index}",
                    source_id=source.source_id,
                    ruleset=source.ruleset,
                    topic_key=topic_key,
                    taxonomy_category=taxonomy_category,
                    taxonomy_subcategory=taxonomy_subcategory,
                    document_identity=source.document_identity,
                    source_title_zh=source.source_title_zh,
                    chapter=chapter,
                    content=clean_content,
                    tags=tags,
                    priority=source.default_priority,
                    is_authoritative=source.is_authoritative,
                    title_zh=title_zh,
                    short_citation=self._build_short_citation(
                        source,
                        chapter=chapter,
                        section_index=section_index,
                    ),
                    visibility=source.default_visibility,
                    allowed_player_ids=list(source.allowed_player_ids),
                    machine_flags=machine_flags,
                )
            )
        return chunks

    def enrich_chunks(
        self,
        source: KnowledgeSourceState,
        chunks: list[RuleChunk],
    ) -> list[RuleChunk]:
        """Add deterministic metadata derived from source-level defaults."""

        enriched_chunks: list[RuleChunk] = []
        for chunk in chunks:
            enriched = chunk.model_copy(deep=True)
            enriched.machine_flags = list(dict.fromkeys(
                [
                    *enriched.machine_flags,
                    f"source_kind:{source.source_kind.value}",
                    f"source_format:{source.source_format.value}",
                ]
            ))
            enriched.tags = list(dict.fromkeys(enriched.tags))
            enriched_chunks.append(enriched)
        return enriched_chunks

    def validate_chunks(
        self,
        chunks: list[RuleChunk],
    ) -> list[RuleChunk]:
        """Validate chunk schema before persistence."""

        return [RuleChunk.model_validate(chunk.model_dump()) for chunk in chunks]

    def persist_chunks(
        self,
        source: KnowledgeSourceState,
        chunks: list[RuleChunk],
    ) -> list[RuleChunk]:
        """Persist chunks to the configured store.

        TODO:
        - Add chunk versioning and source invalidation.
        - Add repository-backed diffing once non-text ingest lands.
        """

        for chunk in chunks:
            self._persisted_chunks[chunk.chunk_id] = deepcopy(chunk)
        if self.storage is not None:
            self.storage.replace_chunks(source.source_id, chunks)
        return list(chunks)

    def ingest_text(
        self,
        source: KnowledgeSourceState,
        raw_text: str,
    ) -> tuple[KnowledgeSourceState, list[RuleChunk]]:
        """Run the text-first ingest pipeline end to end for a registered source."""

        parsed_source = self.parse_source(source, raw_text=raw_text)
        normalized_source = self.normalize_source_text(parsed_source)
        chunked_source = self.chunk_source(source, normalized_source)
        enriched_chunks = self.enrich_chunks(source, chunked_source)
        validated_chunks = self.validate_chunks(enriched_chunks)
        persisted_chunks = self.persist_chunks(source, validated_chunks)

        updated_source = source.model_copy(deep=True)
        updated_source.raw_text = raw_text
        updated_source.normalized_text = normalized_source.normalized_text
        updated_source.chunk_ids = [chunk.chunk_id for chunk in persisted_chunks]
        updated_source.chunk_count = len(persisted_chunks)
        updated_source.ingest_status = KnowledgeIngestStatus.INGESTED
        current_time = datetime.now(timezone.utc)
        updated_source.last_ingested_at = current_time
        updated_source.updated_at = current_time
        if self.storage is not None:
            self.storage.save_source(updated_source)
        self._registered_sources[updated_source.source_id] = updated_source
        return updated_source, persisted_chunks

    def ingest_file(
        self,
        source: KnowledgeSourceState,
    ) -> tuple[KnowledgeSourceState, list[RuleChunk]]:
        """Run the registered file-backed ingest pipeline end to end."""

        parsed_source = self.parse_source_file(source)
        normalized_source = self.normalize_source_text(parsed_source)
        chunked_source = self.chunk_source(source, normalized_source)
        enriched_chunks = self.enrich_chunks(source, chunked_source)
        validated_chunks = self.validate_chunks(enriched_chunks)
        persisted_chunks = self.persist_chunks(source, validated_chunks)

        updated_source = source.model_copy(deep=True)
        updated_source.raw_text = parsed_source.raw_text
        updated_source.normalized_text = normalized_source.normalized_text
        updated_source.chunk_ids = [chunk.chunk_id for chunk in persisted_chunks]
        updated_source.chunk_count = len(persisted_chunks)
        updated_source.ingest_status = KnowledgeIngestStatus.INGESTED
        current_time = datetime.now(timezone.utc)
        updated_source.last_ingested_at = current_time
        updated_source.updated_at = current_time
        if self.storage is not None:
            self.storage.save_source(updated_source)
        self._registered_sources[updated_source.source_id] = updated_source
        return updated_source, persisted_chunks

    def import_character_sheet(
        self,
        source: KnowledgeSourceState,
    ) -> tuple[KnowledgeSourceState, CharacterSheetExtraction]:
        """Import a structured character sheet from JSON, CSV, or XLSX.

        TODO:
        - Align imported fields with the domain Character model more tightly.
        """

        source_path = self._require_source_path(source)
        if source.source_format == KnowledgeSourceFormat.JSON:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            extraction = CharacterSheetExtraction.model_validate(payload)
        elif source.source_format == KnowledgeSourceFormat.CSV:
            extraction = self._parse_character_sheet_csv(source_path)
        elif source.source_format == KnowledgeSourceFormat.XLSX:
            extraction = self._parse_character_sheet_xlsx(source)
        else:
            raise ValueError(
                f"source format {source.source_format.value} does not support character-sheet import"
            )

        updated_source = source.model_copy(deep=True)
        updated_source.character_sheet_extraction = extraction
        current_time = datetime.now(timezone.utc)
        updated_source.ingest_status = KnowledgeIngestStatus.INGESTED
        updated_source.last_ingested_at = current_time
        updated_source.updated_at = current_time
        if self.storage is not None:
            self.storage.save_source(updated_source)
        self._registered_sources[updated_source.source_id] = updated_source
        return updated_source, extraction

    @staticmethod
    def _split_sections(raw_text: str, *, is_markdown: bool) -> list[tuple[str | None, str]]:
        if not raw_text.strip():
            return []
        if is_markdown:
            sections = KnowledgeIngestor._split_markdown_sections(raw_text)
            if sections:
                return sections
        return [(None, section) for section in re.split(r"\n\s*\n+", raw_text) if section.strip()]

    @staticmethod
    def _split_markdown_sections(raw_text: str) -> list[tuple[str | None, str]]:
        sections: list[tuple[str | None, str]] = []
        current_title: str | None = None
        current_lines: list[str] = []
        for line in raw_text.splitlines():
            heading_match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
            if heading_match:
                if current_lines:
                    sections.append((current_title, "\n".join(current_lines).strip()))
                    current_lines = []
                current_title = normalize_chinese_text(heading_match.group(1).strip())
                continue
            current_lines.append(line)
        if current_lines:
            sections.append((current_title, "\n".join(current_lines).strip()))
        return [section for section in sections if section[1]]

    @staticmethod
    def _resolve_topic_metadata(
        title_zh: str,
        content: str,
        source_id: str,
        section_index: int,
    ) -> tuple[str, str, str, list[str], list[str]]:
        term_matches = extract_term_matches(f"{title_zh}\n{content}")
        if term_matches:
            primary_match = KnowledgeIngestor._select_primary_term_match(term_matches)
            return (
                f"term:{primary_match.dev_id}",
                "term",
                primary_match.dev_id,
                [primary_match.canonical_zh],
                [primary_match.dev_id],
            )
        fallback_title = normalize_chinese_text(title_zh).replace(" ", "_")
        return (
            f"source:{source_id}:section:{section_index}:{fallback_title}",
            "text",
            "section",
            [],
            [],
        )

    @staticmethod
    def _select_primary_term_match(term_matches):
        prioritized_meta_terms = {"house_rule"}
        for match in term_matches:
            if match.dev_id not in prioritized_meta_terms:
                return match
        return term_matches[0]

    def _chunk_pdf_pages(
        self,
        source: KnowledgeSourceState,
        parsed_source: ParsedSourceDocument,
    ) -> list[RuleChunk]:
        chunks: list[RuleChunk] = []
        for page in parsed_source.pages:
            clean_content = page.text.strip()
            if not clean_content:
                continue
            title_zh = page.heading or source.source_title_zh or source.document_identity
            topic_key, taxonomy_category, taxonomy_subcategory, tags, machine_flags = (
                self._resolve_topic_metadata(title_zh, clean_content, source.source_id, page.page_number)
            )
            chunks.append(
                RuleChunk(
                    chunk_id=f"{source.source_id}-page-{page.page_number}",
                    source_id=source.source_id,
                    ruleset=source.ruleset,
                    topic_key=topic_key,
                    taxonomy_category=taxonomy_category,
                    taxonomy_subcategory=taxonomy_subcategory,
                    document_identity=source.document_identity,
                    source_title_zh=source.source_title_zh,
                    chapter=page.heading,
                    page_reference=page.page_number,
                    content=clean_content,
                    tags=tags,
                    priority=source.default_priority,
                    is_authoritative=source.is_authoritative,
                    title_zh=title_zh,
                    short_citation=self._build_short_citation(
                        source,
                        chapter=page.heading,
                        page_reference=page.page_number,
                    ),
                    visibility=source.default_visibility,
                    allowed_player_ids=list(source.allowed_player_ids),
                    machine_flags=[*machine_flags, "pdf_text_shallow"],
                )
            )
        return chunks

    @staticmethod
    def _build_short_citation(
        source: KnowledgeSourceState,
        *,
        chapter: str | None = None,
        section_index: int | None = None,
        page_reference: int | None = None,
    ) -> str:
        source_label = source.source_title_zh or source.document_identity
        if page_reference is not None:
            citation = f"《{source_label}》第{page_reference}页"
            if chapter:
                return f"{citation}·{chapter}"
            return citation
        if chapter:
            return f"《{source_label}》·{chapter}"
        if section_index is not None:
            return f"《{source_label}》片段{section_index}"
        return f"《{source_label}》"

    @staticmethod
    def _require_source_path(source: KnowledgeSourceState) -> Path:
        if not source.source_path:
            raise ValueError(f"knowledge source {source.source_id} does not have a source_path")
        return Path(source.source_path)

    def _extract_pdf_pages(self, raw_bytes: bytes) -> list[ParsedSourcePage]:
        pdf_objects = self._extract_pdf_objects(raw_bytes)
        page_objects = self._extract_pdf_page_objects(pdf_objects)
        if page_objects:
            pages: list[ParsedSourcePage] = []
            for page_number, (page_object_number, page_object_body) in enumerate(page_objects, start=1):
                page_text = self._extract_pdf_page_text(
                    page_object_number,
                    page_object_body,
                    pdf_objects,
                )
                normalized_text = normalize_chinese_text(page_text)
                pages.append(
                    ParsedSourcePage(
                        page_number=page_number,
                        text=normalized_text,
                        heading=self._extract_page_heading(normalized_text),
                    )
                )
            return pages

        text_streams = self._extract_pdf_text_streams(raw_bytes)
        if not text_streams:
            return [ParsedSourcePage(page_number=1, text="")]
        return [
            ParsedSourcePage(
                page_number=index,
                text=normalize_chinese_text(text),
                heading=self._extract_page_heading(normalize_chinese_text(text)),
            )
            for index, text in enumerate(text_streams, start=1)
        ]

    @staticmethod
    def _extract_pdf_objects(raw_bytes: bytes) -> dict[int, bytes]:
        return {
            int(match.group(1)): match.group(2)
            for match in re.finditer(
                rb"(\d+)\s+\d+\s+obj(.*?)endobj",
                raw_bytes,
                re.DOTALL,
            )
        }

    @staticmethod
    def _extract_pdf_page_objects(pdf_objects: dict[int, bytes]) -> list[tuple[int, bytes]]:
        ordered_page_numbers: list[int] = []
        for object_body in pdf_objects.values():
            header_text = object_body.decode("latin1", errors="ignore")
            if re.search(r"/Type\s*/Pages\b", header_text):
                ordered_page_numbers.extend(
                    int(match)
                    for match in re.findall(r"(\d+)\s+\d+\s+R", header_text)
                    if int(match) in pdf_objects
                )
                break

        if ordered_page_numbers:
            return [
                (object_number, pdf_objects[object_number])
                for object_number in ordered_page_numbers
                if re.search(
                    r"/Type\s*/Page\b",
                    pdf_objects[object_number].decode("latin1", errors="ignore"),
                )
            ]

        page_objects: list[tuple[int, bytes]] = []
        for object_number, object_body in pdf_objects.items():
            header_text = object_body.decode("latin1", errors="ignore")
            if re.search(r"/Type\s*/Page\b", header_text):
                page_objects.append((object_number, object_body))
        page_objects.sort(key=lambda item: item[0])
        return page_objects

    def _extract_pdf_page_text(
        self,
        page_object_number: int,
        page_object_body: bytes,
        pdf_objects: dict[int, bytes],
    ) -> str:
        page_header = page_object_body.decode("latin1", errors="ignore")
        content_refs = self._extract_pdf_content_refs(page_header)
        fragments: list[str] = []
        for content_ref in content_refs:
            content_object = pdf_objects.get(content_ref)
            if content_object is None:
                continue
            stream_bytes = self._extract_pdf_stream_bytes(content_object)
            if stream_bytes is None:
                continue
            content_header = content_object.decode("latin1", errors="ignore")
            fragments.append(
                self._extract_text_from_pdf_stream(
                    stream_bytes,
                    object_header=content_header,
                )
            )
        if fragments:
            return "\n".join(fragment for fragment in fragments if fragment.strip())

        stream_bytes = self._extract_pdf_stream_bytes(page_object_body)
        if stream_bytes is None:
            return ""
        return self._extract_text_from_pdf_stream(
            stream_bytes,
            object_header=page_header,
        )

    @staticmethod
    def _extract_pdf_content_refs(page_header: str) -> list[int]:
        array_match = re.search(r"/Contents\s*\[(.*?)\]", page_header, re.DOTALL)
        if array_match:
            return [
                int(match)
                for match in re.findall(r"(\d+)\s+\d+\s+R", array_match.group(1))
            ]
        single_match = re.search(r"/Contents\s+(\d+)\s+\d+\s+R", page_header)
        if single_match:
            return [int(single_match.group(1))]
        return []

    @staticmethod
    def _extract_pdf_stream_bytes(object_body: bytes) -> bytes | None:
        stream_match = re.search(
            rb"stream\r?\n(.*?)\r?\nendstream",
            object_body,
            re.DOTALL,
        )
        if stream_match is None:
            return None
        return stream_match.group(1)

    def _extract_text_from_pdf_stream(
        self,
        stream_bytes: bytes,
        *,
        object_header: str,
    ) -> str:
        decoded_stream = self._decode_pdf_stream(
            stream_bytes,
            object_header=object_header,
        )
        page_text = self._extract_pdf_literal_text(decoded_stream)
        if page_text:
            return page_text
        return decoded_stream.strip()

    def _extract_pdf_text_streams(self, raw_bytes: bytes) -> list[str]:
        # TODO: This is a shallow PDF extractor for simple text streams only.
        # It does not attempt full layout recovery, font decoding, or robust object parsing.
        raw_text = raw_bytes.decode("utf-8", errors="ignore")
        object_filters = {
            int(match.group(1)): match.group(2)
            for match in re.finditer(
                r"(\d+)\s+\d+\s+obj(.*?)(?:stream\b)",
                raw_text,
                re.DOTALL,
            )
        }
        streams = list(re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw_bytes, re.DOTALL))
        extracted_pages: list[str] = []
        for stream_match in streams:
            stream_bytes = stream_match.group(1)
            object_number = self._extract_pdf_object_number(raw_text[: stream_match.start()])
            decoded_stream = self._decode_pdf_stream(
                stream_bytes,
                object_header=object_filters.get(object_number, ""),
            )
            page_text = self._extract_pdf_literal_text(decoded_stream)
            if page_text:
                extracted_pages.append(page_text)
            elif decoded_stream.strip():
                extracted_pages.append(decoded_stream.strip())
            else:
                extracted_pages.append("")
        return extracted_pages

    @staticmethod
    def _extract_page_heading(page_text: str) -> str | None:
        for line in page_text.splitlines()[:4]:
            stripped = line.strip().lstrip("#").strip()
            if not stripped:
                continue
            if re.match(r"^第.+[章节幕节]$", stripped):
                return stripped
            if (
                len(stripped) <= 24
                and not any(marker in stripped for marker in ("。", "！", "？", "；", ".", ":", "："))
            ):
                return stripped
        return None

    @staticmethod
    def _extract_pdf_object_number(header_text: str) -> int | None:
        matches = re.findall(r"(\d+)\s+\d+\s+obj", header_text)
        if not matches:
            return None
        return int(matches[-1])

    @staticmethod
    def _decode_pdf_stream(stream_bytes: bytes, *, object_header: str) -> str:
        decoded_bytes = stream_bytes
        if "FlateDecode" in object_header:
            try:
                decoded_bytes = zlib.decompress(stream_bytes)
            except zlib.error:
                decoded_bytes = stream_bytes
        return decoded_bytes.decode("utf-8", errors="ignore")

    @staticmethod
    def _extract_pdf_literal_text(stream_text: str) -> str:
        fragments: list[str] = []
        for literal in re.findall(r"\((.*?)\)\s*Tj", stream_text, re.DOTALL):
            fragments.append(KnowledgeIngestor._decode_pdf_literal(literal))
        for array_match in re.findall(r"\[(.*?)\]\s*TJ", stream_text, re.DOTALL):
            pieces = [
                KnowledgeIngestor._decode_pdf_literal(piece)
                for piece in re.findall(r"\((.*?)\)", array_match, re.DOTALL)
            ]
            if pieces:
                fragments.append("".join(pieces))
        return "\n".join(fragment.strip() for fragment in fragments if fragment.strip())

    @staticmethod
    def _decode_pdf_literal(literal: str) -> str:
        return (
            literal.replace(r"\(", "(")
            .replace(r"\)", ")")
            .replace(r"\n", "\n")
            .replace(r"\r", "")
            .replace(r"\t", "\t")
            .replace(r"\\", "\\")
        )

    @staticmethod
    def _parse_character_sheet_csv(source_path: Path) -> CharacterSheetExtraction:
        with source_path.open("r", encoding="utf-8", newline="") as csv_file:
            rows = list(csv.DictReader(csv_file))
        return KnowledgeIngestor._build_character_sheet_extraction(rows)

    @staticmethod
    def _parse_character_sheet_xlsx(source: KnowledgeSourceState) -> CharacterSheetExtraction:
        source_path = Path(source.source_path or "")
        with zipfile.ZipFile(source_path) as workbook:
            shared_strings = KnowledgeIngestor._read_xlsx_shared_strings(workbook)
            sheet_paths = KnowledgeIngestor._read_xlsx_sheet_paths(workbook)
            template_profile = KnowledgeIngestor._detect_integrated_character_workbook_profile(
                workbook,
                sheet_paths=sheet_paths,
                shared_strings=shared_strings,
            )
            if template_profile is not None:
                return KnowledgeIngestor._parse_integrated_character_workbook(
                    source,
                    workbook,
                    sheet_paths=sheet_paths,
                    shared_strings=shared_strings,
                    template_profile=template_profile,
                )
            worksheet_path = KnowledgeIngestor._resolve_first_xlsx_worksheet_path(workbook)
            rows = KnowledgeIngestor._read_xlsx_rows(
                workbook,
                worksheet_path=worksheet_path,
                shared_strings=shared_strings,
            )
        return KnowledgeIngestor._build_character_sheet_extraction(rows)

    @staticmethod
    def _read_xlsx_sheet_paths(workbook: zipfile.ZipFile) -> dict[str, str]:
        workbook_path = "xl/workbook.xml"
        relationship_path = "xl/_rels/workbook.xml.rels"
        namespace = {
            "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
            "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
        }
        workbook_root = ET.fromstring(workbook.read(workbook_path))
        relationships_root = ET.fromstring(workbook.read(relationship_path))
        relationship_map = {
            relationship.attrib.get("Id"): relationship.attrib.get("Target", "")
            for relationship in relationships_root.findall("rel:Relationship", namespace)
        }
        sheet_paths: dict[str, str] = {}
        for sheet in workbook_root.findall("main:sheets/main:sheet", namespace):
            sheet_name = sheet.attrib.get("name")
            relationship_id = sheet.attrib.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
            if not sheet_name or not relationship_id:
                continue
            target = relationship_map.get(relationship_id, "").lstrip("/")
            if not target:
                continue
            sheet_paths[sheet_name] = f"xl/{target}" if not target.startswith("xl/") else target
        return sheet_paths

    @staticmethod
    def _detect_integrated_character_workbook_profile(
        workbook: zipfile.ZipFile,
        *,
        sheet_paths: dict[str, str],
        shared_strings: list[str],
    ) -> str | None:
        if KnowledgeIngestor.INTEGRATED_CHARACTER_MAIN_SHEET not in sheet_paths:
            return None
        if not any(
            summary_sheet in sheet_paths
            for summary_sheet in KnowledgeIngestor.INTEGRATED_CHARACTER_SUMMARY_SHEETS
        ):
            return None
        helper_sheet_count = sum(
            helper_sheet in sheet_paths
            for helper_sheet in KnowledgeIngestor.INTEGRATED_CHARACTER_HELPER_SHEETS
        )
        if helper_sheet_count < 2:
            return None

        main_sheet = KnowledgeIngestor._read_xlsx_sheet_snapshot(
            workbook,
            sheet_name=KnowledgeIngestor.INTEGRATED_CHARACTER_MAIN_SHEET,
            worksheet_path=sheet_paths[KnowledgeIngestor.INTEGRATED_CHARACTER_MAIN_SHEET],
            shared_strings=shared_strings,
        )
        for cell_ref, expected_label in KnowledgeIngestor.INTEGRATED_CHARACTER_MAIN_ANCHORS.items():
            actual_value = KnowledgeIngestor._sheet_value(main_sheet, cell_ref)
            expected_labels = (
                expected_label
                if isinstance(expected_label, tuple)
                else (expected_label,)
            )
            if not any(label in actual_value for label in expected_labels):
                return None
        return KnowledgeIngestor.INTEGRATED_CHARACTER_WORKBOOK_PROFILE

    @staticmethod
    def _parse_integrated_character_workbook(
        source: KnowledgeSourceState,
        workbook: zipfile.ZipFile,
        *,
        sheet_paths: dict[str, str],
        shared_strings: list[str],
        template_profile: str,
    ) -> CharacterSheetExtraction:
        main_sheet = KnowledgeIngestor._read_xlsx_sheet_snapshot(
            workbook,
            sheet_name=KnowledgeIngestor.INTEGRATED_CHARACTER_MAIN_SHEET,
            worksheet_path=sheet_paths[KnowledgeIngestor.INTEGRATED_CHARACTER_MAIN_SHEET],
            shared_strings=shared_strings,
        )
        summary_sheet_name = next(
            (
                sheet_name
                for sheet_name in KnowledgeIngestor.INTEGRATED_CHARACTER_SUMMARY_SHEETS
                if sheet_name in sheet_paths
            ),
            None,
        )
        summary_sheet = (
            KnowledgeIngestor._read_xlsx_sheet_snapshot(
                workbook,
                sheet_name=summary_sheet_name,
                worksheet_path=sheet_paths[summary_sheet_name],
                shared_strings=shared_strings,
            )
            if summary_sheet_name is not None
            else None
        )
        occupation_list_sheet = (
            KnowledgeIngestor._read_xlsx_sheet_snapshot(
                workbook,
                sheet_name="职业列表",
                worksheet_path=sheet_paths["职业列表"],
                shared_strings=shared_strings,
            )
            if "职业列表" in sheet_paths
            else None
        )
        occupation_skills_sheet = (
            KnowledgeIngestor._read_xlsx_sheet_snapshot(
                workbook,
                sheet_name="本职技能",
                worksheet_path=sheet_paths["本职技能"],
                shared_strings=shared_strings,
            )
            if "本职技能" in sheet_paths
            else None
        )

        investigator_name = KnowledgeIngestor._required_sheet_text(
            main_sheet,
            "E3",
            "investigator_name",
        )
        core_stats = {
            "strength": KnowledgeIngestor._required_sheet_int(main_sheet, "U3", "strength"),
            "dexterity": KnowledgeIngestor._required_sheet_int(main_sheet, "AA3", "dexterity"),
            "power": KnowledgeIngestor._required_sheet_int(main_sheet, "AG3", "power"),
            "constitution": KnowledgeIngestor._required_sheet_int(main_sheet, "U5", "constitution"),
            "appearance": KnowledgeIngestor._required_sheet_int(main_sheet, "AA5", "appearance"),
            "education": KnowledgeIngestor._required_sheet_int(main_sheet, "AG5", "education"),
            "size": KnowledgeIngestor._required_sheet_int(main_sheet, "U7", "size"),
            "intelligence": KnowledgeIngestor._required_sheet_int(main_sheet, "AA7", "intelligence"),
        }
        derived_stats = {
            "hp": KnowledgeIngestor._optional_sheet_scalar(main_sheet, "E10"),
            "hp_max": KnowledgeIngestor._optional_sheet_scalar(main_sheet, "G10"),
            "san": KnowledgeIngestor._optional_sheet_scalar(main_sheet, "N10"),
            "san_max": KnowledgeIngestor._optional_sheet_scalar(main_sheet, "P10"),
            "mp": KnowledgeIngestor._optional_sheet_scalar(main_sheet, "W10"),
            "mp_max": KnowledgeIngestor._optional_sheet_scalar(main_sheet, "Y10"),
            "mov": KnowledgeIngestor._optional_sheet_scalar(main_sheet, "AF10"),
            "armor": KnowledgeIngestor._optional_sheet_scalar(main_sheet, "AN10"),
            "major_wound_threshold": KnowledgeIngestor._optional_sheet_scalar(main_sheet, "D12"),
        }
        normalized_derived_stats = {
            key: value for key, value in derived_stats.items() if value is not None
        }
        source_path = Path(source.source_path or "")
        skills, ambiguous_skill_fields, skill_provenance = (
            KnowledgeIngestor._extract_integrated_workbook_skills(
                main_sheet,
                source_workbook=source_path.name,
            )
        )
        note_fields = KnowledgeIngestor._extract_integrated_workbook_notes(
            main_sheet=main_sheet,
            summary_sheet=summary_sheet,
        )
        starting_inventory = KnowledgeIngestor._extract_integrated_workbook_starting_inventory(
            summary_sheet
        )
        occupation_name = KnowledgeIngestor._sheet_optional_text(main_sheet, "E5")
        occupation_sequence_id = KnowledgeIngestor._sheet_optional_text(main_sheet, "M5")
        (
            occupation_source_metadata,
            occupation_ambiguities,
            occupation_confidence_penalty,
        ) = KnowledgeIngestor._extract_integrated_workbook_occupation_checks(
            occupation_list_sheet=occupation_list_sheet,
            occupation_skills_sheet=occupation_skills_sheet,
            occupation_name=occupation_name,
            occupation_sequence_id=occupation_sequence_id,
            extracted_skills=skills,
        )

        ambiguous_fields = list(ambiguous_skill_fields)
        ambiguous_fields.extend(occupation_ambiguities)
        ambiguous_fields.extend(
            KnowledgeIngestor._collect_formula_without_cached_ambiguities(
                main_sheet,
                "investigator_name",
                ["E3"],
            )
        )
        ambiguous_fields.extend(
            KnowledgeIngestor._collect_formula_without_cached_ambiguities(
                main_sheet,
                "occupation",
                ["E5", "M5"],
            )
        )
        ambiguous_fields.extend(
            KnowledgeIngestor._collect_formula_without_cached_ambiguities(
                main_sheet,
                "identity",
                ["E4", "M4", "E6", "M6", "E7", "M7"],
            )
        )
        ambiguous_fields.extend(
            KnowledgeIngestor._collect_formula_without_cached_ambiguities(
                main_sheet,
                "derived_stats",
                ["E10", "G10", "N10", "P10", "W10", "Y10", "AF10", "AN10", "D12"],
            )
        )
        if summary_sheet is not None:
            ambiguous_fields.extend(
                KnowledgeIngestor._collect_formula_without_cached_ambiguities(
                    summary_sheet,
                    "notes",
                    ["C28", "H28", "C29", "C30", "C31", "C32", "C33", "C34", "C35", "C36"],
                )
            )
        if summary_sheet is None:
            ambiguous_fields.append("summary_sheet:未检测到简化卡摘要页")
        confidence = 0.9
        if summary_sheet is not None:
            confidence += 0.04
        if note_fields["campaign_notes"] is not None:
            confidence += 0.02
        if note_fields["secrets"] is not None:
            confidence += 0.02
        if ambiguous_fields:
            confidence -= min(0.06, 0.01 * len(ambiguous_fields))
        confidence -= occupation_confidence_penalty

        field_provenance = {
            "investigator_name": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="E3",
            ),
            "player_name": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="E4",
            ),
            "occupation": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="E5",
            ),
            "occupation_sequence_id": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="M5",
            ),
            "era": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="M4",
            ),
            "age": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="E6",
            ),
            "sex": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="M6",
            ),
            "residence": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="E7",
            ),
            "hometown": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="M7",
            ),
            "core_stats.strength": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="U3",
            ),
            "core_stats.constitution": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="U5",
            ),
            "core_stats.size": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="U7",
            ),
            "core_stats.dexterity": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="AA3",
            ),
            "core_stats.appearance": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="AA5",
            ),
            "core_stats.intelligence": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="AA7",
            ),
            "core_stats.power": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="AG3",
            ),
            "core_stats.education": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="AG5",
            ),
            "derived_stats.hp": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="E10",
            ),
            "derived_stats.san": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="N10",
            ),
            "derived_stats.mp": ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="W10",
            ),
        }
        field_provenance.update(skill_provenance)
        if summary_sheet is not None and note_fields["background_traits"] is not None:
            field_provenance["background_traits"] = ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=summary_sheet.name,
                source_anchor="C28:C36",
            )
        elif note_fields["background_traits"] is not None:
            field_provenance["background_traits"] = ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=main_sheet.name,
                source_anchor="AA61:AA73",
            )
        if summary_sheet is not None and note_fields["campaign_notes"] is not None:
            field_provenance["campaign_notes"] = ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=summary_sheet.name,
                source_anchor="H28",
            )
        if summary_sheet is not None and note_fields["secrets"] is not None:
            field_provenance["secrets"] = ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=summary_sheet.name,
                source_anchor="C34",
            )
        if summary_sheet is not None and starting_inventory:
            field_provenance["starting_inventory"] = ImportFieldProvenance(
                source_workbook=source_path.name,
                source_sheet=summary_sheet.name,
                source_anchor="B22:K25",
            )

        return CharacterSheetExtraction(
            character_id=source.source_id,
            investigator_name=investigator_name,
            player_name=KnowledgeIngestor._sheet_optional_text(main_sheet, "E4"),
            occupation=KnowledgeIngestor._sheet_optional_text(main_sheet, "E5"),
            occupation_sequence_id=occupation_sequence_id,
            era=KnowledgeIngestor._sheet_optional_text(main_sheet, "M4") or "1920s",
            age=KnowledgeIngestor._optional_sheet_int(main_sheet, "E6"),
            sex=KnowledgeIngestor._sheet_optional_text(main_sheet, "M6"),
            residence=KnowledgeIngestor._sheet_optional_text(main_sheet, "E7"),
            hometown=KnowledgeIngestor._sheet_optional_text(main_sheet, "M7"),
            core_stats=core_stats,
            derived_stats=normalized_derived_stats,
            skills=skills,
            starting_inventory=starting_inventory,
            background_traits=note_fields["background_traits"],
            secrets=note_fields["secrets"],
            campaign_notes=note_fields["campaign_notes"],
            template_profile=template_profile,
            source_metadata={
                "xlsx_import_mode": "integrated_template",
                "main_sheet_name": main_sheet.name,
                "summary_sheet_name": summary_sheet_name or "",
                "cached_values_preferred": "true",
                "merged_range_count": str(len(main_sheet.merged_ranges)),
                **occupation_source_metadata,
            },
            field_provenance=field_provenance,
            extraction_confidence=round(max(0.0, min(1.0, confidence)), 2),
            ambiguous_fields=list(dict.fromkeys(ambiguous_fields)),
        )

    @staticmethod
    def _extract_integrated_workbook_occupation_checks(
        *,
        occupation_list_sheet: XlsxSheetSnapshot | None,
        occupation_skills_sheet: XlsxSheetSnapshot | None,
        occupation_name: str | None,
        occupation_sequence_id: str | None,
        extracted_skills: dict[str, int],
    ) -> tuple[dict[str, str], list[str], float]:
        source_metadata: dict[str, str] = {}
        ambiguities: list[str] = []
        confidence_penalty = 0.0
        helper_name: str | None = None
        helper_sequence_id: str | None = None

        if occupation_list_sheet is not None:
            helper_sequence_id, helper_name = (
                KnowledgeIngestor._find_integrated_occupation_reference(
                    occupation_list_sheet,
                    occupation_name=occupation_name,
                    occupation_sequence_id=occupation_sequence_id,
                )
            )
        if helper_sequence_id is not None:
            source_metadata["occupation_helper_sequence_id"] = helper_sequence_id
        if helper_name is not None:
            source_metadata["occupation_helper_name"] = helper_name

        if occupation_sequence_id and helper_sequence_id is None:
            ambiguities.append(f"occupation_helper_sequence_not_found:{occupation_sequence_id}")
            confidence_penalty += 0.01
        elif occupation_name and helper_name is None:
            ambiguities.append(f"occupation_helper_name_not_found:{occupation_name}")
            confidence_penalty += 0.01

        if (
            occupation_name
            and helper_name is not None
            and occupation_name != helper_name
        ):
            ambiguities.append(
                f"occupation_helper_mismatch:{helper_sequence_id or occupation_sequence_id or ''}:{helper_name}:{occupation_name}"
            )
            confidence_penalty += 0.01

        skill_column = None
        if occupation_skills_sheet is not None:
            skill_column = KnowledgeIngestor._find_integrated_occupation_skill_column(
                occupation_skills_sheet,
                occupation_sequence_id=helper_sequence_id or occupation_sequence_id,
                occupation_name=helper_name or occupation_name,
            )
        if skill_column is not None and occupation_skills_sheet is not None:
            expected_skills = KnowledgeIngestor._extract_integrated_occupation_skill_expectations(
                occupation_skills_sheet,
                occupation_column=skill_column,
            )
            if expected_skills:
                source_metadata["occupation_helper_skill_expectations"] = "、".join(
                    expected_skills[:8]
                )
                normalized_extracted_skills = {
                    normalize_chinese_text(skill_name).replace(" ", "")
                    for skill_name in extracted_skills
                }
                missing_skills = [
                    skill_name
                    for skill_name in expected_skills
                    if normalize_chinese_text(skill_name).replace(" ", "")
                    not in normalized_extracted_skills
                ]
                if missing_skills:
                    ambiguities.append(
                        "occupation_skill_expectation_missing:" + ":".join(missing_skills[:4])
                    )
                    confidence_penalty += min(0.02, 0.005 * len(missing_skills[:4]))
        return source_metadata, ambiguities, confidence_penalty

    @staticmethod
    def _find_integrated_occupation_reference(
        occupation_list_sheet: XlsxSheetSnapshot,
        *,
        occupation_name: str | None,
        occupation_sequence_id: str | None,
    ) -> tuple[str | None, str | None]:
        fallback_match: tuple[str | None, str | None] = (None, None)
        max_row = max(
            (KnowledgeIngestor._cell_row(reference) for reference in occupation_list_sheet.cells),
            default=0,
        )
        for row_index in range(2, max_row + 1):
            sequence_id = KnowledgeIngestor._sheet_optional_text(
                occupation_list_sheet,
                f"A{row_index}",
            )
            helper_name = KnowledgeIngestor._sheet_optional_text(
                occupation_list_sheet,
                f"B{row_index}",
            )
            if helper_name is None:
                continue
            if occupation_sequence_id and sequence_id == occupation_sequence_id:
                return sequence_id, helper_name
            if occupation_name and helper_name == occupation_name:
                fallback_match = (sequence_id, helper_name)
        return fallback_match

    @staticmethod
    def _find_integrated_occupation_skill_column(
        occupation_skills_sheet: XlsxSheetSnapshot,
        *,
        occupation_sequence_id: str | None,
        occupation_name: str | None,
    ) -> str | None:
        fallback_column: str | None = None
        for cell_reference, cell in occupation_skills_sheet.cells.items():
            row_index = KnowledgeIngestor._cell_row(cell_reference)
            column_name = KnowledgeIngestor._cell_column_name(cell_reference)
            value = cell.value.strip()
            if not value:
                continue
            if row_index == 1 and occupation_sequence_id and value == occupation_sequence_id:
                return column_name
            if row_index == 2 and occupation_name and value == occupation_name:
                fallback_column = column_name
        return fallback_column

    @staticmethod
    def _extract_integrated_occupation_skill_expectations(
        occupation_skills_sheet: XlsxSheetSnapshot,
        *,
        occupation_column: str,
    ) -> list[str]:
        expectations: list[str] = []
        max_row = max(
            (KnowledgeIngestor._cell_row(reference) for reference in occupation_skills_sheet.cells),
            default=0,
        )
        for row_index in range(3, max_row + 1):
            row_label = KnowledgeIngestor._sheet_optional_text(
                occupation_skills_sheet,
                f"A{row_index}",
            )
            raw_marker = KnowledgeIngestor._sheet_optional_text(
                occupation_skills_sheet,
                f"{occupation_column}{row_index}",
            )
            if row_label is None or raw_marker is None:
                continue
            expectation = KnowledgeIngestor._normalize_integrated_expected_skill(
                row_label=row_label,
                raw_marker=raw_marker,
            )
            if expectation and expectation not in expectations:
                expectations.append(expectation)
        return expectations

    @staticmethod
    def _normalize_integrated_expected_skill(
        *,
        row_label: str,
        raw_marker: str,
    ) -> str | None:
        control_labels = {"☆", "⊙", "☯", "※", "任意特长"}
        symbolic_markers = {"★", "☆", "☯", "⊙", "※"}
        normalized_label = normalize_chinese_text(
            row_label.replace("：", "").replace(" Ω", "").replace("　", " ").strip()
        )
        normalized_marker = raw_marker.replace("　", " ").strip()
        if normalized_label in control_labels:
            return None
        if normalized_label.startswith("技艺") and normalized_marker in symbolic_markers:
            return None
        if normalized_label.startswith(("格斗", "射击")) and normalized_marker in symbolic_markers:
            return None
        if normalized_marker.lstrip("-").isdigit():
            return None
        cleaned_marker = normalized_marker.strip("★☆☯⊙※ ").strip()
        if normalized_label.startswith("技艺") and cleaned_marker:
            return normalize_chinese_text(cleaned_marker)
        if normalized_label.startswith("射击") and cleaned_marker:
            return normalize_chinese_text(f"射击（{cleaned_marker}）")
        if normalized_label.startswith("格斗") and cleaned_marker:
            return normalize_chinese_text(cleaned_marker)
        if normalized_label.endswith(("①", "②", "③", "④", "⑤")):
            return None
        return normalized_label

    @staticmethod
    def _read_xlsx_sheet_snapshot(
        workbook: zipfile.ZipFile,
        *,
        sheet_name: str,
        worksheet_path: str,
        shared_strings: list[str],
    ) -> XlsxSheetSnapshot:
        namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        worksheet_root = ET.fromstring(workbook.read(worksheet_path))
        cells: dict[str, XlsxCellSnapshot] = {}
        for row in worksheet_root.findall(".//main:sheetData/main:row", namespace):
            for cell in row.findall("main:c", namespace):
                cell_reference = cell.attrib.get("r", "")
                if not cell_reference:
                    continue
                cells[cell_reference] = KnowledgeIngestor._read_xlsx_cell_snapshot(
                    cell,
                    namespace=namespace,
                    shared_strings=shared_strings,
                )
        merged_ranges = [
            merge_cell.attrib.get("ref", "")
            for merge_cell in worksheet_root.findall(".//main:mergeCells/main:mergeCell", namespace)
            if merge_cell.attrib.get("ref")
        ]
        return XlsxSheetSnapshot(name=sheet_name, cells=cells, merged_ranges=merged_ranges)

    @staticmethod
    def _read_xlsx_cell_snapshot(
        cell: ET.Element,
        *,
        namespace: dict[str, str],
        shared_strings: list[str],
    ) -> XlsxCellSnapshot:
        cell_type = cell.attrib.get("t")
        formula_node = cell.find("main:f", namespace)
        formula = formula_node.text if formula_node is not None and formula_node.text else None
        if cell_type == "s":
            value_node = cell.find("main:v", namespace)
            if value_node is None or value_node.text is None:
                return XlsxCellSnapshot(value="", formula=formula)
            shared_index = int(value_node.text)
            if 0 <= shared_index < len(shared_strings):
                return XlsxCellSnapshot(value=shared_strings[shared_index], formula=formula)
            return XlsxCellSnapshot(value="", formula=formula)
        if cell_type == "inlineStr":
            text_parts = [text_node.text or "" for text_node in cell.findall(".//main:t", namespace)]
            return XlsxCellSnapshot(value="".join(text_parts), formula=formula)
        value_node = cell.find("main:v", namespace)
        value = value_node.text if value_node is not None and value_node.text is not None else ""
        return XlsxCellSnapshot(value=value, formula=formula)

    @staticmethod
    def _sheet_value(sheet: XlsxSheetSnapshot, cell_reference: str) -> str:
        cell = sheet.cells.get(cell_reference)
        if cell is None:
            return ""
        return cell.value.strip()

    @staticmethod
    def _sheet_optional_text(sheet: XlsxSheetSnapshot, cell_reference: str) -> str | None:
        value = KnowledgeIngestor._sheet_value(sheet, cell_reference)
        if not value or value in {"0", "#N/A"}:
            return None
        return value

    @staticmethod
    def _required_sheet_text(
        sheet: XlsxSheetSnapshot,
        cell_reference: str,
        field_name: str,
    ) -> str:
        value = KnowledgeIngestor._sheet_optional_text(sheet, cell_reference)
        if value is None:
            raise ValueError(
                f"integrated workbook field {field_name} is missing at {sheet.name}!{cell_reference}"
            )
        return value

    @staticmethod
    def _required_sheet_int(
        sheet: XlsxSheetSnapshot,
        cell_reference: str,
        field_name: str,
    ) -> int:
        value = KnowledgeIngestor._sheet_value(sheet, cell_reference)
        if not value.lstrip("-").isdigit():
            raise ValueError(
                f"integrated workbook field {field_name} must be an integer at {sheet.name}!{cell_reference}"
            )
        return int(value)

    @staticmethod
    def _optional_sheet_int(sheet: XlsxSheetSnapshot, cell_reference: str) -> int | None:
        value = KnowledgeIngestor._sheet_value(sheet, cell_reference)
        if not value or value in {"#N/A"}:
            return None
        if value.lstrip("-").isdigit():
            return int(value)
        return None

    @staticmethod
    def _optional_sheet_scalar(sheet: XlsxSheetSnapshot, cell_reference: str) -> int | str | None:
        value = KnowledgeIngestor._sheet_value(sheet, cell_reference)
        if not value or value in {"#N/A"}:
            return None
        if value.lstrip("-").isdigit():
            return int(value)
        return value

    @staticmethod
    def _extract_integrated_workbook_skills(
        main_sheet: XlsxSheetSnapshot,
        *,
        source_workbook: str,
    ) -> tuple[dict[str, int], list[str], dict[str, ImportFieldProvenance]]:
        skills: dict[str, int] = {}
        ambiguous_fields: list[str] = []
        field_provenance: dict[str, ImportFieldProvenance] = {}
        for row_index in range(16, 60):
            for base_ref, subskill_ref, score_ref in (
                (f"F{row_index}", f"H{row_index}", f"R{row_index}"),
                (f"AB{row_index}", f"AD{row_index}", f"AN{row_index}"),
            ):
                skill_name = KnowledgeIngestor._normalize_integrated_skill_name(
                    base_name=KnowledgeIngestor._sheet_value(main_sheet, base_ref),
                    subskill_name=KnowledgeIngestor._sheet_value(main_sheet, subskill_ref),
                )
                if skill_name is None:
                    base_name = KnowledgeIngestor._sheet_value(main_sheet, base_ref)
                    if KnowledgeIngestor._is_integrated_skill_placeholder(base_name):
                        ambiguous_fields.append(f"skill_placeholder:{base_name}")
                    continue
                score_value = KnowledgeIngestor._sheet_value(main_sheet, score_ref)
                if not score_value.lstrip("-").isdigit():
                    continue
                skills[skill_name] = int(score_value)
                field_provenance[f"skills.{skill_name}"] = ImportFieldProvenance(
                    source_workbook=source_workbook,
                    source_sheet=main_sheet.name,
                    source_anchor=f"{base_ref}/{score_ref}",
                )
        return skills, list(dict.fromkeys(ambiguous_fields)), field_provenance

    @staticmethod
    def _normalize_integrated_skill_name(
        *,
        base_name: str,
        subskill_name: str,
    ) -> str | None:
        normalized_base = base_name.replace("\n", "").strip()
        normalized_subskill = subskill_name.replace("\n", "").strip()
        if not normalized_base or normalized_base == "技能名称":
            return None
        if normalized_subskill and normalized_subskill not in {"0", "#N/A"}:
            return normalized_subskill
        if KnowledgeIngestor._is_integrated_skill_placeholder(normalized_base):
            return None
        if normalized_base.endswith(("：", ":")):
            return None
        return normalized_base

    @staticmethod
    def _is_integrated_skill_placeholder(base_name: str) -> bool:
        normalized_base = base_name.replace("\n", "").strip()
        if not normalized_base:
            return False
        if any(marker in normalized_base for marker in ("①", "②", "③", "④", "⑤")):
            return True
        if normalized_base.startswith("技艺") and any(
            marker in normalized_base for marker in ("//", "／／", "____", "待填")
        ):
            return True
        return False

    @staticmethod
    def _extract_integrated_workbook_notes(
        *,
        main_sheet: XlsxSheetSnapshot,
        summary_sheet: XlsxSheetSnapshot | None,
    ) -> dict[str, str | None]:
        background_parts: list[str] = []
        secrets_parts: list[str] = []
        campaign_notes: str | None = None

        if summary_sheet is not None:
            summary_background = KnowledgeIngestor._sheet_optional_text(summary_sheet, "H28")
            if summary_background:
                campaign_notes = summary_background
            for label, cell_reference in (
                ("描述", "C28"),
                ("信仰", "C29"),
                ("重要人", "C30"),
                ("重要地", "C31"),
                ("宝物", "C32"),
                ("特质", "C33"),
                ("伤疤", "C35"),
                ("恐惧", "C36"),
            ):
                value = KnowledgeIngestor._sheet_optional_text(summary_sheet, cell_reference)
                if value:
                    background_parts.append(f"{label}：{value}")
            secret_value = KnowledgeIngestor._sheet_optional_text(summary_sheet, "C34")
            if secret_value:
                secrets_parts.append(f"小秘密：{secret_value}")

        if not background_parts:
            for label, cell_reference in (
                ("形象描述", "AA61"),
                ("思想与信念", "AA63"),
                ("重要之人", "AA65"),
                ("意义非凡之地", "AA67"),
                ("宝贵之物", "AA69"),
                ("特质", "AA71"),
                ("伤口和疤痕", "AA73"),
            ):
                value = KnowledgeIngestor._sheet_optional_text(main_sheet, cell_reference)
                if value:
                    background_parts.append(f"{label}：{value}")

        return {
            "background_traits": "\n".join(background_parts) if background_parts else None,
            "secrets": "\n".join(secrets_parts) if secrets_parts else None,
            "campaign_notes": campaign_notes,
        }

    @staticmethod
    def _extract_integrated_workbook_starting_inventory(
        summary_sheet: XlsxSheetSnapshot | None,
    ) -> list[str]:
        if summary_sheet is None:
            return []
        inventory: list[str] = []
        for cell_reference in ("B22", "E22", "H22", "K22", "B23", "E23", "H23", "K23", "H25"):
            value = KnowledgeIngestor._sheet_optional_text(summary_sheet, cell_reference)
            if value is None:
                continue
            for item in re.split(r"[，,、\s]+", value):
                normalized_item = item.strip()
                if not normalized_item or normalized_item == "0":
                    continue
                if normalized_item not in inventory:
                    inventory.append(normalized_item)
        return inventory

    @staticmethod
    def _collect_formula_without_cached_ambiguities(
        sheet: XlsxSheetSnapshot,
        field_name: str,
        cell_references: list[str],
    ) -> list[str]:
        ambiguities: list[str] = []
        for cell_reference in cell_references:
            cell = sheet.cells.get(cell_reference)
            if cell is None:
                continue
            if cell.formula is not None and not cell.value.strip():
                ambiguities.append(
                    f"formula_without_cached:{field_name}:{sheet.name}!{cell_reference}"
                )
        return ambiguities

    @staticmethod
    def _build_character_sheet_extraction(
        rows: list[dict[str, str]],
    ) -> CharacterSheetExtraction:
        grouped_values: dict[str, dict[str, int | str]] = {
            "core_stats": {},
            "derived_stats": {},
            "skills": {},
        }
        scalar_values: dict[str, str] = {}
        ambiguous_fields: list[str] = []
        for row in rows:
            section = (row.get("section") or "").strip()
            field_name = (row.get("field") or "").strip()
            value = (row.get("value") or "").strip()
            if not section:
                continue
            if section in {"core_stats", "skills"}:
                if not field_name:
                    raise ValueError(f"{section} rows must include a field name")
                grouped_values[section][field_name] = KnowledgeIngestor._parse_required_int(
                    value,
                    field_name=field_name,
                )
            elif section == "derived_stats":
                if not field_name:
                    raise ValueError("derived_stats rows must include a field name")
                grouped_values[section][field_name] = KnowledgeIngestor._parse_optional_int(value)
            elif section == "ambiguous_fields":
                if value:
                    ambiguous_fields.append(f"{field_name}:{value}" if field_name else value)
                elif field_name:
                    ambiguous_fields.append(field_name)
            else:
                if not field_name:
                    continue
                scalar_values[field_name] = value
        missing_scalar_fields = [
            field_name
            for field_name in ("character_id", "investigator_name")
            if field_name not in scalar_values
        ]
        if missing_scalar_fields:
            raise ValueError(
                f"character sheet is missing required fields: {', '.join(missing_scalar_fields)}"
            )
        return CharacterSheetExtraction(
            character_id=scalar_values["character_id"],
            investigator_name=scalar_values["investigator_name"],
            occupation=KnowledgeIngestor._optional_text(scalar_values.get("occupation")),
            era=scalar_values.get("era", "1920s"),
            core_stats={key: int(value) for key, value in grouped_values["core_stats"].items()},
            derived_stats=grouped_values["derived_stats"],
            skills={key: int(value) for key, value in grouped_values["skills"].items()},
            background_traits=KnowledgeIngestor._optional_text(scalar_values.get("background_traits")),
            secrets=KnowledgeIngestor._optional_text(scalar_values.get("secrets")),
            campaign_notes=KnowledgeIngestor._optional_text(scalar_values.get("campaign_notes")),
            extraction_confidence=float(scalar_values.get("extraction_confidence", "1.0")),
            ambiguous_fields=ambiguous_fields,
        )

    @staticmethod
    def _optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @staticmethod
    def _parse_required_int(value: str, *, field_name: str) -> int:
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an integer") from exc

    @staticmethod
    def _parse_optional_int(value: str) -> int | str:
        if value.lstrip("-").isdigit():
            return int(value)
        return value

    @staticmethod
    def _read_xlsx_shared_strings(workbook: zipfile.ZipFile) -> list[str]:
        shared_strings_path = "xl/sharedStrings.xml"
        if shared_strings_path not in workbook.namelist():
            return []
        namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        root = ET.fromstring(workbook.read(shared_strings_path))
        values: list[str] = []
        for item in root.findall("main:si", namespace):
            text_parts = [text_node.text or "" for text_node in item.findall(".//main:t", namespace)]
            values.append("".join(text_parts))
        return values

    @staticmethod
    def _resolve_first_xlsx_worksheet_path(workbook: zipfile.ZipFile) -> str:
        workbook_path = "xl/workbook.xml"
        relationship_path = "xl/_rels/workbook.xml.rels"
        namespace = {
            "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
            "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
        }
        workbook_root = ET.fromstring(workbook.read(workbook_path))
        first_sheet = workbook_root.find("main:sheets/main:sheet", namespace)
        if first_sheet is None:
            raise ValueError("xlsx workbook does not contain any sheets")
        relationship_id = first_sheet.attrib.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        if relationship_id and relationship_path in workbook.namelist():
            relationships_root = ET.fromstring(workbook.read(relationship_path))
            for relationship in relationships_root.findall("rel:Relationship", namespace):
                if relationship.attrib.get("Id") == relationship_id:
                    target = relationship.attrib.get("Target", "worksheets/sheet1.xml").lstrip("/")
                    return f"xl/{target}" if not target.startswith("xl/") else target
        return "xl/worksheets/sheet1.xml"

    @staticmethod
    def _read_xlsx_rows(
        workbook: zipfile.ZipFile,
        *,
        worksheet_path: str,
        shared_strings: list[str],
    ) -> list[dict[str, str]]:
        namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        worksheet_root = ET.fromstring(workbook.read(worksheet_path))
        sheet_rows = worksheet_root.findall(".//main:sheetData/main:row", namespace)
        headers: list[str] | None = None
        rows: list[dict[str, str]] = []
        for row in sheet_rows:
            values_by_index: dict[int, str] = {}
            for cell in row.findall("main:c", namespace):
                cell_reference = cell.attrib.get("r", "")
                column_index = KnowledgeIngestor._xlsx_column_index(cell_reference)
                values_by_index[column_index] = KnowledgeIngestor._read_xlsx_cell_value(
                    cell,
                    namespace=namespace,
                    shared_strings=shared_strings,
                )
            if not values_by_index:
                continue
            max_index = max(values_by_index)
            ordered_values = [values_by_index.get(index, "") for index in range(max_index + 1)]
            if headers is None:
                headers = [value.strip() for value in ordered_values]
                continue
            row_payload = {
                header: ordered_values[index].strip() if index < len(ordered_values) else ""
                for index, header in enumerate(headers)
                if header
            }
            if any(value for value in row_payload.values()):
                rows.append(row_payload)
        return rows

    @staticmethod
    def _read_xlsx_cell_value(
        cell: ET.Element,
        *,
        namespace: dict[str, str],
        shared_strings: list[str],
    ) -> str:
        cell_type = cell.attrib.get("t")
        if cell_type == "s":
            value_node = cell.find("main:v", namespace)
            if value_node is None or value_node.text is None:
                return ""
            shared_index = int(value_node.text)
            if 0 <= shared_index < len(shared_strings):
                return shared_strings[shared_index]
            return ""
        if cell_type == "inlineStr":
            text_parts = [text_node.text or "" for text_node in cell.findall(".//main:t", namespace)]
            return "".join(text_parts)
        value_node = cell.find("main:v", namespace)
        if value_node is None or value_node.text is None:
            return ""
        return value_node.text

    @staticmethod
    def _xlsx_column_index(cell_reference: str) -> int:
        column_reference = "".join(character for character in cell_reference if character.isalpha()).upper()
        index = 0
        for character in column_reference:
            index = index * 26 + (ord(character) - ord("A") + 1)
        return max(index - 1, 0)

    @staticmethod
    def _cell_column_name(cell_reference: str) -> str:
        return "".join(character for character in cell_reference if character.isalpha()).upper()

    @staticmethod
    def _cell_row(cell_reference: str) -> int:
        digits = "".join(character for character in cell_reference if character.isdigit())
        return int(digits) if digits else 0
