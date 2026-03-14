from __future__ import annotations

import json
from html import escape
from typing import Any, Callable
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from coc_runner.api.dependencies import get_session_service
from coc_runner.api.playtest_layout import render_playtest_shell
from coc_runner.application.session_service import SessionService
from coc_runner.domain.errors import ConflictError
from coc_runner.domain.models import (
    CreateCheckpointRequest,
    PlayerActionRequest,
    ReviewDraftRequest,
    RestoreCheckpointRequest,
    UpdateCheckpointRequest,
    UpdateKeeperPromptRequest,
    ViewerRole,
)
from coc_runner.error_details import (
    build_structured_error_detail,
    extract_error_detail,
    shape_validation_error_items,
)


router = APIRouter(prefix="/playtest", tags=["playtest"])


def _render_detail(detail: dict[str, Any] | str | None) -> str:
    if detail is None:
        return ""
    if isinstance(detail, str):
        return (
            '<section class="feedback feedback-error">'
            "<h2>操作失败</h2>"
            f"<p>{escape(detail)}</p>"
            "</section>"
        )

    lines = [f"<p>{escape(detail.get('message', '操作失败'))}</p>"]
    code = detail.get("code")
    if code:
        lines.append(f'<p class="feedback-code">code: {escape(str(code))}</p>')
    for error in detail.get("errors", []):
        message = error.get("message")
        if message:
            lines.append(f"<li>{escape(str(message))}</li>")
    extra_list = "".join(
        line for line in lines[2:]
    )
    return (
        '<section class="feedback feedback-error">'
        "<h2>操作失败</h2>"
        f"{lines[0]}"
        f"{lines[1] if len(lines) > 1 else ''}"
        f"{f'<ul>{extra_list}</ul>' if extra_list else ''}"
        "</section>"
    )


def _render_restore_result(restore_result: dict[str, Any] | None) -> str:
    if restore_result is None:
        return ""
    warnings = restore_result.get("warnings", [])
    warning_items = "".join(
        f"<li>{escape(str(warning.get('message', warning)))}</li>" for warning in warnings
    )
    warning_block = (
        '<div class="warning-box"><h3>恢复 warnings</h3><ul>'
        f"{warning_items}</ul></div>"
        if warning_items
        else ""
    )
    new_session_id = restore_result["new_session_id"]
    return (
        '<section class="feedback feedback-success">'
        "<h2>已从检查点恢复新会话</h2>"
        f"<p>new_session_id: <code>{escape(new_session_id)}</code></p>"
        f'<p><a href="/playtest/sessions/{escape(new_session_id)}">打开新会话</a></p>'
        f"{warning_block}"
        "</section>"
    )


def _render_checkpoint_import_result(import_result: dict[str, Any] | None) -> str:
    if import_result is None:
        return ""
    checkpoint = import_result.get("checkpoint", {})
    original_checkpoint_id = import_result.get("original_checkpoint_id")
    lines = [
        "<h2>检查点已导入</h2>",
        f"<p>new_checkpoint_id: <code>{escape(str(checkpoint.get('checkpoint_id', '')))}</code></p>",
    ]
    if original_checkpoint_id:
        lines.append(
            f"<p>original_checkpoint_id: <code>{escape(str(original_checkpoint_id))}</code></p>"
        )
    return (
        '<section class="feedback feedback-success">'
        + "".join(lines)
        + "</section>"
    )


def _render_launcher_link(session_id: str) -> str:
    return f'<a href="/playtest/sessions/{escape(session_id)}/home">返回 playtest 入口</a>'


def _render_playtest_launcher_page(
    *,
    session_id: str,
    session_snapshot: dict[str, Any] | None,
    detail: dict[str, Any] | str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    snapshot = session_snapshot or {}
    scenario = snapshot.get("scenario") or {}
    progress_state = snapshot.get("progress_state") or {}
    current_scene = snapshot.get("current_scene") or {}
    current_beat_id = progress_state.get("current_beat")
    beats_by_id = {
        str(beat.get("beat_id")): beat
        for beat in scenario.get("beats") or []
        if isinstance(beat, dict) and beat.get("beat_id")
    }
    current_beat_title = (
        (beats_by_id.get(str(current_beat_id)) or {}).get("title")
        if current_beat_id is not None
        else None
    )
    investigator_entries = [
        participant
        for participant in snapshot.get("participants") or []
        if isinstance(participant, dict)
        and participant.get("kind") == "human"
        and participant.get("actor_id") != snapshot.get("keeper_id")
    ]
    investigator_list = (
        "".join(
            f"""
            <article class="attention-card">
              <h3>{escape(str(participant.get("display_name") or participant.get("actor_id") or "调查员"))}</h3>
              <p class="meta-line">actor_id: <code>{escape(str(participant.get("actor_id", "")))}</code></p>
              <a class="action-link" href="/playtest/sessions/{escape(session_id)}/investigator/{escape(str(participant.get("actor_id", "")))}">打开调查员页面</a>
            </article>
            """
            for participant in investigator_entries
        )
        if investigator_entries
        else '<p class="empty-state">当前没有可进入的调查员页面。</p>'
    )

    body = f"""
      <section class="hero">
        <h1>Playtest 入口</h1>
        <div class="hero-meta">
          <span>session_id: <code>{escape(session_id)}</code></span>
          <span>场景：{escape(str(scenario.get('title', '未知会话')))}</span>
          <span>KP：{escape(str(snapshot.get('keeper_name', 'KP')))}</span>
          <span>keeper_id: <code>{escape(str(snapshot.get('keeper_id', '—')))}</code></span>
        </div>
      </section>
      {_render_detail(detail)}
      <section class="panel">
        <h2>会话摘要</h2>
        <div class="summary-grid">
          <article class="summary-card">
            <h3>当前进度</h3>
            <ul>
              <li>当前场景：{escape(str(current_scene.get('title', '未知场景')))}</li>
              <li>当前 beat：{escape(str(current_beat_id or '无'))}</li>
              <li>当前 beat 标题：{escape(str(current_beat_title or '无'))}</li>
              <li>状态版本：{escape(str(snapshot.get('state_version', '—')))}</li>
            </ul>
          </article>
          <article class="summary-card">
            <h3>使用说明</h3>
            <ul>
              <li>主持人处理推进与审阅，请进入主持人工作台。</li>
              <li>调查员只应打开自己的调查员页面。</li>
              <li>检查点页面用于存档、恢复与导出导入。</li>
            </ul>
          </article>
        </div>
      </section>
      <section class="panel">
        <h2>主要入口</h2>
        <div class="quick-actions">
          <a class="action-link" href="/playtest/sessions/{escape(session_id)}/keeper">打开主持人工作台</a>
          <a class="action-link" href="/playtest/sessions/{escape(session_id)}">打开检查点页面</a>
        </div>
      </section>
      <section class="panel">
        <h2>调查员入口</h2>
        <div class="attention-grid">
          {investigator_list}
        </div>
      </section>
    """
    return _render_shell(
        title=f"Session {session_id} Playtest Home",
        body=body,
        status_code=status_code,
    )


def _render_notice(notice: str | None) -> str:
    if not notice:
        return ""
    return (
        '<section class="feedback feedback-success">'
        f"<p>{escape(notice)}</p>"
        "</section>"
    )


def _render_shell(
    *,
    title: str,
    body: str,
    status_code: int = status.HTTP_200_OK,
    include_form_script: bool = False,
) -> HTMLResponse:
    return render_playtest_shell(
        title=title,
        body=body,
        status_code=status_code,
        include_form_script=include_form_script,
    )


def _format_datetime(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _render_checkpoint_list(checkpoints: list[dict[str, Any]], *, session_id: str, keeper_id: str) -> str:
    if not checkpoints:
        return '<p class="empty-state">还没有检查点。先创建一个用于回放或分支。</p>'

    items = []
    for checkpoint in checkpoints:
        checkpoint_id = escape(str(checkpoint["checkpoint_id"]))
        label = escape(str(checkpoint["label"]))
        note_value = checkpoint.get("note")
        note_display = (
            f"<p>{escape(str(note_value))}</p>"
            if note_value not in {None, ""}
            else '<p class="muted">未写备注</p>'
        )
        note_form_value = "" if note_value is None else escape(str(note_value))
        created_by = checkpoint.get("created_by") or "—"
        created_at = checkpoint["created_at"]
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat()
        item = f"""
        <article class="checkpoint-card" data-checkpoint-id="{checkpoint_id}">
          <header class="checkpoint-card-header">
            <div>
              <h3>{label}</h3>
              {note_display}
            </div>
            <div class="checkpoint-meta">
              <span>版本 {escape(str(checkpoint["source_session_version"]))}</span>
              <span>{escape(str(created_at))}</span>
              <span>创建者 {escape(str(created_by))}</span>
            </div>
          </header>
          <div class="checkpoint-actions">
            <form method="post" action="/playtest/sessions/{escape(session_id)}/checkpoints/{checkpoint_id}/update" data-submit-label="保存中...">
              <input type="hidden" name="operator_id" value="{escape(keeper_id)}" />
              <label>
                名称
                <input type="text" name="label" value="{label}" />
              </label>
              <label>
                备注
                <textarea name="note" rows="3">{note_form_value}</textarea>
              </label>
              <button type="submit">保存</button>
            </form>
            <div class="checkpoint-secondary-actions">
              <a class="action-link" href="/playtest/sessions/{escape(session_id)}/checkpoints/{checkpoint_id}/export">导出</a>
              <form method="post" action="/playtest/sessions/{escape(session_id)}/checkpoints/{checkpoint_id}/restore" data-submit-label="恢复中..." data-confirm="恢复会创建一个新的 session，不会覆盖当前 session。确定继续吗？">
                <button type="submit">恢复为新会话</button>
              </form>
              <form method="post" action="/playtest/sessions/{escape(session_id)}/checkpoints/{checkpoint_id}/delete" data-submit-label="删除中..." data-confirm="确认删除该检查点吗？">
                <button type="submit" class="danger">删除</button>
              </form>
            </div>
          </div>
        </article>
        """
        items.append(item)
    return "".join(items)


def _render_checkpoint_page(
    *,
    session_id: str,
    session_snapshot: dict[str, Any] | None,
    checkpoints: list[dict[str, Any]],
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
    restore_result: dict[str, Any] | None = None,
    import_result: dict[str, Any] | None = None,
    import_payload_text: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    snapshot = session_snapshot or {}
    has_live_session = session_snapshot is not None
    scenario_title = snapshot.get("scenario", {}).get("title", "检查点命名空间")
    current_scene = snapshot.get("current_scene", {}).get("title", "无本地会话")
    state_version = snapshot.get("state_version", "—")
    keeper_id = str(snapshot.get("keeper_id", "keeper-1"))
    keeper_name = snapshot.get("keeper_name", "KP")
    import_form_value = escape(import_payload_text or "")
    nav_links = (
        f"""
        <div class="nav-links">
          {_render_launcher_link(session_id)}
          <a href="/playtest/sessions/{escape(session_id)}/keeper">打开主持人工作台</a>
          <a href="/sessions/{escape(session_id)}/snapshot">查看 snapshot JSON</a>
          <a href="/sessions/{escape(session_id)}/export">查看 export JSON</a>
        </div>
        """
        if has_live_session
        else '<div class="nav-links"><span class="muted">当前页面只承载导入的 checkpoint 记录，没有对应的本地 source session。</span></div>'
    )
    create_panel = (
        f"""
      <section class="panel">
        <h2>创建检查点</h2>
        <p class="help">恢复检查点会创建一个新的 session，不会覆盖当前 session。</p>
        <form method="post" action="/playtest/sessions/{escape(session_id)}/checkpoints/create" data-submit-label="创建中...">
          <input type="hidden" name="operator_id" value="{escape(keeper_id)}" />
          <label>
            名称
            <input type="text" name="label" placeholder="例如：档案室前" required />
          </label>
          <label>
            备注
            <textarea name="note" rows="3" placeholder="可选。记录为什么保留这个分支点。"></textarea>
          </label>
          <button type="submit">创建检查点</button>
        </form>
      </section>
        """
        if has_live_session
        else ""
    )

    body = f"""
      <section class="hero">
        <h1>{escape(str(scenario_title))}</h1>
        <div class="hero-meta">
          <span>session_id: <code>{escape(session_id)}</code></span>
          <span>当前场景：{escape(str(current_scene))}</span>
          <span>版本：{escape(str(state_version))}</span>
          <span>KP：{escape(str(keeper_name))}</span>
        </div>
        {nav_links}
      </section>
      {_render_notice(notice)}
      {_render_detail(detail)}
      {_render_restore_result(restore_result)}
      {_render_checkpoint_import_result(import_result)}
      {create_panel}
      <section class="panel">
        <h2>导入检查点</h2>
        <p class="help">粘贴导出的 checkpoint JSON。导入后不会自动恢复 session。</p>
        <form method="post" action="/playtest/sessions/{escape(session_id)}/checkpoints/import" data-submit-label="导入中...">
          <label>
            Checkpoint JSON
            <textarea name="checkpoint_payload" rows="14" placeholder='{{"format_version":1,...}}' required>{import_form_value}</textarea>
          </label>
          <button type="submit">导入检查点</button>
        </form>
      </section>
      <section class="panel">
        <h2>检查点</h2>
        <div class="checkpoint-list">
          {_render_checkpoint_list(checkpoints, session_id=session_id, keeper_id=keeper_id)}
        </div>
      </section>
    """
    return _render_shell(
        title=f"Session {session_id} Checkpoints",
        body=body,
        status_code=status_code,
        include_form_script=True,
    )


def _render_warning_summary(warnings: list[dict[str, Any]]) -> str:
    if not warnings:
        return ""
    warning_items = "".join(
        f"<li>{escape(str(warning.get('message', '')))}</li>"
        for warning in warnings
        if warning.get("message")
    )
    return (
        '<section class="warning-box">'
        '<h3>当前环境缺少外部知识源</h3>'
        "<p>规则 grounding、角色来源追溯或后续再同步可能降级。</p>"
        f"{f'<ul>{warning_items}</ul>' if warning_items else ''}"
        "</section>"
    )


def _render_attention_block(
    *,
    title: str,
    items: list[str],
    empty_text: str,
) -> str:
    if not items:
        return (
            '<article class="attention-card">'
            f"<h3>{escape(title)}</h3>"
            f'<p class="empty-state">{escape(empty_text)}</p>'
            "</article>"
        )
    item_list = "".join(f"<li>{item}</li>" for item in items)
    return (
        '<article class="attention-card">'
        f"<h3>{escape(title)}</h3>"
        f"<ul>{item_list}</ul>"
        "</article>"
    )


def _render_prompt_attention_item(prompt: dict[str, Any]) -> str:
    prompt_id = str(prompt.get("prompt_id", "prompt"))
    prompt_text = escape(str(prompt.get("prompt_text", "未命名提示")))
    category = prompt.get("category")
    category_suffix = (
        f' <span class="meta-line">[{escape(str(category))}]</span>' if category else ""
    )
    return (
        f"{prompt_text}{category_suffix} "
        f'<a class="action-link" href="#prompt-{escape(prompt_id)}">处理此提示</a>'
    )


def _render_draft_attention_item(draft: dict[str, Any]) -> str:
    draft_id = str(draft.get("draft_id", "draft"))
    draft_text = escape(str(draft.get("draft_text", "未命名草稿")))
    pending_suffix = (
        ' <span class="meta-line">[待审]</span>'
        if draft.get("requires_explicit_approval")
        else ""
    )
    return (
        f"{draft_text}{pending_suffix} "
        f'<a class="action-link" href="#draft-{escape(draft_id)}">前往审阅</a>'
    )


def _render_prompt_jump_targets(
    prompts: list[dict[str, Any]],
    *,
    session_id: str,
    operator_id: str,
) -> str:
    if not prompts:
        return ""
    cards: list[str] = []
    for prompt in prompts[:3]:
        prompt_id = str(prompt.get("prompt_id", "prompt"))
        prompt_text = escape(str(prompt.get("prompt_text", "未命名提示")))
        category = prompt.get("category")
        status_value = prompt.get("status")
        trigger_reason = prompt.get("trigger_reason")
        beat_id = prompt.get("beat_id")
        scene_id = prompt.get("scene_id")
        notes = prompt.get("notes") or []
        notes_block = (
            "<ul>"
            + "".join(f"<li>{escape(str(note))}</li>" for note in notes)
            + "</ul>"
            if notes
            else '<p class="muted">当前还没有备注。</p>'
        )
        cards.append(
            f"""
            <article class="attention-card" id="prompt-{escape(prompt_id)}">
              <div class="activity-header">
                <h3>{prompt_text}</h3>
                <span class="activity-meta">{escape(str(status_value or "pending"))}</span>
              </div>
              <p class="meta-line">prompt_id: <span class="mono">{escape(prompt_id)}</span></p>
              {
                  f'<p class="meta-line">category: <span class="mono">{escape(str(category))}</span></p>'
                  if category
                  else ''
              }
              {
                  f'<p class="meta-line">scene_id: <span class="mono">{escape(str(scene_id))}</span></p>'
                  if scene_id
                  else ''
              }
              {
                  f'<p class="meta-line">beat_id: <span class="mono">{escape(str(beat_id))}</span></p>'
                  if beat_id
                  else ''
              }
              {
                  f'<p>{escape(str(trigger_reason))}</p>'
                  if trigger_reason
                  else '<p class="muted">该提示没有额外触发说明。</p>'
              }
              <div>
                <p class="meta-line">当前备注</p>
                {notes_block}
              </div>
              <p class="meta-line">
                处理入口：<code>/sessions/{escape(session_id)}/keeper-prompts/{escape(prompt_id)}/status</code>
              </p>
              <form method="post" action="/playtest/sessions/{escape(session_id)}/keeper/prompts/{escape(prompt_id)}/status#prompt-{escape(prompt_id)}" data-submit-label="提交中...">
                <input type="hidden" name="operator_id" value="{escape(operator_id)}" />
                <label>
                  备注（可选）
                  <textarea name="note" rows="2" placeholder="可选。顺手留一句处理说明。"></textarea>
                </label>
                <div class="checkpoint-secondary-actions">
                  <button type="submit" name="status" value="acknowledged">标记 acknowledged</button>
                  <button type="submit" name="status" value="completed">标记 completed</button>
                  <button type="submit" name="status" value="dismissed" class="danger">标记 dismissed</button>
                </div>
              </form>
            </article>
            """
        )
    return (
        '<section class="panel" id="prompt-targets">'
        "<h2>KP 提示处理入口</h2>"
        '<p class="help">以下定位块用于快速查看提示上下文；实际处理仍复用现有 keeper prompt API。</p>'
        f"<div class=\"attention-grid\">{''.join(cards)}</div>"
        "</section>"
    )


def _render_draft_jump_targets(
    drafts: list[dict[str, Any]],
    *,
    session_id: str,
    reviewer_id: str,
) -> str:
    if not drafts:
        return ""
    cards: list[str] = []
    for draft in drafts[:3]:
        draft_id = str(draft.get("draft_id", "draft"))
        draft_text = escape(str(draft.get("draft_text", "未命名草稿")))
        review_status = draft.get("review_status")
        risk_level = draft.get("risk_level")
        rationale_summary = draft.get("rationale_summary")
        requires_explicit_approval = bool(draft.get("requires_explicit_approval"))
        cards.append(
            f"""
            <article class="attention-card" id="draft-{escape(draft_id)}">
              <div class="activity-header">
                <h3>{draft_text}</h3>
                <span class="activity-meta">{escape(str(review_status or "pending"))}</span>
              </div>
              <p class="meta-line">draft_id: <span class="mono">{escape(draft_id)}</span></p>
              {
                  f'<p class="meta-line">risk_level: <span class="mono">{escape(str(risk_level))}</span></p>'
                  if risk_level
                  else ''
              }
              <p class="meta-line">
                requires_explicit_approval:
                <span class="mono">{escape(str(requires_explicit_approval).lower())}</span>
              </p>
              {
                  f'<p>{escape(str(rationale_summary))}</p>'
                  if rationale_summary
                  else '<p class="muted">该草稿没有额外的风险摘要。</p>'
              }
              <p class="meta-line">
                审阅入口：<code>/sessions/{escape(session_id)}/draft-actions/{escape(draft_id)}/review</code>
              </p>
              <form method="post" action="/playtest/sessions/{escape(session_id)}/draft-actions/{escape(draft_id)}/review#draft-{escape(draft_id)}" data-submit-label="提交中...">
                <input type="hidden" name="reviewer_id" value="{escape(reviewer_id)}" />
                <label>
                  editor_notes（可选）
                  <textarea name="editor_notes" rows="2" placeholder="可选。顺手留一句审阅说明。"></textarea>
                </label>
                <div class="checkpoint-secondary-actions">
                  <button type="submit" name="decision" value="approve">批准草稿</button>
                  <button type="submit" name="decision" value="reject" class="danger">驳回草稿</button>
                </div>
              </form>
            </article>
            """
        )
    return (
        '<section class="panel" id="draft-review-targets">'
        "<h2>待审草稿入口</h2>"
        '<p class="help">以下定位块用于快速回到待审草稿上下文；实际 approve / edit / reject 继续走现有 review API。</p>'
        f"<div class=\"attention-grid\">{''.join(cards)}</div>"
        "</section>"
    )


def _render_recent_activity(events: list[dict[str, Any]]) -> str:
    if not events:
        return '<p class="empty-state">最近还没有可见活动。</p>'
    items: list[str] = []
    for event in reversed(events[-6:]):
        event_type = event.get("event_type", "event")
        text = event.get("text", "无摘要")
        created_at = _format_datetime(event.get("created_at", ""))
        items.append(
            f"""
            <article class="activity-item">
              <div class="activity-header">
                <h3>{escape(str(text))}</h3>
                <span class="activity-meta">{escape(created_at)}</span>
              </div>
              <p class="meta-line">event_type: <span class="mono">{escape(str(event_type))}</span></p>
            </article>
            """
        )
    return "".join(items)


def _build_prompt_result_timestamp(prompt: dict[str, Any]) -> str:
    return str(
        prompt.get("completed_at")
        or prompt.get("dismissed_at")
        or prompt.get("acknowledged_at")
        or prompt.get("updated_at")
        or prompt.get("created_at")
        or ""
    )


def _build_rejected_draft_audit_map(session_snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rejected_details_by_draft: dict[str, dict[str, Any]] = {}
    for entry in reversed(session_snapshot.get("audit_log") or []):
        if entry.get("action") != "review_decision":
            continue
        details = entry.get("details") or {}
        if details.get("review_status") != "rejected":
            continue
        draft_id = details.get("draft_id") or entry.get("subject_id")
        if not draft_id or draft_id in rejected_details_by_draft:
            continue
        rejected_details_by_draft[str(draft_id)] = {
            "editor_notes": details.get("editor_notes"),
            "decision": details.get("decision"),
            "review_status": details.get("review_status"),
            "created_at": entry.get("created_at"),
        }
    return rejected_details_by_draft


def _render_recent_result_panel(
    *,
    session_snapshot: dict[str, Any],
    keeper_view: dict[str, Any],
) -> str:
    progress_state = keeper_view.get("progress_state") or {}
    prompt_results = [
        prompt
        for prompt in progress_state.get("queued_kp_prompts") or []
        if prompt.get("status") not in {None, "pending"}
    ]
    prompt_results.sort(key=_build_prompt_result_timestamp, reverse=True)

    visible_reviewed = sorted(
        keeper_view.get("visible_reviewed_actions") or [],
        key=lambda reviewed: str(reviewed.get("created_at") or ""),
        reverse=True,
    )
    authoritative_by_review_id = {
        str(action.get("review_id")): action
        for action in keeper_view.get("visible_authoritative_actions") or []
        if action.get("review_id")
    }
    rejected_audit_by_draft = _build_rejected_draft_audit_map(session_snapshot)
    rejected_outcomes: list[dict[str, Any]] = []
    for draft in keeper_view.get("visible_draft_actions") or []:
        if draft.get("review_status") != "rejected":
            continue
        draft_id = str(draft.get("draft_id"))
        audit_details = rejected_audit_by_draft.get(draft_id, {})
        rejected_outcomes.append(
            {
                "draft_id": draft_id,
                "draft_text": draft.get("draft_text", "未命名草稿"),
                "editor_notes": audit_details.get("editor_notes"),
                "decision": audit_details.get("decision") or "reject",
                "created_at": str(audit_details.get("created_at") or draft.get("created_at") or ""),
            }
        )
    rejected_outcomes.sort(key=lambda outcome: outcome["created_at"], reverse=True)

    if not prompt_results and not visible_reviewed and not rejected_outcomes:
        return (
            '<section class="panel" id="recent-results">'
            "<h2>最近处理结果</h2>"
            '<p class="empty-state">还没有最近处理结果。</p>'
            "</section>"
        )

    if prompt_results:
        prompt_items = []
        for prompt in prompt_results[:3]:
            note_lines = prompt.get("notes") or []
            note_display = " / ".join(escape(str(note)) for note in note_lines) if note_lines else "当前没有备注。"
            prompt_items.append(
                f"""
                <article class="activity-item">
                  <div class="activity-header">
                    <h3>{escape(str(prompt.get('prompt_text', '未命名提示')))}</h3>
                    <span class="activity-meta">{escape(_build_prompt_result_timestamp(prompt))}</span>
                  </div>
                  <p class="meta-line">结果：<span class="mono">{escape(str(prompt.get('status', 'pending')))}</span></p>
                  <p>{note_display}</p>
                </article>
                """
            )
        prompt_block = "".join(prompt_items)
    else:
        prompt_block = '<p class="empty-state">还没有已处理的提示。</p>'

    draft_cards: list[tuple[str, str]] = []
    for reviewed in visible_reviewed[:3]:
        review_id = str(reviewed.get("review_id", "review"))
        authoritative = authoritative_by_review_id.get(review_id)
        summary_text = (
            reviewed.get("execution_summary")
            or reviewed.get("review_summary")
            or (authoritative or {}).get("execution_summary")
            or (authoritative or {}).get("review_summary")
            or "已生成 reviewed / authoritative 结果。"
        )
        editor_notes = ((reviewed.get("decision") or {}).get("editor_notes")) or ""
        created_at = str(reviewed.get("created_at") or "")
        draft_cards.append(
            (
                created_at,
                f"""
                <article class="activity-item">
                  <div class="activity-header">
                    <h3>{escape(str(reviewed.get('final_text', '未命名落地结果')))}</h3>
                    <span class="activity-meta">{escape(created_at)}</span>
                  </div>
                  <p class="meta-line">
                    decision: <span class="mono">{escape(str((reviewed.get('decision') or {}).get('decision') or reviewed.get('review_status') or 'approved'))}</span>
                    · 已写入权威历史
                  </p>
                  <p>落地摘要：{escape(str(summary_text))}</p>
                  {
                      f'<p class="meta-line">审阅说明：{escape(str(editor_notes))}</p>'
                      if editor_notes
                      else ''
                  }
                </article>
                """,
            )
        )
    for rejected in rejected_outcomes[:3]:
        draft_cards.append(
            (
                rejected["created_at"],
                f"""
                <article class="activity-item">
                  <div class="activity-header">
                    <h3>{escape(str(rejected['draft_text']))}</h3>
                    <span class="activity-meta">{escape(str(rejected['created_at']))}</span>
                  </div>
                  <p class="meta-line">
                    decision: <span class="mono">{escape(str(rejected['decision']))}</span>
                    · 未写入权威历史
                  </p>
                  {
                      f'<p class="meta-line">审阅说明：{escape(str(rejected["editor_notes"]))}</p>'
                      if rejected.get("editor_notes")
                      else '<p class="muted">该驳回结果没有额外审阅说明。</p>'
                  }
                </article>
                """,
            )
        )
    draft_cards.sort(key=lambda item: item[0], reverse=True)
    draft_block = (
        "".join(card for _, card in draft_cards[:4])
        if draft_cards
        else '<p class="empty-state">还没有最近草稿结果。</p>'
    )

    return f"""
      <section class="panel" id="recent-results">
        <h2>最近处理结果</h2>
        <div class="summary-grid">
          <article class="summary-card">
            <h3>最近提示结果</h3>
            <div class="recent-list">{prompt_block}</div>
          </article>
          <article class="summary-card">
            <h3>最近草稿结果</h3>
            <div class="recent-list">{draft_block}</div>
          </article>
        </div>
      </section>
    """


def _render_checkpoint_summary(checkpoints: list[dict[str, Any]], *, session_id: str) -> str:
    if not checkpoints:
        summary_list = '<p class="empty-state">还没有检查点。先去创建一个用于回放或分支。</p>'
    else:
        items: list[str] = []
        for checkpoint in reversed(checkpoints[-3:]):
            note_value = checkpoint.get("note")
            note_display = escape(str(note_value)) if note_value not in {None, ""} else "未写备注"
            items.append(
                f"""
                <article class="checkpoint-summary-item">
                  <div class="checkpoint-summary-header">
                    <h3>{escape(str(checkpoint['label']))}</h3>
                    <span class="activity-meta">{escape(_format_datetime(checkpoint['created_at']))}</span>
                  </div>
                  <p>{note_display}</p>
                  <p class="meta-line">
                    版本 <span class="mono">{escape(str(checkpoint['source_session_version']))}</span>
                    · 创建者 {escape(str(checkpoint.get('created_by') or '—'))}
                  </p>
                </article>
                """
            )
        summary_list = "".join(items)
    return (
        '<section class="panel" id="checkpoints">'
        "<div class=\"checkpoint-summary-header\">"
        "<h2>最近检查点</h2>"
        f'<a class="action-link" href="/playtest/sessions/{escape(session_id)}">管理检查点</a>'
        "</div>"
        f'<div class="checkpoint-summary-list">{summary_list}</div>'
        "</section>"
    )


def _render_quick_actions(session_id: str) -> str:
    return f"""
      <section class="panel">
        <h2>快速入口</h2>
        <div class="quick-actions">
          <a class="action-link" href="/playtest/sessions/{escape(session_id)}">打开检查点页面</a>
          <a class="action-link" href="/sessions/{escape(session_id)}/snapshot">查看 snapshot JSON</a>
          <a class="action-link" href="/sessions/{escape(session_id)}/export">查看 export JSON</a>
          <a class="action-link" href="#attention">查看待处理项</a>
          <a class="action-link" href="#recent-activity">查看最近活动</a>
        </div>
      </section>
    """


def _render_keeper_dashboard_page(
    *,
    session_id: str,
    session_snapshot: dict[str, Any] | None,
    keeper_view: dict[str, Any] | None,
    checkpoints: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    snapshot = session_snapshot or {}
    current_view = keeper_view or {}
    progress_state = current_view.get("progress_state") or {}
    workflow = current_view.get("keeper_workflow") or {}
    summary = workflow.get("summary") or {}
    scenario = current_view.get("scenario") or {}
    current_scene = current_view.get("current_scene") or {}
    keeper_id = str(snapshot.get("keeper_id", current_view.get("keeper_id", "keeper-1")))
    beats = {beat["beat_id"]: beat for beat in scenario.get("beats", [])}
    current_beat_id = progress_state.get("current_beat")
    current_beat = beats.get(current_beat_id) if current_beat_id else None
    active_prompts = workflow.get("active_prompts") or []
    pending_drafts = [
        draft
        for draft in current_view.get("visible_draft_actions") or []
        if draft.get("review_status") == "pending"
    ]
    unresolved_objectives = workflow.get("unresolved_objectives") or []
    prompt_items = [_render_prompt_attention_item(prompt) for prompt in active_prompts[:3]]
    draft_items = [_render_draft_attention_item(draft) for draft in pending_drafts[:3]]
    objective_items = [
        escape(str(objective.get("text", objective.get("objective_id", "未命名目标"))))
        for objective in unresolved_objectives[:3]
    ]
    summary_lines = summary.get("summary_lines") or []
    summary_block = (
        "<ul>" + "".join(f"<li>{escape(str(line))}</li>" for line in summary_lines[:3]) + "</ul>"
        if summary_lines
        else '<p class="empty-state">当前没有额外的推进摘要。</p>'
    )
    body = f"""
      <section class="hero">
        <h1>主持人工作台</h1>
        <div class="hero-meta">
          <span>session_id: <code>{escape(session_id)}</code></span>
          <span>KP：{escape(str(snapshot.get('keeper_name', current_view.get('keeper_name', 'KP'))))}</span>
          <span>keeper_id: <code>{escape(str(snapshot.get('keeper_id', '—')))}</code></span>
          <span>当前场景：{escape(str(current_scene.get('title', '未知场景')))}</span>
          <span>状态版本：{escape(str(current_view.get('state_version', snapshot.get('state_version', '—'))))}</span>
          <span class="status-pill{' warn' if warnings else ''}">
            {escape('存在降级/外部来源告警' if warnings else '状态正常')}
          </span>
        </div>
        <div class="nav-links">
          {_render_launcher_link(session_id)}
          <a href="/playtest/sessions/{escape(session_id)}">返回检查点页面</a>
          <a href="/sessions/{escape(session_id)}/snapshot">snapshot JSON</a>
          <a href="/sessions/{escape(session_id)}/export">export JSON</a>
        </div>
      </section>
      {_render_notice(notice)}
      {_render_detail(detail)}
      {_render_warning_summary(warnings)}
      <section class="panel">
        <h2>会话摘要</h2>
        <div class="summary-grid">
          <article class="summary-card">
            <h3>当前推进</h3>
            <ul>
              <li>当前场景：{escape(str(current_scene.get('title', '未知场景')))}</li>
              <li>当前 beat：{escape(str(current_beat_id or '无'))}</li>
              <li>当前 beat 标题：{escape(str(current_beat.get('title') if current_beat else '无'))}</li>
            </ul>
          </article>
          <article class="summary-card">
            <h3>待处理概览</h3>
            <ul>
              <li>KP 提示：{escape(str(summary.get('active_prompt_count', len(active_prompts))))}</li>
              <li>未完成目标：{escape(str(summary.get('unresolved_objective_count', len(unresolved_objectives))))}</li>
              <li>待审草稿：{escape(str(len(pending_drafts)))}</li>
            </ul>
          </article>
          <article class="summary-card">
            <h3>最近状态摘要</h3>
            {summary_block}
          </article>
        </div>
      </section>
      <section class="panel" id="attention">
        <h2>待处理</h2>
        <div class="attention-grid">
          {_render_attention_block(title='KP 提示', items=prompt_items, empty_text='当前没有待处理的 KP 提示。')}
          {_render_attention_block(title='待审草稿', items=draft_items, empty_text='当前没有待审草稿。')}
          {_render_attention_block(title='未完成目标', items=objective_items, empty_text='当前没有未完成目标。')}
        </div>
      </section>
      {_render_recent_result_panel(session_snapshot=snapshot, keeper_view=current_view)}
      {_render_prompt_jump_targets(active_prompts, session_id=session_id, operator_id=keeper_id)}
      {_render_draft_jump_targets(pending_drafts, session_id=session_id, reviewer_id=keeper_id)}
      <section class="panel" id="recent-activity">
        <h2>最近活动</h2>
        <div class="recent-list">
          {_render_recent_activity(current_view.get('visible_events') or [])}
        </div>
      </section>
      {_render_checkpoint_summary(checkpoints, session_id=session_id)}
      {_render_quick_actions(session_id)}
    """
    return _render_shell(
        title=f"会话 {session_id} 主持人工作台",
        body=body,
        status_code=status_code,
        include_form_script=True,
    )


def _normalize_form_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip()


async def _read_form_payload(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    return {key: value for key, value in parse_qsl(body, keep_blank_values=True)}


def _build_validation_detail(exc: ValidationError) -> dict[str, Any]:
    return build_structured_error_detail(
        code="request_validation_failed",
        message="请求参数校验失败",
        scope="request_validation",
        errors=shape_validation_error_items(exc.errors()),
    )


def _playtest_status_for_exception(exc: Exception) -> int:
    if isinstance(exc, ValidationError):
        return status.HTTP_422_UNPROCESSABLE_ENTITY
    if isinstance(exc, LookupError):
        return status.HTTP_404_NOT_FOUND
    if isinstance(exc, PermissionError):
        return status.HTTP_403_FORBIDDEN
    if isinstance(exc, ConflictError):
        return status.HTTP_409_CONFLICT
    return status.HTTP_400_BAD_REQUEST


def _render_playtest_exception(
    render_page: Callable[..., HTMLResponse],
    *,
    exc: ValidationError | LookupError | PermissionError | ConflictError | ValueError,
    **render_kwargs: Any,
) -> HTMLResponse:
    detail = (
        _build_validation_detail(exc)
        if isinstance(exc, ValidationError)
        else extract_error_detail(exc)
    )
    return render_page(
        detail=detail,
        status_code=_playtest_status_for_exception(exc),
        **render_kwargs,
    )


def _load_page_context(service: SessionService, session_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    snapshot: dict[str, Any] | None = None
    snapshot_error: LookupError | None = None
    try:
        snapshot = service.snapshot_session(session_id)
    except LookupError as exc:
        snapshot_error = exc
    try:
        checkpoints = [
            checkpoint.model_dump(mode="json")
            for checkpoint in service.list_checkpoints(session_id).checkpoints
        ]
    except LookupError:
        if snapshot_error is not None:
            raise snapshot_error
        raise
    return snapshot, checkpoints


def _load_keeper_workspace_context(
    service: SessionService,
    session_id: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    session, keeper_view, checkpoints, warnings = service.get_keeper_workspace(session_id)
    return (
        session.model_dump(mode="json"),
        keeper_view.model_dump(mode="json"),
        [checkpoint.model_dump(mode="json") for checkpoint in checkpoints],
        [warning.model_dump(mode="json") for warning in warnings],
    )


def _render_checkpoint_page_from_service(
    *,
    service: SessionService,
    session_id: str,
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
    restore_result: dict[str, Any] | None = None,
    import_result: dict[str, Any] | None = None,
    import_payload_text: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    try:
        snapshot, checkpoints = _load_page_context(service, session_id)
    except LookupError as exc:
        fallback_detail = detail or extract_error_detail(exc)
        return _render_checkpoint_page(
            session_id=session_id,
            session_snapshot=None,
            checkpoints=[],
            notice=notice,
            detail=fallback_detail,
            restore_result=restore_result,
            import_result=import_result,
            import_payload_text=import_payload_text,
            status_code=status_code,
        )
    return _render_checkpoint_page(
        session_id=session_id,
        session_snapshot=snapshot,
        checkpoints=checkpoints,
        notice=notice,
        detail=detail,
        restore_result=restore_result,
        import_result=import_result,
        import_payload_text=import_payload_text,
        status_code=status_code,
    )


def _render_playtest_launcher_from_service(
    *,
    service: SessionService,
    session_id: str,
    detail: dict[str, Any] | str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    try:
        snapshot = service.snapshot_session(session_id)
    except LookupError as exc:
        return _render_playtest_launcher_page(
            session_id=session_id,
            session_snapshot=None,
            detail=detail or extract_error_detail(exc),
            status_code=(
                status_code
                if status_code != status.HTTP_200_OK
                else status.HTTP_404_NOT_FOUND
            ),
        )
    return _render_playtest_launcher_page(
        session_id=session_id,
        session_snapshot=snapshot,
        detail=detail,
        status_code=status_code,
    )


def _render_checkpoint_export_page(
    *,
    session_id: str,
    checkpoint_id: str,
    export_payload: dict[str, Any],
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    export_json = escape(json.dumps(export_payload, ensure_ascii=False, indent=2))
    body = f"""
      <section class="hero">
        <h1>导出检查点</h1>
        <div class="hero-meta">
          <span>session_id: <code>{escape(session_id)}</code></span>
          <span>checkpoint_id: <code>{escape(checkpoint_id)}</code></span>
        </div>
        <div class="nav-links">
          <a href="/playtest/sessions/{escape(session_id)}">返回检查点页面</a>
        </div>
      </section>
      <section class="panel">
        <h2>可复制的 checkpoint JSON</h2>
        <p class="help">直接复制下面内容即可在另一台环境导入。</p>
        <label>
          Export JSON
          <textarea rows="24" readonly>{export_json}</textarea>
        </label>
      </section>
    """
    return _render_shell(
        title=f"Checkpoint {checkpoint_id} Export",
        body=body,
        status_code=status_code,
    )


def _render_list_or_empty(
    items: list[str],
    *,
    empty_text: str,
) -> str:
    if not items:
        return f'<p class="empty-state">{escape(empty_text)}</p>'
    return "<ul>" + "".join(f"<li>{escape(str(item))}</li>" for item in items) + "</ul>"


def _render_investigator_action_result(action_result: dict[str, Any] | None) -> str:
    if action_result is None:
        return ""
    lines = [
        "<h2>最近一次提交结果</h2>",
        f"<p>{escape(str(action_result.get('message', '已提交行动。')))}</p>",
    ]
    authoritative_event = action_result.get("authoritative_event")
    draft_action = action_result.get("draft_action")
    if isinstance(authoritative_event, dict) and authoritative_event.get("text"):
        lines.append(
            f"<p>结果：{escape(str(authoritative_event['text']))}</p>"
        )
    elif isinstance(draft_action, dict) and draft_action.get("draft_text"):
        lines.append(
            f"<p>结果：{escape(str(draft_action['draft_text']))}</p>"
        )

    review_summary = None
    if isinstance(authoritative_event, dict):
        rules_grounding = authoritative_event.get("rules_grounding")
        if isinstance(rules_grounding, dict):
            review_summary = rules_grounding.get("review_summary")
    warning_block = ""
    if action_result.get("grounding_degraded"):
        warning_block = (
            '<div class="warning-box"><h3>规则依据降级</h3>'
            f"<p>{escape(str(review_summary or '当前环境缺少外部知识源，未命中可用规则依据。'))}</p>"
            "</div>"
        )
    return (
        '<section class="feedback feedback-success">'
        + "".join(lines)
        + warning_block
        + "</section>"
    )


def _render_investigator_recent_events(visible_events: list[dict[str, Any]]) -> str:
    recent_events = list(reversed(visible_events[-5:]))
    if not recent_events:
        return '<p class="empty-state">还没有你可见的近期事件。</p>'
    items: list[str] = []
    for event in recent_events:
        created_at = _format_datetime(event.get("created_at", ""))
        event_type = event.get("event_type", "event")
        text = event.get("text", "")
        items.append(
            f"""
            <article class="activity-item">
              <div class="activity-header">
                <h3>{escape(str(text))}</h3>
                <span class="activity-meta">{escape(str(created_at))}</span>
              </div>
              <p class="muted">event_type: {escape(str(event_type))}</p>
            </article>
            """
        )
    return "".join(items)


def _render_investigator_page(
    *,
    session_id: str,
    viewer_id: str,
    investigator_view: dict[str, Any] | None,
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
    action_result: dict[str, Any] | None = None,
    action_text: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    view = investigator_view or {}
    participants = view.get("participants", [])
    viewer_summary = next(
        (participant for participant in participants if participant.get("actor_id") == viewer_id),
        None,
    )
    viewer_name = (
        viewer_summary.get("display_name")
        if isinstance(viewer_summary, dict)
        else viewer_id
    ) or viewer_id
    own_character_state = view.get("own_character_state") or {}
    own_private_state = view.get("own_private_state") or {}
    visible_clues = [
        clue.get("title")
        for clue in view.get("scenario", {}).get("clues", [])
        if isinstance(clue, dict) and clue.get("title")
    ]
    scene_title = view.get("current_scene", {}).get("title", "未知场景")
    state_version = view.get("state_version", "—")
    action_form_value = escape(action_text or "")

    body = f"""
      <section class="hero">
        <h1>{escape(str(viewer_name))} 的调查页面</h1>
        <div class="hero-meta">
          <span>session_id: <code>{escape(session_id)}</code></span>
          <span>viewer_id: <code>{escape(viewer_id)}</code></span>
          <span>当前场景：{escape(str(scene_title))}</span>
          <span>版本：{escape(str(state_version))}</span>
        </div>
        <div class="nav-links">
          {_render_launcher_link(session_id)}
        </div>
      </section>
      {_render_notice(notice)}
      {_render_detail(detail)}
      {_render_investigator_action_result(action_result)}
      <section class="panel">
        <h2>我的摘要</h2>
        <div class="summary-grid">
          <article class="summary-card">
            <h3>当前状态</h3>
            <ul>
              <li>角色：{escape(str(viewer_name))}</li>
              <li>当前场景：{escape(str(scene_title))}</li>
              <li>HP：{escape(str(own_character_state.get('current_hit_points', '—')))}</li>
              <li>MP：{escape(str(own_character_state.get('current_magic_points', '—')))}</li>
              <li>SAN：{escape(str(own_character_state.get('current_sanity', '—')))}</li>
            </ul>
          </article>
          <article class="summary-card">
            <h3>可见线索</h3>
            {_render_list_or_empty(visible_clues, empty_text="当前还没有你可见的线索。")}
          </article>
          <article class="summary-card">
            <h3>状态与条件</h3>
            {_render_list_or_empty(
                list(own_character_state.get("status_effects", []))
                + list(own_character_state.get("temporary_conditions", [])),
                empty_text="当前没有可见的状态效果或临时条件。",
            )}
          </article>
          <article class="summary-card">
            <h3>我的备注</h3>
            {_render_list_or_empty(
                list(own_character_state.get("private_notes", []))
                + list(own_private_state.get("private_notes", [])),
                empty_text="当前没有你的私有备注。",
            )}
          </article>
        </div>
      </section>
      <section class="panel">
        <h2>提交玩家行动</h2>
        <form method="post" action="/playtest/sessions/{escape(session_id)}/investigator/{escape(viewer_id)}/actions" data-submit-label="提交中...">
          <label>
            action_text
            <textarea name="action_text" rows="4" placeholder="例如：我检查门缝后的低语来源。" required>{action_form_value}</textarea>
          </label>
          <button type="submit">提交行动</button>
        </form>
      </section>
      <section class="panel">
        <h2>最近可见事件</h2>
        <div class="recent-list">
          {_render_investigator_recent_events(list(view.get("visible_events", [])))}
        </div>
      </section>
    """
    return _render_shell(
        title=f"Session {session_id} Investigator {viewer_id}",
        body=body,
        status_code=status_code,
        include_form_script=True,
    )


def _render_keeper_dashboard_from_service(
    *,
    service: SessionService,
    session_id: str,
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    try:
        snapshot, keeper_view, checkpoints, warnings = _load_keeper_workspace_context(
            service,
            session_id,
        )
    except LookupError as exc:
        return _render_keeper_dashboard_page(
            session_id=session_id,
            session_snapshot=None,
            keeper_view=None,
            checkpoints=[],
            warnings=[],
            notice=notice,
            detail=detail or extract_error_detail(exc),
            status_code=(
                status_code
                if status_code != status.HTTP_200_OK
                else status.HTTP_404_NOT_FOUND
            ),
        )
    return _render_keeper_dashboard_page(
        session_id=session_id,
        session_snapshot=snapshot,
        keeper_view=keeper_view,
        checkpoints=checkpoints,
        warnings=warnings,
        notice=notice,
        detail=detail,
        status_code=status_code,
    )


def _load_investigator_page_context(
    service: SessionService,
    session_id: str,
    viewer_id: str,
) -> dict[str, Any]:
    return service.get_session_view(
        session_id,
        viewer_id=viewer_id,
        viewer_role=ViewerRole.INVESTIGATOR,
    ).model_dump(mode="json")


def _render_investigator_page_from_service(
    *,
    service: SessionService,
    session_id: str,
    viewer_id: str,
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
    action_result: dict[str, Any] | None = None,
    action_text: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    try:
        investigator_view = _load_investigator_page_context(service, session_id, viewer_id)
    except (LookupError, ValueError) as exc:
        fallback_detail = detail or extract_error_detail(exc)
        return _render_investigator_page(
            session_id=session_id,
            viewer_id=viewer_id,
            investigator_view=None,
            notice=notice,
            detail=fallback_detail,
            action_result=action_result,
            action_text=action_text,
            status_code=(
                status_code
                if status_code != status.HTTP_200_OK
                else (
                    status.HTTP_404_NOT_FOUND
                    if isinstance(exc, LookupError)
                    else status.HTTP_400_BAD_REQUEST
                )
            ),
        )
    return _render_investigator_page(
        session_id=session_id,
        viewer_id=viewer_id,
        investigator_view=investigator_view,
        notice=notice,
        detail=detail,
        action_result=action_result,
        action_text=action_text,
        status_code=status_code,
    )


@router.get("/sessions/{session_id}", response_class=HTMLResponse)
def session_checkpoint_page(
    session_id: str,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    return _render_checkpoint_page_from_service(service=service, session_id=session_id)


@router.get("/sessions/{session_id}/home", response_class=HTMLResponse)
def playtest_launcher_page(
    session_id: str,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    return _render_playtest_launcher_from_service(service=service, session_id=session_id)


@router.get("/sessions/{session_id}/keeper", response_class=HTMLResponse)
def keeper_dashboard_page(
    session_id: str,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    return _render_keeper_dashboard_from_service(service=service, session_id=session_id)


@router.get("/sessions/{session_id}/investigator/{viewer_id}", response_class=HTMLResponse)
def investigator_playtest_page(
    session_id: str,
    viewer_id: str,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    return _render_investigator_page_from_service(
        service=service,
        session_id=session_id,
        viewer_id=viewer_id,
    )


@router.post("/sessions/{session_id}/investigator/{viewer_id}/actions", response_class=HTMLResponse)
async def submit_player_action_via_investigator_ui(
    session_id: str,
    viewer_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    action_text = _normalize_form_text(form.get("action_text")) or ""
    try:
        response = service.submit_player_action(
            session_id,
            PlayerActionRequest(
                actor_id=viewer_id,
                action_text=action_text,
            ),
        )
        return _render_investigator_page_from_service(
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            notice=response.message,
            action_result=response.model_dump(mode="json"),
        )
    except (ValidationError, LookupError, ConflictError, ValueError) as exc:
        return _render_playtest_exception(
            _render_investigator_page_from_service,
            exc=exc,
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            action_text=action_text,
        )


@router.post("/sessions/{session_id}/keeper/prompts/{prompt_id}/status", response_class=HTMLResponse)
async def update_keeper_prompt_via_dashboard(
    session_id: str,
    prompt_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    note = _normalize_form_text(form.get("note")) or None
    try:
        response = service.update_keeper_prompt_status(
            session_id,
            prompt_id,
            UpdateKeeperPromptRequest(
                operator_id=form.get("operator_id", ""),
                status=form.get("status"),
                add_notes=[note] if note else [],
            ),
        )
        notice = response.message
        if note:
            notice = f"{notice} 备注：{note}"
        return _render_keeper_dashboard_from_service(
            service=service,
            session_id=session_id,
            notice=notice,
        )
    except (ValidationError, LookupError, PermissionError, ConflictError, ValueError) as exc:
        return _render_playtest_exception(
            _render_keeper_dashboard_from_service,
            exc=exc,
            service=service,
            session_id=session_id,
        )


@router.post("/sessions/{session_id}/draft-actions/{draft_id}/review", response_class=HTMLResponse)
async def review_draft_via_dashboard(
    session_id: str,
    draft_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    editor_notes = _normalize_form_text(form.get("editor_notes")) or None
    try:
        response = service.review_draft_action(
            session_id,
            draft_id,
            ReviewDraftRequest(
                reviewer_id=form.get("reviewer_id", ""),
                decision=form.get("decision"),
                editor_notes=editor_notes,
            ),
        )
        notice = response.message
        if editor_notes:
            notice = f"{notice} 审阅说明：{editor_notes}"
        if response.grounding_degraded:
            notice = f"{notice}（规则依据处于降级状态）"
        return _render_keeper_dashboard_from_service(
            service=service,
            session_id=session_id,
            notice=notice,
        )
    except (ValidationError, LookupError, PermissionError, ConflictError, ValueError) as exc:
        return _render_playtest_exception(
            _render_keeper_dashboard_from_service,
            exc=exc,
            service=service,
            session_id=session_id,
        )


@router.post("/sessions/{session_id}/checkpoints/create", response_class=HTMLResponse)
async def create_checkpoint_via_ui(
    session_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    try:
        service.create_checkpoint(
            session_id,
            CreateCheckpointRequest(
                label=form.get("label", ""),
                note=_normalize_form_text(form.get("note")) or None,
                operator_id=form.get("operator_id") or None,
            ),
        )
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            notice="检查点已创建",
        )
    except (ValidationError, LookupError, ConflictError, ValueError) as exc:
        return _render_playtest_exception(
            _render_checkpoint_page_from_service,
            exc=exc,
            service=service,
            session_id=session_id,
        )


@router.get("/sessions/{session_id}/checkpoints/{checkpoint_id}/export", response_class=HTMLResponse)
def export_checkpoint_via_ui(
    session_id: str,
    checkpoint_id: str,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    try:
        export_payload = service.export_checkpoint(session_id, checkpoint_id)
        return _render_checkpoint_export_page(
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            export_payload=export_payload.model_dump(mode="json"),
        )
    except (LookupError, ConflictError, ValueError) as exc:
        return _render_playtest_exception(
            _render_checkpoint_page_from_service,
            exc=exc,
            service=service,
            session_id=session_id,
        )


@router.post("/sessions/{session_id}/checkpoints/import", response_class=HTMLResponse)
async def import_checkpoint_via_ui(
    session_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    checkpoint_payload_text = form.get("checkpoint_payload", "")
    try:
        payload = json.loads(checkpoint_payload_text)
    except json.JSONDecodeError as exc:
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            detail=build_structured_error_detail(
                code="session_checkpoint_import_invalid_payload",
                message="检查点导入载荷校验失败",
                scope="session_checkpoint_import_payload",
                errors=[
                    {
                        "loc": ["body", "checkpoint_payload"],
                        "message": str(exc),
                        "type": "json_invalid",
                    }
                ],
            ),
            import_payload_text=checkpoint_payload_text,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        response = service.import_checkpoint(payload)
        imported_session_id = response.checkpoint.source_session_id
        notice = response.message
        if imported_session_id != session_id:
            notice = f"{notice}，已切换到来源会话命名空间 {imported_session_id}。"
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=imported_session_id,
            notice=notice,
            import_result=response.model_dump(mode="json"),
        )
    except (ValidationError, LookupError, ConflictError, ValueError) as exc:
        return _render_playtest_exception(
            _render_checkpoint_page_from_service,
            exc=exc,
            service=service,
            session_id=session_id,
            import_payload_text=checkpoint_payload_text,
        )


@router.post("/sessions/{session_id}/checkpoints/{checkpoint_id}/update", response_class=HTMLResponse)
async def update_checkpoint_via_ui(
    session_id: str,
    checkpoint_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    payload: dict[str, Any] = {}
    if "label" in form:
        payload["label"] = form.get("label")
    if "note" in form:
        payload["note"] = form.get("note")
    if "operator_id" in form:
        payload["operator_id"] = form.get("operator_id") or None
    try:
        service.update_checkpoint(
            session_id,
            checkpoint_id,
            UpdateCheckpointRequest(**payload),
        )
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            notice="检查点已更新",
        )
    except (ValidationError, LookupError, ConflictError, ValueError) as exc:
        return _render_playtest_exception(
            _render_checkpoint_page_from_service,
            exc=exc,
            service=service,
            session_id=session_id,
        )


@router.post("/sessions/{session_id}/checkpoints/{checkpoint_id}/delete", response_class=HTMLResponse)
def delete_checkpoint_via_ui(
    session_id: str,
    checkpoint_id: str,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    try:
        service.delete_checkpoint(session_id, checkpoint_id)
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            notice="检查点已删除",
        )
    except (LookupError, ConflictError, ValueError) as exc:
        return _render_playtest_exception(
            _render_checkpoint_page_from_service,
            exc=exc,
            service=service,
            session_id=session_id,
        )


@router.post("/sessions/{session_id}/checkpoints/{checkpoint_id}/restore", response_class=HTMLResponse)
def restore_checkpoint_via_ui(
    session_id: str,
    checkpoint_id: str,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    try:
        restore_response = service.restore_checkpoint(
            session_id,
            checkpoint_id,
            RestoreCheckpointRequest(),
        )
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            restore_result=restore_response.model_dump(mode="json"),
            status_code=status.HTTP_200_OK,
        )
    except (LookupError, ConflictError, ValueError) as exc:
        return _render_playtest_exception(
            _render_checkpoint_page_from_service,
            exc=exc,
            service=service,
            session_id=session_id,
        )
