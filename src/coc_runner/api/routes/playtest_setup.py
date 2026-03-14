from __future__ import annotations

from html import escape
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from coc_runner.api.dependencies import get_session_service
from coc_runner.api.routes.playtest_shared import (
    _normalize_form_text,
    _read_form_payload,
    _render_detail,
    _render_knowledge_index_link,
    _render_playtest_exception,
    _render_session_index_link,
    _render_shell,
)
from coc_runner.application.session_service import SessionService
from coc_runner.domain.scenario_examples import (
    blackout_clinic_payload,
    midnight_archive_payload,
    whispering_guesthouse_payload,
)
from coc_runner.domain.models import SessionStartRequest
from coc_runner.error_details import build_structured_error_detail


router = APIRouter()


def _playtest_scenario_templates() -> list[dict[str, Any]]:
    return [
        {
            "template_id": "whispering_guesthouse",
            "title": "雾港旅店的低语",
            "summary": "旅店老板封死地下储物间，调查员要在封闭空间里顺着低语与旧图纸找出真相。",
            "experience_hint": "偏封闭空间调查",
            "recommended_party": "推荐 1-2 名调查员",
            "builder": whispering_guesthouse_payload,
        },
        {
            "template_id": "midnight_archive",
            "title": "雨夜档案馆",
            "summary": "雨夜档案馆里散着烧焦便笺与借阅记录，适合沿文书与地下异常慢慢下探。",
            "experience_hint": "偏档案探索",
            "recommended_party": "推荐 1-3 名调查员",
            "builder": midnight_archive_payload,
        },
        {
            "template_id": "blackout_clinic",
            "title": "停电诊所",
            "summary": "停电诊所里的病历、封锁区与失控异变更强调压迫感与医疗现场推进。",
            "experience_hint": "偏医疗异变",
            "recommended_party": "推荐 2-4 名调查员",
            "builder": blackout_clinic_payload,
        },
    ]


def _get_playtest_scenario_template(template_id: str) -> dict[str, Any] | None:
    for template in _playtest_scenario_templates():
        if template["template_id"] == template_id:
            return template
    return None


def _default_playtest_setup_form_values() -> dict[str, Any]:
    return {
        "keeper_name": "",
        "playtest_group": "",
        "scenario_template": "whispering_guesthouse",
        "investigator_names": ["", "", "", ""],
    }


def _normalize_playtest_setup_form_values(form: dict[str, str]) -> dict[str, Any]:
    values = _default_playtest_setup_form_values()
    values["keeper_name"] = form.get("keeper_name", "")
    values["playtest_group"] = form.get("playtest_group", "")
    values["scenario_template"] = form.get(
        "scenario_template",
        values["scenario_template"],
    )
    values["investigator_names"] = [
        form.get(f"investigator_{index}_name", "")
        for index in range(1, 5)
    ]
    return values


def _build_playtest_setup_error_detail(
    *,
    message: str,
    scenario_template: str | None = None,
) -> dict[str, Any]:
    detail_kwargs: dict[str, Any] = {}
    if scenario_template:
        detail_kwargs["scenario_template"] = scenario_template
    return build_structured_error_detail(
        code="playtest_session_setup_invalid",
        message=message,
        scope="playtest_session_setup",
        **detail_kwargs,
    )


def _build_playtest_setup_character(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "occupation": "调查员",
        "age": 28,
        "language_preference": "zh-CN",
        "attributes": {
            "strength": 50,
            "constitution": 55,
            "size": 60,
            "dexterity": 65,
            "appearance": 45,
            "intelligence": 70,
            "power": 60,
            "education": 75,
        },
        "skills": {
            "图书馆使用": 70,
            "侦查": 60,
            "心理学": 50,
        },
    }


def _build_playtest_setup_request(form_values: dict[str, Any]) -> SessionStartRequest:
    scenario_template = str(form_values.get("scenario_template") or "whispering_guesthouse")
    template = _get_playtest_scenario_template(scenario_template)
    if template is None:
        raise ValueError(
            _build_playtest_setup_error_detail(
                message=f"未找到会话模板 {scenario_template}",
                scenario_template=scenario_template,
            )
        )
    investigator_names = [
        _normalize_form_text(str(value))
        for value in form_values.get("investigator_names") or []
    ]
    filtered_names = [name for name in investigator_names if name]
    if not filtered_names:
        raise ValueError(
            _build_playtest_setup_error_detail(
                message="至少需要填写 1 名调查员。",
                scenario_template=scenario_template,
            )
        )
    participants = [
        {
            "actor_id": f"investigator-{index}",
            "display_name": name,
            "kind": "human",
            "character": _build_playtest_setup_character(name),
        }
        for index, name in enumerate(filtered_names, start=1)
    ]
    return SessionStartRequest.model_validate(
        {
            "keeper_name": str(form_values.get("keeper_name") or ""),
            "playtest_group": _normalize_form_text(
                str(form_values.get("playtest_group") or "")
            ),
            "scenario": template["builder"](),
            "participants": participants,
        }
    )


def _render_playtest_session_create_page(
    *,
    form_values: dict[str, Any] | None = None,
    detail: dict[str, Any] | str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    values = form_values or _default_playtest_setup_form_values()
    selected_template = str(values.get("scenario_template") or "whispering_guesthouse")
    selected_template_meta = _get_playtest_scenario_template(
        selected_template
    ) or _playtest_scenario_templates()[0]
    template_cards = "".join(
        f"""
        <label class="attention-card">
          <input
            type="radio"
            name="scenario_template"
            value="{escape(str(template["template_id"]))}"
            {"checked" if template["template_id"] == selected_template else ""}
          />
          <h3>{escape(str(template["title"]))}</h3>
          <p>{escape(str(template["summary"]))}</p>
          <p class="meta-line">
            {escape(str(template["experience_hint"]))}
            · {escape(str(template["recommended_party"]))}
          </p>
          {
              '<p class="meta-line"><strong>当前选择</strong></p>'
              if template["template_id"] == selected_template
              else ''
          }
        </label>
        """
        for template in _playtest_scenario_templates()
    )
    investigator_inputs = "".join(
        f"""
        <label>
          调查员 {index}
          <input
            type="text"
            name="investigator_{index}_name"
            value="{escape(str((values.get('investigator_names') or ["", "", "", ""])[index - 1] or ""))}"
            {"required" if index == 1 else ""}
          />
        </label>
        """
        for index in range(1, 5)
    )
    body = f"""
      <section class="hero">
        <h1>创建新局</h1>
        <div class="hero-meta">
          <span>复用内置剧本模板创建 session，不提供 scenario 原始 JSON 编辑。</span>
        </div>
        <div class="nav-links">
          {_render_session_index_link()}
          {_render_knowledge_index_link("先看准备资料")}
        </div>
      </section>
      {_render_detail(detail)}
      <section class="panel">
        <h2>最小 setup</h2>
        <form method="post" action="/playtest/sessions/create" data-submit-label="创建中...">
          <label>
            keeper_name
            <input type="text" name="keeper_name" value="{escape(str(values.get('keeper_name') or ''))}" required />
          </label>
          <label>
            playtest_group（可选）
            <input type="text" name="playtest_group" value="{escape(str(values.get('playtest_group') or ''))}" placeholder="例如：旅店线压力测试" />
          </label>
          <p class="help">可用来标识同一轮测试、同一批 session 或同一主题实验。</p>
          <fieldset>
            <legend>scenario_template</legend>
            <div class="attention-grid">
              {template_cards}
            </div>
          </fieldset>
          <article class="summary-card">
            <h3>当前选中模板</h3>
            <p><strong>当前选择：{escape(str(selected_template_meta["title"]))}</strong></p>
            <p>{escape(str(selected_template_meta["summary"]))}</p>
            <p class="meta-line">
              {escape(str(selected_template_meta["experience_hint"]))}
              · {escape(str(selected_template_meta["recommended_party"]))}
            </p>
          </article>
          <div class="summary-grid">
            {investigator_inputs}
          </div>
          <p class="help">至少填写 1 名调查员。创建成功后会直接进入 launcher。</p>
          <button type="submit">创建新局</button>
        </form>
      </section>
    """
    return _render_shell(
        title="创建新局",
        body=body,
        status_code=status_code,
        include_form_script=True,
    )


@router.get("/sessions/create", response_class=HTMLResponse)
async def view_playtest_session_create_page(
    playtest_group: str | None = None,
) -> HTMLResponse:
    form_values = _default_playtest_setup_form_values()
    normalized_group = _normalize_form_text(playtest_group)
    if normalized_group:
        form_values["playtest_group"] = normalized_group
    return _render_playtest_session_create_page(form_values=form_values)


@router.post("/sessions/create", response_class=HTMLResponse)
async def create_playtest_session(
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = _normalize_playtest_setup_form_values(await _read_form_payload(request))
    try:
        start_request = _build_playtest_setup_request(form)
        response = service.start_session(start_request)
        session_id = response.session_id
        return RedirectResponse(
            url=f"/playtest/sessions/{session_id}/home",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except (ValidationError, ValueError) as exc:
        return _render_playtest_exception(
            _render_playtest_session_create_page,
            exc=exc,
            form_values=form,
        )
