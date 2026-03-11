from __future__ import annotations

import re
from collections import defaultdict

from coc_runner.domain.models import VisibilityScope
from knowledge.schemas import RetrievedChunk, RuleChunk, RuleQueryResult
from knowledge.terminology import (
    NormalizedTermMatch,
    extract_term_matches,
    normalize_chinese_text,
)


class KnowledgeRetriever:
    """In-memory retriever stub for deterministic contract testing."""

    _HUMAN_REVIEW_TERMS = ("人工审阅", "人工审核", "人工确认", "重大理智损失", "疯狂发作")
    _SANITY_REVIEW_TERMS = ("理智", "SAN", "疯狂", "发疯", "秘密揭露")
    _QUERY_STOP_TERMS = (
        "什么时候",
        "什么场景",
        "什么技能",
        "会发生什么",
        "会怎样",
        "是什么",
        "是不是",
        "能不能",
        "怎么处理",
        "怎么算",
        "如何",
        "怎么",
        "在哪",
        "为何",
        "为什么",
        "需要",
        "该用",
        "处理",
        "规则",
        "检定",
        "判定",
        "技能",
        "失败后",
        "失败时",
        "失败",
        "成功",
        "成功时",
        "用来",
        "适合",
        "什么",
        "我的",
    )
    _ASCII_STOP_TOKENS = {"keeper", "kp"}

    def __init__(self, chunks: list[RuleChunk] | None = None) -> None:
        self._chunks: list[RuleChunk] = list(chunks or [])

    def replace_chunks(self, chunks: list[RuleChunk]) -> None:
        self._chunks = list(chunks)

    def add_chunks(self, chunks: list[RuleChunk]) -> None:
        self._chunks.extend(chunks)

    def query_rules(
        self,
        query_text: str,
        *,
        viewer_role: str = "investigator",
        viewer_id: str | None = None,
        minimum_priority: int | None = None,
        deterministic_resolution_required: bool = False,
    ) -> RuleQueryResult:
        normalized_query = normalize_chinese_text(query_text)
        term_matches = extract_term_matches(query_text)
        visible_chunks = [
            chunk
            for chunk in self._chunks
            if self._is_visible_to_caller(
                chunk,
                viewer_role=viewer_role,
                viewer_id=viewer_id,
            )
        ]
        matching_chunks = [
            chunk
            for chunk in visible_chunks
            if self._matches_query(chunk, query_text, normalized_query, term_matches)
        ]
        if minimum_priority is not None:
            matching_chunks = [
                chunk for chunk in matching_chunks if chunk.priority >= minimum_priority
            ]

        selected_chunks, conflict_groups = self._select_highest_priority_chunks(
            matching_chunks
        )
        selected_chunks.sort(
            key=lambda chunk: (
                chunk.priority,
                self._chunk_relevance_score(
                    chunk,
                    normalized_query=normalized_query,
                    term_matches=term_matches,
                ),
                int(chunk.is_authoritative),
            ),
            reverse=True,
        )
        conflicts_found = bool(conflict_groups)
        retrieved_chunks = [
            RetrievedChunk(
                chunk_id=chunk.chunk_id,
                text=chunk.content,
                topic_key=chunk.topic_key,
                resolved_topic=chunk.overrides_topic or chunk.topic_key,
                page_reference=chunk.page_reference,
                short_citation=chunk.short_citation,
                visibility=chunk.visibility,
                is_authoritative=chunk.is_authoritative,
                priority=chunk.priority,
                core_clue_flag=chunk.core_clue_flag,
                alternate_paths=list(chunk.alternate_paths),
                tags=list(chunk.tags),
            )
            for chunk in selected_chunks
        ]
        confidence_score = self._calculate_confidence(
            normalized_query,
            term_matches_count=len(term_matches),
            matched_chunk_count=len(retrieved_chunks),
        )
        citations = self._collect_citations(selected_chunks)
        deterministic_handoff_topic = (
            (selected_chunks[0].overrides_topic or selected_chunks[0].topic_key)
            if selected_chunks and deterministic_resolution_required
            else None
        )
        human_review_reason = self._build_human_review_reason(
            normalized_query or query_text,
            selected_chunks,
            conflicts_found=conflicts_found,
        )
        human_review_recommended = human_review_reason is not None or any(
            not chunk.is_authoritative for chunk in selected_chunks
        )
        chinese_answer_draft = self._build_chinese_answer_draft(
            selected_chunks,
            citations=citations,
            conflict_groups=conflict_groups,
        )
        return RuleQueryResult(
            original_query=query_text,
            normalized_query=normalized_query if normalized_query != query_text else None,
            matched_chunks=retrieved_chunks,
            citations=citations,
            confidence_score=confidence_score,
            conflicts_found=conflicts_found,
            conflict_explanation=self._build_conflict_explanation(conflict_groups),
            human_review_recommended=human_review_recommended,
            human_review_reason=human_review_reason,
            deterministic_resolution_required=deterministic_resolution_required,
            deterministic_handoff_topic=deterministic_handoff_topic,
            chinese_answer_draft=chinese_answer_draft,
            structured_payload={
                "term_match_count": len(term_matches),
                "visible_chunk_count": len(visible_chunks),
                "selected_chunk_count": len(retrieved_chunks),
                "selected_topics": [
                    chunk.overrides_topic or chunk.topic_key for chunk in selected_chunks
                ],
            },
        )

    def _is_visible_to_caller(
        self,
        chunk: RuleChunk,
        *,
        viewer_role: str,
        viewer_id: str | None,
    ) -> bool:
        # TODO: If knowledge visibility diverges from session visibility later, split this into a dedicated policy object.
        if chunk.visibility == VisibilityScope.SYSTEM_INTERNAL:
            return False
        if viewer_role == "keeper":
            return True
        if chunk.visibility == VisibilityScope.PUBLIC:
            return True
        if chunk.visibility == VisibilityScope.KP_ONLY:
            return False
        if viewer_id is None:
            return False
        if chunk.visibility in {
            VisibilityScope.SHARED_SUBSET,
            VisibilityScope.SHARED_CLUE,
            VisibilityScope.INVESTIGATOR_PRIVATE,
            VisibilityScope.HIDDEN_CLUE,
        }:
            return viewer_id in chunk.allowed_player_ids
        return False

    def _matches_query(
        self,
        chunk: RuleChunk,
        query_text: str,
        normalized_query: str,
        term_matches: list[NormalizedTermMatch],
    ) -> bool:
        normalized_haystack = normalize_chinese_text(
            " ".join(
                [
                    chunk.title_zh,
                    chunk.content,
                    " ".join(chunk.tags),
                    chunk.topic_key,
                    chunk.overrides_topic or "",
                    chunk.taxonomy_category,
                    chunk.taxonomy_subcategory,
                ]
            )
        )
        if term_matches:
            if any(match.canonical_zh in normalized_haystack for match in term_matches):
                return True
            if any(match.dev_id in chunk.machine_flags for match in term_matches):
                return True
            if any(match.dev_id in {chunk.topic_key, chunk.overrides_topic or ""} for match in term_matches):
                return True
        if normalized_query and normalized_query in normalized_haystack:
            return True
        for token in self._split_query_tokens(normalized_query or query_text):
            if token and token in normalized_haystack:
                return True
        bigram_query_text = self._build_bigram_query_text(normalized_query or query_text)
        if self._bigram_overlap_score(
            bigram_query_text,
            self._build_bigram_gate_haystack(chunk),
        ) >= 2:
            return True
        return False

    @staticmethod
    def _build_bigram_gate_haystack(chunk: RuleChunk) -> str:
        return normalize_chinese_text(
            " ".join(
                [
                    chunk.title_zh,
                    " ".join(chunk.tags),
                    chunk.topic_key,
                ]
            )
        )

    def _select_highest_priority_chunks(
        self,
        chunks: list[RuleChunk],
    ) -> tuple[list[RuleChunk], list[list[RuleChunk]]]:
        grouped_chunks: dict[str, list[RuleChunk]] = defaultdict(list)
        for chunk in chunks:
            group_key = chunk.overrides_topic or chunk.topic_key
            grouped_chunks[group_key].append(chunk)

        selected_chunks: list[RuleChunk] = []
        conflict_groups: list[list[RuleChunk]] = []
        for group in grouped_chunks.values():
            ordered_group = sorted(
                group,
                key=lambda chunk: (chunk.priority, int(chunk.is_authoritative)),
                reverse=True,
            )
            selected_chunks.append(ordered_group[0])
            if (
                len(ordered_group) > 1
                and ordered_group[0].priority == ordered_group[1].priority
                and ordered_group[0].content != ordered_group[1].content
            ):
                highest_priority = ordered_group[0].priority
                conflict_groups.append(
                    [
                        chunk
                        for chunk in ordered_group
                        if chunk.priority == highest_priority
                    ]
                )

        selected_chunks.sort(
            key=lambda chunk: (chunk.priority, int(chunk.is_authoritative)),
            reverse=True,
        )
        return selected_chunks, conflict_groups

    @staticmethod
    def _split_query_tokens(query_text: str) -> list[str]:
        tokens: list[str] = []
        for token in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", query_text):
            if re.fullmatch(r"[A-Za-z0-9]+", token):
                if token.lower() not in KnowledgeRetriever._ASCII_STOP_TOKENS:
                    tokens.append(token)
                continue
            normalized_token = token
            for stop_term in KnowledgeRetriever._QUERY_STOP_TERMS:
                normalized_token = normalized_token.replace(stop_term, " ")
            for fragment in normalized_token.split():
                if len(fragment) >= 2:
                    tokens.append(fragment)
        return list(dict.fromkeys(tokens))

    @staticmethod
    def _bigram_overlap_score(query_text: str, haystack: str) -> int:
        """Count 2-character Chinese bigram overlaps between query and haystack."""

        query_chars = re.findall(r"[\u4e00-\u9fff]", query_text)
        hay_chars = re.findall(r"[\u4e00-\u9fff]", haystack)
        if len(query_chars) < 2 or len(hay_chars) < 2:
            return 0
        query_bigrams = {
            query_chars[index] + query_chars[index + 1]
            for index in range(len(query_chars) - 1)
        }
        hay_bigrams = {
            hay_chars[index] + hay_chars[index + 1]
            for index in range(len(hay_chars) - 1)
        }
        return len(query_bigrams & hay_bigrams)

    @staticmethod
    def _build_bigram_query_text(query_text: str) -> str:
        tokens = KnowledgeRetriever._split_query_tokens(query_text)
        if tokens:
            return "".join(tokens)
        return query_text

    def _chunk_relevance_score(
        self,
        chunk: RuleChunk,
        *,
        normalized_query: str,
        term_matches: list[NormalizedTermMatch],
    ) -> int:
        resolved_topic = chunk.overrides_topic or chunk.topic_key
        normalized_title = normalize_chinese_text(chunk.title_zh)
        normalized_content = normalize_chinese_text(chunk.content)
        score = 0
        for match in term_matches:
            term_topic = f"term:{match.dev_id}"
            if resolved_topic == term_topic or chunk.topic_key == term_topic:
                score += 10
            if normalized_title == match.canonical_zh:
                score += 6
            elif match.canonical_zh in normalized_title:
                score += 4
            if match.canonical_zh in normalized_content:
                score += 2
        if normalized_query and normalized_title and normalized_title in normalized_query:
            score += 3
        bigram_score = KnowledgeRetriever._bigram_overlap_score(
            KnowledgeRetriever._build_bigram_query_text(normalized_query),
            normalized_content,
        )
        score += min(bigram_score, 4)
        return score

    @staticmethod
    def _calculate_confidence(
        normalized_query: str,
        *,
        term_matches_count: int,
        matched_chunk_count: int,
    ) -> float | None:
        if not normalized_query:
            return None
        if matched_chunk_count == 0:
            return 0.0
        if term_matches_count > 0:
            return 0.95
        return 0.7

    @staticmethod
    def _build_chinese_answer_draft(
        chunks: list[RuleChunk],
        *,
        citations: list[str],
        conflict_groups: list[list[RuleChunk]],
    ) -> str | None:
        if not chunks:
            return None
        lead_chunk = chunks[0]
        answer_parts = [
            f"优先参考“{lead_chunk.title_zh}”：{lead_chunk.content}"
        ]
        if len(chunks) > 1:
            supplemental_titles = "、".join(chunk.title_zh for chunk in chunks[1:3])
            answer_parts.append(f"补充参考：{supplemental_titles}。")
        if conflict_groups:
            answer_parts.append("当前命中同优先级冲突规则，需人工裁定。")
        if citations:
            answer_parts.append(f"引用：{'；'.join(citations)}。")
        return "".join(answer_parts)

    @staticmethod
    def _collect_citations(chunks: list[RuleChunk]) -> list[str]:
        citations: list[str] = []
        for chunk in chunks:
            citation = KnowledgeRetriever._format_citation(chunk)
            if citation and citation not in citations:
                citations.append(citation)
        return citations

    @staticmethod
    def _format_citation(chunk: RuleChunk) -> str | None:
        if chunk.short_citation:
            return chunk.short_citation
        source_label = chunk.source_title_zh or chunk.document_identity
        if chunk.page_reference is not None:
            citation = f"《{source_label}》第{chunk.page_reference}页"
            if chunk.chapter:
                return f"{citation}·{chunk.chapter}"
            return citation
        if chunk.chapter:
            return f"《{source_label}》·{chunk.chapter}"
        return f"《{source_label}》"

    def _build_human_review_reason(
        self,
        query_text: str,
        chunks: list[RuleChunk],
        *,
        conflicts_found: bool,
    ) -> str | None:
        query_mentions_human_review = any(term in query_text for term in self._HUMAN_REVIEW_TERMS)
        query_mentions_sanity = any(term in query_text for term in self._SANITY_REVIEW_TERMS)
        chunk_text = " ".join([chunk.title_zh + " " + chunk.content for chunk in chunks])
        chunks_mention_human_review = any(term in chunk_text for term in self._HUMAN_REVIEW_TERMS)
        chunks_mention_sanity = any(term in chunk_text for term in self._SANITY_REVIEW_TERMS)

        if conflicts_found:
            return "同一主题命中相同优先级但内容不同的规则，建议Keeper人工复核。"
        if any(not chunk.is_authoritative for chunk in chunks):
            return "当前结果包含非权威来源，建议人工确认。"
        if query_mentions_sanity and query_mentions_human_review:
            return "查询同时涉及理智高风险规则与人工审阅请求，建议Keeper人工复核。"
        if query_mentions_sanity:
            return "查询涉及理智、疯狂或秘密揭露等高风险规则，建议Keeper人工复核。"
        if query_mentions_human_review:
            return "查询直接涉及人工审阅或高风险节点，建议Keeper人工复核。"
        if chunks_mention_sanity and chunks_mention_human_review:
            return "匹配规则同时涉及理智高风险节点与人工审阅要求，建议Keeper人工复核。"
        if chunks_mention_sanity:
            return "匹配规则涉及理智、疯狂或秘密揭露等高风险场景，建议Keeper人工复核。"
        if chunks_mention_human_review:
            return "匹配规则涉及人工审阅或高风险节点，建议Keeper人工复核。"
        return None

    @staticmethod
    def _build_conflict_explanation(conflict_groups: list[list[RuleChunk]]) -> str | None:
        if not conflict_groups:
            return None
        summarized_groups: list[str] = []
        for group in conflict_groups[:2]:
            topic_label = group[0].title_zh
            competing_citations = "、".join(
                KnowledgeRetriever._format_citation(chunk) or chunk.chunk_id
                for chunk in group[:3]
            )
            summarized_groups.append(
                f"主题“{topic_label}”同时命中：{competing_citations}"
            )
        return "；".join(summarized_groups) + "。"
