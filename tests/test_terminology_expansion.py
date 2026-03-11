from __future__ import annotations

import pytest

from knowledge.terminology import extract_term_matches


def test_extract_term_matches_expanded_terms_cover_common_investigation_skills() -> None:
    psychology_matches = extract_term_matches("我想用心理学分析这个NPC")
    assert any(match.dev_id == "psychology" for match in psychology_matches)

    persuade_matches = extract_term_matches("我尝试说服店主")
    assert any(match.dev_id == "persuade" for match in persuade_matches)

    dodge_matches = extract_term_matches("进行一次闪避")
    assert any(match.dev_id == "dodge" for match in dodge_matches)

    mythos_matches = extract_term_matches("我查看克苏鲁神话相关资料")
    assert any(match.dev_id == "cthulhu_mythos" for match in mythos_matches)


@pytest.mark.parametrize(
    ("query_text", "expected_dev_id"),
    [
        ("我劝说老板让我们进去", "persuade"),
        ("我威胁他把钥匙交出来", "intimidate"),
        ("我要治疗伤口", "medicine"),
    ],
)
def test_extract_term_matches_social_and_medical_aliases_without_extra_context(
    query_text: str,
    expected_dev_id: str,
) -> None:
    matches = extract_term_matches(query_text)

    assert any(match.dev_id == expected_dev_id for match in matches)


@pytest.mark.parametrize(
    "query_text",
    [
        "我查看过菜单了",
        "我做过观察天气的作业",
    ],
)
def test_extract_term_matches_do_not_use_removed_ambiguous_context_markers(
    query_text: str,
) -> None:
    matches = extract_term_matches(query_text)

    assert all(match.dev_id != "spot_hidden" for match in matches)
