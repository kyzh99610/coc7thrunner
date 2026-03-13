from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from coc_runner.api.dependencies import get_session_service
from coc_runner.application.session_service import SessionService
from coc_runner.domain.errors import ConflictError
from coc_runner.domain.models import (
    CreateCheckpointRequest,
    RestoreCheckpointRequest,
    UpdateCheckpointRequest,
)
from coc_runner.error_details import (
    build_structured_error_detail,
    extract_error_detail,
    shape_validation_error_items,
)


router = APIRouter(prefix="/playtest", tags=["playtest"])

_PLAYTEST_PAGE_STYLES = """
:root {
  color-scheme: light;
  --bg: #f2efe7;
  --card: #fffdf8;
  --ink: #2b2118;
  --muted: #6b5b4d;
  --line: #d7cab8;
  --accent: #6c4f3d;
  --danger: #8b2f2f;
  --success: #245c3d;
  --warn: #7a5b11;
}
body {
  margin: 0;
  font-family: "Microsoft YaHei UI", "Noto Sans SC", sans-serif;
  background: linear-gradient(180deg, #efe7d6 0%, var(--bg) 100%);
  color: var(--ink);
}
main {
  max-width: 1040px;
  margin: 0 auto;
  padding: 32px 20px 48px;
}
.hero, .panel, .checkpoint-card, .feedback, .attention-card, .activity-item, .checkpoint-summary-item {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 16px;
  box-shadow: 0 10px 30px rgba(43, 33, 24, 0.06);
}
.hero, .panel, .feedback {
  padding: 20px;
  margin-bottom: 18px;
}
.hero h1 {
  margin: 0 0 8px;
  font-size: 28px;
}
.hero-meta, .nav-links, .quick-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 18px;
  color: var(--muted);
}
.nav-links {
  margin-top: 14px;
}
.panel h2, .panel h3 {
  margin-top: 0;
}
form {
  display: grid;
  gap: 12px;
}
label {
  display: grid;
  gap: 6px;
  font-size: 14px;
}
input, textarea, button {
  font: inherit;
}
input, textarea {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 10px 12px;
  background: #fff;
}
button, .action-link {
  border: none;
  border-radius: 999px;
  padding: 10px 14px;
  background: var(--accent);
  color: #fff;
  cursor: pointer;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
button.danger, .action-link.danger {
  background: var(--danger);
}
button:disabled {
  opacity: 0.6;
  cursor: wait;
}
.checkpoint-list, .attention-grid, .recent-list, .checkpoint-summary-list {
  display: grid;
  gap: 14px;
}
.dashboard-grid {
  display: grid;
  gap: 18px;
}
.summary-grid {
  display: grid;
  gap: 14px;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}
.summary-card {
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.75);
}
.summary-card h3 {
  margin: 0 0 10px;
  font-size: 15px;
}
.summary-card ul, .recent-list ul, .attention-card ul, .warning-box ul {
  margin: 0;
  padding-left: 18px;
}
.summary-card li, .recent-list li, .attention-card li, .warning-box li {
  margin-bottom: 6px;
}
.checkpoint-card, .attention-card, .activity-item, .checkpoint-summary-item {
  padding: 18px;
}
.checkpoint-card-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.checkpoint-card-header h3 {
  margin: 0 0 6px;
}
.checkpoint-meta {
  display: grid;
  gap: 6px;
  min-width: 180px;
  color: var(--muted);
  font-size: 13px;
  text-align: right;
}
.checkpoint-actions {
  display: grid;
  gap: 14px;
}
.checkpoint-secondary-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.feedback-success {
  border-color: rgba(36, 92, 61, 0.25);
}
.feedback-error {
  border-color: rgba(139, 47, 47, 0.25);
}
.feedback-code, .muted, .empty-state, .help, .meta-line, .activity-meta {
  color: var(--muted);
}
.warning-box {
  margin-top: 14px;
  padding: 14px;
  border-radius: 12px;
  background: rgba(122, 91, 17, 0.08);
  color: var(--warn);
}
.warning-box h3, .warning-box p {
  margin-top: 0;
}
.status-pill {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(108, 79, 61, 0.12);
  color: var(--accent);
  font-size: 13px;
}
.status-pill.warn {
  background: rgba(122, 91, 17, 0.12);
  color: var(--warn);
}
.activity-item h3, .attention-card h3, .checkpoint-summary-item h3 {
  margin: 0 0 8px;
  font-size: 16px;
}
.checkpoint-summary-item p, .attention-card p, .activity-item p {
  margin: 0 0 8px;
}
.checkpoint-summary-header, .activity-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: baseline;
}
code, .mono {
  background: rgba(43, 33, 24, 0.08);
  padding: 2px 6px;
  border-radius: 6px;
}
a {
  color: var(--accent);
}
@media (max-width: 720px) {
  .checkpoint-card-header, .checkpoint-summary-header, .activity-header {
    display: grid;
  }
  .checkpoint-meta {
    text-align: left;
    min-width: 0;
  }
}
"""

_PLAYTEST_FORM_SCRIPT = """
document.querySelectorAll("form[data-submit-label]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    const confirmMessage = form.dataset.confirm;
    if (confirmMessage && !window.confirm(confirmMessage)) {
      event.preventDefault();
      return;
    }
    const button = form.querySelector("button[type='submit']");
    if (!button) {
      return;
    }
    button.disabled = true;
    button.dataset.originalText = button.textContent;
    button.textContent = form.dataset.submitLabel || "处理中...";
  });
});
"""


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
    script = f"<script>{_PLAYTEST_FORM_SCRIPT}</script>" if include_form_script else ""
    html = f"""
    <!doctype html>
    <html lang="zh-CN">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>{escape(title)}</title>
        <style>{_PLAYTEST_PAGE_STYLES}</style>
      </head>
      <body>
        <main>{body}</main>
        {script}
      </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=status_code)


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
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    snapshot = session_snapshot or {}
    scenario_title = snapshot.get("scenario", {}).get("title", "未知会话")
    current_scene = snapshot.get("current_scene", {}).get("title", "未知场景")
    state_version = snapshot.get("state_version", "—")
    keeper_id = str(snapshot.get("keeper_id", "keeper-1"))
    keeper_name = snapshot.get("keeper_name", "KP")

    body = f"""
      <section class="hero">
        <h1>{escape(str(scenario_title))}</h1>
        <div class="hero-meta">
          <span>session_id: <code>{escape(session_id)}</code></span>
          <span>当前场景：{escape(str(current_scene))}</span>
          <span>版本：{escape(str(state_version))}</span>
          <span>KP：{escape(str(keeper_name))}</span>
        </div>
        <div class="nav-links">
          <a href="/playtest/sessions/{escape(session_id)}/keeper">打开主持人工作台</a>
          <a href="/sessions/{escape(session_id)}/snapshot">查看 snapshot JSON</a>
          <a href="/sessions/{escape(session_id)}/export">查看 export JSON</a>
        </div>
      </section>
      {_render_notice(notice)}
      {_render_detail(detail)}
      {_render_restore_result(restore_result)}
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
              <p class="meta-line">
                处理入口：<code>/sessions/{escape(session_id)}/keeper-prompts/{escape(prompt_id)}/status</code>
              </p>
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
          <a href="/playtest/sessions/{escape(session_id)}">返回检查点页面</a>
          <a href="/sessions/{escape(session_id)}/snapshot">snapshot JSON</a>
          <a href="/sessions/{escape(session_id)}/export">export JSON</a>
        </div>
      </section>
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
      {_render_prompt_jump_targets(active_prompts, session_id=session_id)}
      {_render_draft_jump_targets(pending_drafts, session_id=session_id)}
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


def _load_page_context(service: SessionService, session_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    snapshot = service.snapshot_session(session_id)
    checkpoints = [
        checkpoint.model_dump(mode="json")
        for checkpoint in service.list_checkpoints(session_id).checkpoints
    ]
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
            status_code=status_code,
        )
    return _render_checkpoint_page(
        session_id=session_id,
        session_snapshot=snapshot,
        checkpoints=checkpoints,
        notice=notice,
        detail=detail,
        restore_result=restore_result,
        status_code=status_code,
    )


def _render_keeper_dashboard_from_service(
    *,
    service: SessionService,
    session_id: str,
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
        detail=detail,
        status_code=status_code,
    )


@router.get("/sessions/{session_id}", response_class=HTMLResponse)
def session_checkpoint_page(
    session_id: str,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    return _render_checkpoint_page_from_service(service=service, session_id=session_id)


@router.get("/sessions/{session_id}/keeper", response_class=HTMLResponse)
def keeper_dashboard_page(
    session_id: str,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    return _render_keeper_dashboard_from_service(service=service, session_id=session_id)


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
    except ValidationError as exc:
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            detail=_build_validation_detail(exc),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except LookupError as exc:
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except ConflictError as exc:
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_409_CONFLICT,
        )
    except ValueError as exc:
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
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
    except ValidationError as exc:
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            detail=_build_validation_detail(exc),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except LookupError as exc:
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except ConflictError as exc:
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_409_CONFLICT,
        )
    except ValueError as exc:
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
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
    except LookupError as exc:
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except ConflictError as exc:
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_409_CONFLICT,
        )
    except ValueError as exc:
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
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
    except LookupError as exc:
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except ConflictError as exc:
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_409_CONFLICT,
        )
    except ValueError as exc:
        return _render_checkpoint_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
