from __future__ import annotations

from urllib.parse import quote

from fastapi.testclient import TestClient

from coc_runner.domain.scenario_examples import whispering_guesthouse_payload
from tests.helpers import make_participant


def _register_rule_source(
    client: TestClient,
    *,
    source_id: str,
    source_title_zh: str,
    content: str,
    default_priority: int = 40,
) -> None:
    register_response = client.post(
        "/knowledge/register-source",
        json={
            "source_id": source_id,
            "source_kind": "rulebook",
            "source_format": "plain_text",
            "source_title_zh": source_title_zh,
            "document_identity": source_id,
            "default_priority": default_priority,
            "is_authoritative": True,
        },
    )
    assert register_response.status_code == 201
    ingest_response = client.post(
        "/knowledge/ingest-text",
        json={"source_id": source_id, "content": content},
    )
    assert ingest_response.status_code == 200


def _start_rules_session(client: TestClient) -> str:
    response = client.post(
        "/sessions/start",
        json={
            "keeper_name": "KP",
            "keeper_id": "keeper-1",
            "scenario": whispering_guesthouse_payload(),
            "participants": [make_participant("investigator-1", "林舟")],
        },
    )
    assert response.status_code == 201
    return response.json()["session_id"]


def test_keeper_runtime_assistance_shows_more_rules_query_link_with_default_query(
    client: TestClient,
) -> None:
    _register_rule_source(
        client,
        source_id="playtest-rules-link",
        source_title_zh="侦查规则",
        content="# 侦查\n侦查用于发现隐藏线索与可疑痕迹。",
    )
    session_id = _start_rules_session(client)
    query_text = "侦察能发现隐藏线索吗"
    action_response = client.post(
        f"/sessions/{session_id}/player-action",
        json={
            "actor_id": "investigator-1",
            "action_text": "我按侦查规则检查前台记账桌的异常痕迹。",
            "structured_action": {"type": "inspect_front_desk"},
            "rules_query_text": query_text,
            "deterministic_resolution_required": True,
        },
    )
    assert action_response.status_code == 202

    keeper_response = client.get(f"/playtest/sessions/{session_id}/keeper")
    investigator_response = client.get(
        f"/playtest/sessions/{session_id}/investigator/investigator-1"
    )

    assert keeper_response.status_code == 200
    keeper_html = keeper_response.text
    assert "更多规则查询" in keeper_html
    assert (
        f'/playtest/sessions/{session_id}/rules?query_text={quote(query_text)}"'
        in keeper_html
    )

    assert investigator_response.status_code == 200
    assert "更多规则查询" not in investigator_response.text


def test_playtest_rules_query_page_prefills_query_and_renders_results(
    client: TestClient,
) -> None:
    _register_rule_source(
        client,
        source_id="playtest-rules-page",
        source_title_zh="侦查规则",
        content="# 侦查\n侦查用于发现隐藏线索与可疑痕迹。",
    )
    session_id = _start_rules_session(client)
    query_text = "侦察能发现隐藏线索吗"

    response = client.get(
        f"/playtest/sessions/{session_id}/rules",
        params={"query_text": query_text},
    )

    assert response.status_code == 200
    html = response.text
    assert "规则查询" in html
    assert f'value="{query_text}"' in html
    assert "侦查用于发现隐藏线索与可疑痕迹。" in html
    assert "《侦查规则》片段1" in html
    assert f'/playtest/sessions/{session_id}/keeper"' in html


def test_playtest_rules_query_page_post_submission_succeeds(
    client: TestClient,
) -> None:
    _register_rule_source(
        client,
        source_id="playtest-rules-post",
        source_title_zh="聆听规则",
        content="# 聆听\n聆听用于察觉远处声音与门后的动静。",
    )
    session_id = _start_rules_session(client)

    response = client.post(
        f"/playtest/sessions/{session_id}/rules",
        data={"query_text": "听觉判定"},
    )

    assert response.status_code == 200
    html = response.text
    assert "规则查询结果" in html
    assert "聆听用于察觉远处声音与门后的动静。" in html


def test_playtest_rules_query_page_shows_natural_empty_state_when_no_matches(
    client: TestClient,
) -> None:
    session_id = _start_rules_session(client)

    response = client.get(
        f"/playtest/sessions/{session_id}/rules",
        params={"query_text": "完全不存在的规则主题"},
    )

    assert response.status_code == 200
    html = response.text
    assert "规则查询" in html
    assert "当前查询没有命中规则摘要。" in html


def test_playtest_rules_query_page_invalid_submission_shows_structured_error(
    client: TestClient,
) -> None:
    session_id = _start_rules_session(client)

    response = client.post(
        f"/playtest/sessions/{session_id}/rules",
        data={"query_text": ""},
    )

    assert response.status_code == 422
    html = response.text
    assert "操作失败" in html
    assert "request_validation_failed" in html
    assert "规则查询" in html
