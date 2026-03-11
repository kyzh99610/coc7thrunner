from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from knowledge.retrieval import KnowledgeRetriever
from knowledge.schemas import RuleChunk, RuleQueryResult
from knowledge.terminology import normalize_chinese_text


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "knowledge"
ACCEPTANCE_CASES = json.loads(
    (FIXTURE_DIR / "rules_query_acceptance_cases.json").read_text(encoding="utf-8")
)


def _make_rule_chunk(
    chunk_id: str,
    *,
    title_zh: str,
    content: str,
    topic_key: str,
    priority: int,
    tags: list[str] | None = None,
) -> RuleChunk:
    return RuleChunk(
        chunk_id=chunk_id,
        source_id="phase2-unit-source",
        topic_key=topic_key,
        taxonomy_category="rules",
        taxonomy_subcategory="acceptance",
        document_identity="phase2-unit-source",
        source_title_zh="Phase2 单测规则",
        content=content,
        tags=tags or [],
        priority=priority,
        is_authoritative=True,
        title_zh=title_zh,
        short_citation="Phase2 单测 p.1",
    )


def _register_source(
    client: TestClient,
    *,
    source_id: str,
    source_kind: str,
    source_title_zh: str,
    source_path: Path,
    default_priority: int,
    default_visibility: str = "public",
    allowed_player_ids: list[str] | None = None,
) -> None:
    response = client.post(
        "/knowledge/register-source",
        json={
            "source_id": source_id,
            "source_kind": source_kind,
            "source_format": "markdown",
            "source_title_zh": source_title_zh,
            "document_identity": source_id,
            "source_path": str(source_path),
            "default_priority": default_priority,
            "default_visibility": default_visibility,
            "allowed_player_ids": allowed_player_ids or [],
            "is_authoritative": True,
        },
    )
    assert response.status_code == 201


def _seed_acceptance_sources(client: TestClient) -> None:
    _register_source(
        client,
        source_id="acceptance-core",
        source_kind="rulebook",
        source_title_zh="受理核心规则样例",
        source_path=FIXTURE_DIR / "acceptance_core_rules.md",
        default_priority=30,
    )
    _register_source(
        client,
        source_id="acceptance-house",
        source_kind="house_rule",
        source_title_zh="受理房规样例",
        source_path=FIXTURE_DIR / "acceptance_house_rules.md",
        default_priority=80,
    )
    _register_source(
        client,
        source_id="acceptance-shared",
        source_kind="module",
        source_title_zh="受理共享线索样例",
        source_path=FIXTURE_DIR / "acceptance_shared_rules.md",
        default_priority=50,
        default_visibility="shared_subset",
        allowed_player_ids=["investigator-1"],
    )
    _register_source(
        client,
        source_id="acceptance-kp",
        source_kind="module",
        source_title_zh="受理KP私有样例",
        source_path=FIXTURE_DIR / "acceptance_kp_rules.md",
        default_priority=60,
        default_visibility="kp_only",
    )

    for source_id in (
        "acceptance-core",
        "acceptance-house",
        "acceptance-shared",
        "acceptance-kp",
    ):
        response = client.post("/knowledge/ingest-file", json={"source_id": source_id})
        assert response.status_code == 200


def test_rules_query_acceptance_cases_use_persisted_knowledge(client: TestClient) -> None:
    _seed_acceptance_sources(client)

    for case in ACCEPTANCE_CASES:
        response = client.post(
            "/rules/query",
            json={
                "query_text": case["query_text"],
                "viewer_role": case.get("viewer_role", "investigator"),
                "viewer_id": case.get("viewer_id"),
                "deterministic_resolution_required": case.get(
                    "deterministic_resolution_required",
                    False,
                ),
            },
        )
        assert response.status_code == 200, case["case_id"]
        result = RuleQueryResult.model_validate(response.json())

        assert result.original_query == case["query_text"], case["case_id"]
        assert result.human_review_recommended is case["expect_human_review"], case["case_id"]

        expected_review_reason_contains = case.get("expected_review_reason_contains")
        if expected_review_reason_contains is not None:
            assert result.human_review_reason is not None, case["case_id"]
            assert expected_review_reason_contains in result.human_review_reason, case["case_id"]

        if case.get("expect_no_matches"):
            assert result.matched_chunks == [], case["case_id"]
            assert result.citations == [], case["case_id"]
            assert result.chinese_answer_draft is None, case["case_id"]
            continue

        minimum_matches = case.get("minimum_matches", 1)
        if minimum_matches == 0:
            assert result.matched_chunks == [], case["case_id"]
            assert result.citations == [], case["case_id"]
            assert result.chinese_answer_draft is None, case["case_id"]
            continue

        assert len(result.matched_chunks) >= minimum_matches, case["case_id"]
        assert result.chinese_answer_draft is not None, case["case_id"]
        assert result.chinese_answer_draft.startswith("优先参考"), case["case_id"]

        if "expected_normalized_query" in case:
            assert result.normalized_query == case["expected_normalized_query"], case["case_id"]

        expected_topics = case.get("expected_topics", [])
        if expected_topics:
            resolved_topics = {chunk.resolved_topic for chunk in result.matched_chunks}
            for expected_topic in expected_topics:
                assert expected_topic in resolved_topics, case["case_id"]

        expected_top_topic = case.get("expected_top_topic")
        if expected_top_topic is not None:
            assert result.matched_chunks[0].resolved_topic == expected_top_topic, case["case_id"]

        expected_top_topic_any_of = case.get("expected_top_topic_any_of")
        if expected_top_topic_any_of is not None:
            assert (
                result.matched_chunks[0].resolved_topic in expected_top_topic_any_of
            ), case["case_id"]

        expected_priority = case.get("expected_priority")
        if expected_priority is not None:
            assert result.matched_chunks[0].priority == expected_priority, case["case_id"]

        for fragment in case.get("expected_answer_contains", []):
            assert fragment in (result.chinese_answer_draft or ""), case["case_id"]

        expected_citation_prefix = case.get("expected_citation_prefix")
        if expected_citation_prefix is not None:
            assert result.citations, case["case_id"]
            assert all(
                citation.startswith(expected_citation_prefix) for citation in result.citations
            ), case["case_id"]

        if case.get("deterministic_resolution_required"):
            assert result.deterministic_resolution_required is True, case["case_id"]
            assert result.deterministic_handoff_topic is not None, case["case_id"]


def test_rules_query_acceptance_cases_with_zero_minimum_matches_return_no_chunks(
    client: TestClient,
) -> None:
    _seed_acceptance_sources(client)

    zero_match_cases = [
        case
        for case in ACCEPTANCE_CASES
        if case.get("minimum_matches") == 0 and not case.get("expect_no_matches")
    ]

    for case in zero_match_cases:
        response = client.post(
            "/rules/query",
            json={
                "query_text": case["query_text"],
                "viewer_role": case.get("viewer_role", "investigator"),
                "viewer_id": case.get("viewer_id"),
                "deterministic_resolution_required": case.get(
                    "deterministic_resolution_required",
                    False,
                ),
            },
        )
        assert response.status_code == 200, case["case_id"]
        result = RuleQueryResult.model_validate(response.json())

        assert result.matched_chunks == [], case["case_id"]
        assert result.citations == [], case["case_id"]
        assert result.chinese_answer_draft is None, case["case_id"]


def test_retrieval_bigram_matching_handles_short_chinese_action_phrases_with_structured_fields() -> None:
    retriever = KnowledgeRetriever(
        [
            _make_rule_chunk(
                "phase2-spot-hidden",
                title_zh="侦查",
                content="侦查技能用于发现地上的脚印线索与隐藏痕迹。",
                topic_key="term:spot_hidden",
                priority=100,
                tags=["脚印", "线索"],
            )
        ]
    )

    result = retriever.query_rules("我仔细看地上的脚印线索")

    assert result.matched_chunks
    assert result.matched_chunks[0].resolved_topic == "term:spot_hidden"
    assert normalize_chinese_text("我仔细看地上的脚印线索") not in result.matched_chunks[0].text


def test_phase2_fixture_queries_hit_expected_rule_chunks(client: TestClient) -> None:
    _register_source(
        client,
        source_id="phase2-core",
        source_kind="rulebook",
        source_title_zh="Phase2 核心规则样例",
        source_path=FIXTURE_DIR / "coc7e_core_rules_phase2.md",
        default_priority=100,
    )
    ingest_response = client.post("/knowledge/ingest-file", json={"source_id": "phase2-core"})
    assert ingest_response.status_code == 200

    cases = [
        ("我查看门边的脚印", {"term:spot_hidden"}),
        ("我去图书馆查阅旧报纸", {"term:library_use"}),
        ("我劝说老板让我们进去", {"term:persuade"}),
        ("我尝试说服旅店老板开门", {"term:persuade"}),
        ("我推动侦查检定", {"term:pushed_roll"}),
        ("目睹深渊生物后进行理智检定", {"term:sanity_check"}),
    ]

    for query_text, expected_topics in cases:
        response = client.post(
            "/rules/query",
            json={
                "query_text": query_text,
                "viewer_role": "investigator",
                "deterministic_resolution_required": True,
            },
        )
        assert response.status_code == 200, query_text
        result = RuleQueryResult.model_validate(response.json())

        assert result.matched_chunks, query_text
        assert result.citations, query_text
        assert result.chinese_answer_draft is not None, query_text
        resolved_topics = {chunk.resolved_topic for chunk in result.matched_chunks}
        assert expected_topics & resolved_topics, query_text
        assert result.deterministic_handoff_topic is not None, query_text
