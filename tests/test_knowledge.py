from __future__ import annotations

from coc_runner.domain.models import VisibilityScope
from knowledge.retrieval import KnowledgeRetriever
from knowledge.schemas import RuleChunk
from knowledge.terminology import extract_term_matches, normalize_chinese_text


def _make_rule_chunk(
    chunk_id: str,
    *,
    title_zh: str,
    content: str,
    priority: int,
    visibility: VisibilityScope = VisibilityScope.PUBLIC,
    allowed_player_ids: list[str] | None = None,
    overrides_topic: str | None = None,
    tags: list[str] | None = None,
    is_authoritative: bool = True,
) -> RuleChunk:
    return RuleChunk(
        chunk_id=chunk_id,
        source_id="source-core",
        topic_key=overrides_topic or f"topic:{chunk_id}",
        taxonomy_category="skills",
        taxonomy_subcategory="checks",
        document_identity="core-rulebook",
        source_title_zh="克苏鲁的呼唤第七版",
        content=content,
        tags=tags or [],
        priority=priority,
        is_authoritative=is_authoritative,
        title_zh=title_zh,
        short_citation="核心规则 p.100",
        visibility=visibility,
        allowed_player_ids=allowed_player_ids or [],
        overrides_topic=overrides_topic,
    )


def test_normalize_chinese_text_normalizes_aliases_to_canonical_display_terms() -> None:
    raw_text = "请做一次侦察检定，然后进行听觉判定。"

    normalized = normalize_chinese_text(raw_text)

    assert normalized == "请做一次侦查检定，然后进行聆听判定。"


def test_normalize_chinese_text_avoids_over_normalizing_generic_library_word() -> None:
    raw_text = "他站在图书馆门口，等待其他调查员。"

    normalized = normalize_chinese_text(raw_text)

    assert normalized == raw_text


def test_extract_term_matches_returns_correct_dev_ids() -> None:
    raw_text = "请做一次侦察检定，并判定SAN是否下降。"

    matches = extract_term_matches(raw_text)

    assert [match.dev_id for match in matches] == ["spot_hidden", "sanity_point"]
    assert [raw_text[match.start : match.end] for match in matches] == [
        match.matched_alias for match in matches
    ]


def test_retrieval_priority_drops_lower_priority_chunks_for_same_topic() -> None:
    lower_priority_chunk = _make_rule_chunk(
        "chunk-low",
        title_zh="斗殴",
        content="较旧的斗殴规则说明。",
        priority=10,
        overrides_topic="skill:brawl",
        tags=["斗殴"],
    )
    higher_priority_chunk = _make_rule_chunk(
        "chunk-high",
        title_zh="斗殴",
        content="优先采用的斗殴规则说明。",
        priority=50,
        overrides_topic="skill:brawl",
        tags=["斗殴"],
    )
    retriever = KnowledgeRetriever([lower_priority_chunk, higher_priority_chunk])

    result = retriever.query_rules("斗殴检定")

    assert [chunk.chunk_id for chunk in result.matched_chunks] == ["chunk-high"]


def test_retrieval_visibility_filters_kp_only_public_and_shared_subset() -> None:
    public_chunk = _make_rule_chunk(
        "chunk-public",
        title_zh="公共规则",
        content="侦查检定的公共说明。",
        priority=10,
    )
    kp_only_chunk = _make_rule_chunk(
        "chunk-kp",
        title_zh="KP 私有规则",
        content="侦查检定的 KP 说明。",
        priority=20,
        visibility=VisibilityScope.KP_ONLY,
    )
    shared_subset_chunk = _make_rule_chunk(
        "chunk-shared",
        title_zh="共享子集规则",
        content="侦查检定的共享说明。",
        priority=15,
        visibility=VisibilityScope.SHARED_SUBSET,
        allowed_player_ids=["investigator-1"],
    )
    retriever = KnowledgeRetriever([public_chunk, kp_only_chunk, shared_subset_chunk])

    investigator_one = retriever.query_rules(
        "侦查检定",
        viewer_role="investigator",
        viewer_id="investigator-1",
    )
    investigator_two = retriever.query_rules(
        "侦查检定",
        viewer_role="investigator",
        viewer_id="investigator-2",
    )
    keeper = retriever.query_rules("侦查检定", viewer_role="keeper")

    assert [chunk.chunk_id for chunk in investigator_one.matched_chunks] == [
        "chunk-shared",
        "chunk-public",
    ]
    assert [chunk.chunk_id for chunk in investigator_two.matched_chunks] == ["chunk-public"]
    assert [chunk.chunk_id for chunk in keeper.matched_chunks] == [
        "chunk-kp",
        "chunk-shared",
        "chunk-public",
    ]


def test_deterministic_resolution_required_passes_through() -> None:
    retriever = KnowledgeRetriever(
        [
            _make_rule_chunk(
                "chunk-spot-hidden",
                title_zh="侦查",
                content="侦查检定用于发现隐藏线索。",
                priority=30,
                tags=["侦查"],
            )
        ]
    )

    result = retriever.query_rules(
        "观察检定",
        deterministic_resolution_required=True,
    )

    assert result.deterministic_resolution_required is True
    assert result.normalized_query == "侦查检定"


def test_conflict_explanation_and_review_reason_are_chinese_first() -> None:
    first_chunk = _make_rule_chunk(
        "chunk-conflict-a",
        title_zh="侦查",
        content="侦查失败时立刻失去线索。",
        priority=60,
        overrides_topic="term:spot_hidden",
    )
    second_chunk = _make_rule_chunk(
        "chunk-conflict-b",
        title_zh="侦查",
        content="侦查失败时仍可获得线索，但要付出代价。",
        priority=60,
        overrides_topic="term:spot_hidden",
    )
    retriever = KnowledgeRetriever([first_chunk, second_chunk])

    result = retriever.query_rules("侦查失败后怎么处理")

    assert result.conflicts_found is True
    assert result.conflict_explanation is not None
    assert "主题“侦查”" in result.conflict_explanation
    assert result.human_review_recommended is True
    assert result.human_review_reason is not None
    assert "Keeper人工复核" in result.human_review_reason


def test_bigram_gate_does_not_match_long_content_without_structured_field_support() -> None:
    chunk = _make_rule_chunk(
        "chunk-long-narrative",
        title_zh="旅店背景",
        content=(
            "这是一段很长的叙事正文，描述旅店大厅的壁炉里残留余烬、潮湿空气、"
            "楼梯木板声以及窗边摇晃的帘子，但并不是规则条目。"
        ),
        priority=20,
        tags=["叙事", "环境"],
    )
    retriever = KnowledgeRetriever([chunk])

    result = retriever.query_rules("壁炉余烬")

    assert result.matched_chunks == []


def test_bigram_gate_still_matches_when_title_tags_or_topic_key_carry_overlap() -> None:
    chunk = _make_rule_chunk(
        "chunk-structured-hit",
        title_zh="档案检索",
        content="正文只给一个简短说明。",
        priority=20,
        tags=["旧报纸", "馆藏"],
        overrides_topic="term:library_archive_lookup",
    )
    retriever = KnowledgeRetriever([chunk])

    result = retriever.query_rules("档案馆藏")

    assert [matched.chunk_id for matched in result.matched_chunks] == ["chunk-structured-hit"]


def test_chunk_relevance_score_keeps_content_bonus_after_bigram_gate_narrows() -> None:
    relevant_chunk = _make_rule_chunk(
        "chunk-relevant-content",
        title_zh="线索分析",
        content="地上的脚印痕迹一路延伸到暗门前。",
        priority=20,
        tags=["脚印", "痕迹"],
        overrides_topic="topic:relevant-content",
    )
    generic_chunk = _make_rule_chunk(
        "chunk-generic-content",
        title_zh="线索分析",
        content="这里只是一般性的描述，没有额外细节。",
        priority=20,
        tags=["脚印", "痕迹"],
        overrides_topic="topic:generic-content",
    )
    retriever = KnowledgeRetriever([relevant_chunk, generic_chunk])
    query_text = "地上脚印痕迹"
    normalized_query = normalize_chinese_text(query_text)
    term_matches = extract_term_matches(query_text)

    assert retriever._matches_query(relevant_chunk, query_text, normalized_query, term_matches)
    assert retriever._matches_query(generic_chunk, query_text, normalized_query, term_matches)

    relevant_score = retriever._chunk_relevance_score(
        relevant_chunk,
        normalized_query=normalized_query,
        term_matches=term_matches,
    )
    generic_score = retriever._chunk_relevance_score(
        generic_chunk,
        normalized_query=normalized_query,
        term_matches=term_matches,
    )

    assert relevant_score > generic_score
