from __future__ import annotations

from fastapi.testclient import TestClient


def _session_count(client: TestClient) -> int:
    return len(client.app.state.session_service.list_sessions())


def test_playtest_session_create_page_displays_minimal_setup_form(
    client: TestClient,
) -> None:
    response = client.get("/playtest/sessions/create")

    assert response.status_code == 200
    html = response.text
    assert "创建新局" in html
    assert 'href="/playtest/knowledge"' in html
    assert "先看准备资料" in html
    assert 'action="/playtest/sessions/create"' in html
    assert 'name="keeper_name"' in html
    assert 'name="playtest_group"' in html
    assert "可用来标识同一轮测试、同一批 session 或同一主题实验。" in html
    assert 'type="radio"' in html
    assert 'name="scenario_template"' in html
    assert 'value="whispering_guesthouse"' in html
    assert 'value="midnight_archive"' in html
    assert 'value="blackout_clinic"' in html
    assert "雾港旅店的低语" in html
    assert "雨夜档案馆" in html
    assert "停电诊所" in html
    assert "偏封闭空间调查" in html
    assert "偏档案探索" in html
    assert "偏医疗异变" in html
    assert "当前选中模板" in html
    assert "当前选择：雾港旅店的低语" in html
    assert 'name="investigator_1_name"' in html
    assert 'name="investigator_4_name"' in html
    assert "创建成功后会直接进入 launcher。" in html


def test_playtest_session_setup_flow_creates_session_and_redirects_to_launcher(
    client: TestClient,
) -> None:
    session_count_before_create = _session_count(client)

    response = client.post(
        "/playtest/sessions/create",
        data={
            "keeper_name": "新局 KP",
            "playtest_group": "旅店线压力测试",
            "scenario_template": "midnight_archive",
            "investigator_1_name": "林舟",
            "investigator_2_name": "周岚",
            "investigator_3_name": "",
            "investigator_4_name": "",
        },
    )

    assert response.status_code == 200
    assert response.history
    assert response.history[0].status_code == 303
    assert response.url.path.startswith("/playtest/sessions/")
    assert response.url.path.endswith("/home")
    new_session_id = response.url.path.split("/")[3]
    html = response.text
    assert "Playtest 入口" in html
    assert f"session_id: <code>{new_session_id}</code>" in html
    assert "KP：新局 KP" in html
    assert "分组：旅店线压力测试" in html
    assert "雨夜档案馆" in html
    assert "林舟" in html
    assert "周岚" in html
    assert _session_count(client) == session_count_before_create + 1

    index_response = client.get("/playtest/sessions")
    assert index_response.status_code == 200
    assert new_session_id in index_response.text
    assert "旅店线压力测试" in index_response.text
    assert f'/playtest/sessions/{new_session_id}/home"' in index_response.text


def test_playtest_session_setup_flow_shows_error_without_creating_session_when_no_investigator_names(
    client: TestClient,
) -> None:
    session_count_before_create = _session_count(client)

    response = client.post(
        "/playtest/sessions/create",
        data={
            "keeper_name": "新局 KP",
            "scenario_template": "blackout_clinic",
            "investigator_1_name": "",
            "investigator_2_name": "",
            "investigator_3_name": "",
            "investigator_4_name": "",
        },
    )

    assert response.status_code == 400
    html = response.text
    assert "操作失败" in html
    assert "playtest_session_setup_invalid" in html
    assert "至少需要填写 1 名调查员。" in html
    assert 'name="keeper_name"' in html
    assert "新局 KP" in html
    assert "当前选择：停电诊所" in html
    assert _session_count(client) == session_count_before_create


def test_playtest_session_setup_flow_shows_structured_error_for_unknown_scenario_template(
    client: TestClient,
) -> None:
    session_count_before_create = _session_count(client)

    response = client.post(
        "/playtest/sessions/create",
        data={
            "keeper_name": "新局 KP",
            "playtest_group": "模板错误回归",
            "scenario_template": "missing-template",
            "investigator_1_name": "林舟",
            "investigator_2_name": "",
            "investigator_3_name": "",
            "investigator_4_name": "",
        },
    )

    assert response.status_code == 400
    html = response.text
    assert "操作失败" in html
    assert "playtest_session_setup_invalid" in html
    assert "未找到会话模板 missing-template" in html
    assert "模板错误回归" in html
    assert 'name="scenario_template"' in html
    assert _session_count(client) == session_count_before_create
