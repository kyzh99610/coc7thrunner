from __future__ import annotations

import json
from html import escape
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from coc_runner.api.dependencies import get_knowledge_service, get_session_service
from coc_runner.api.routes.playtest_knowledge import router as playtest_knowledge_router
from coc_runner.api.routes.playtest_setup import router as playtest_setup_router
from coc_runner.api.routes.playtest_shared import (
    _build_validation_detail,
    _format_datetime,
    _normalize_form_text,
    _playtest_status_for_exception,
    _read_form_payload,
    _render_detail,
    _render_knowledge_index_link,
    _render_playtest_exception,
    _render_session_create_link,
    _render_session_index_link,
    _render_shell,
)
from coc_runner.application.knowledge_service import KnowledgeService
from coc_runner.application.session_service import SessionService
from coc_runner.domain.errors import ConflictError
from coc_runner.domain.models import (
    AuditActionType,
    CreateCheckpointRequest,
    EventType,
    InvestigatorAttributeCheckRequest,
    InvestigatorSanCheckRequest,
    InvestigatorSkillCheckRequest,
    KeeperLiveControlRequest,
    PlayerActionRequest,
    ReviewDraftRequest,
    RestoreCheckpointRequest,
    SessionStatus,
    UpdateSessionLifecycleRequest,
    UpdateCheckpointRequest,
    UpdateKeeperPromptRequest,
    ViewerRole,
)
from coc_runner.error_details import (
    build_structured_error_detail,
    extract_error_detail,
)
from knowledge.schemas import RuleQueryRequest


router = APIRouter(prefix="/playtest", tags=["playtest"])
router.include_router(playtest_knowledge_router)
router.include_router(playtest_setup_router)


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


def _render_recap_link(session_id: str) -> str:
    return f'<a href="/playtest/sessions/{escape(session_id)}/recap">查看会话回顾</a>'


def _session_status_label(status_value: Any) -> str:
    normalized = str(status_value or SessionStatus.PLANNED.value)
    return {
        SessionStatus.PLANNED.value: "计划中",
        SessionStatus.ACTIVE.value: "进行中",
        SessionStatus.PAUSED.value: "已暂停",
        SessionStatus.COMPLETED.value: "已完成",
    }.get(normalized, normalized)


def _playtest_group_label(group_value: Any, *, empty_label: str = "未分组") -> str:
    normalized = str(group_value or "").strip()
    return normalized or empty_label


def _render_session_status_display(status_value: Any) -> str:
    normalized = str(status_value or SessionStatus.PLANNED.value)
    return (
        f'{escape(_session_status_label(normalized))} '
        f'<span class="mono">{escape(normalized)}</span>'
    )


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
    session_status = snapshot.get("status") or SessionStatus.PLANNED.value
    playtest_group = snapshot.get("playtest_group")
    group_link = (
        f'<a href="/playtest/groups/{quote(str(playtest_group))}">返回本组</a>'
        if playtest_group
        else ""
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
          {
              f'<span>分组：{escape(_playtest_group_label(playtest_group))}</span>'
              if playtest_group
              else ''
          }
          <span>当前状态：{_render_session_status_display(session_status)}</span>
        </div>
        <div class="nav-links">
          {_render_session_index_link()}
          {group_link}
        </div>
      </section>
      {_render_detail(detail)}
      {
          '<section class="feedback feedback-success"><h2>该局已完成</h2><p>可进入主持人工作台查看最小收尾摘要，或前往检查点页面继续导出/分支，也可查看会话回顾。</p></section>'
          if session_status == SessionStatus.COMPLETED.value
          else ''
      }
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
              <li>当前状态：{escape(_session_status_label(session_status))} <span class="mono">{escape(str(session_status))}</span></li>
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
          {_render_recap_link(session_id).replace('<a ', '<a class="action-link" ')}
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


def _build_recap_event_entries(session_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    control_labels = {
        "session_lifecycle": "Session 状态",
        "resolve_objective": "Objective 完成",
        "reopen_objective": "Objective 重新打开",
        "reveal_clue": "Reveal 线索",
        "reveal_scene": "Reveal 场景",
        "advance_beat": "Beat 推进",
    }
    event_labels = {
        EventType.SESSION_STARTED.value: "会话启动",
        EventType.PLAYER_ACTION.value: "玩家行动",
        EventType.REVIEWED_ACTION.value: "已审动作",
        EventType.IMPORT.value: "导入",
        EventType.ROLLBACK.value: "回滚",
    }
    for event in session_snapshot.get("timeline") or []:
        event_type = str(event.get("event_type") or "")
        summary = str(event.get("text") or "").strip()
        if not summary:
            continue
        if event_type == EventType.MANUAL_ACTION.value:
            payload = event.get("structured_payload") or {}
            category = control_labels.get(
                str(payload.get("control_type") or ""),
                "主持人控场",
            )
        else:
            category = event_labels.get(event_type)
        if not category:
            continue
        entries.append(
            {
                "created_at": _format_datetime(event.get("created_at")),
                "category": category,
                "summary": summary,
                "meta_lines": [],
            }
        )
    return entries


def _build_recap_audit_entries(session_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for audit_entry in session_snapshot.get("audit_log") or []:
        action = str(audit_entry.get("action") or "")
        details = audit_entry.get("details") or {}
        subject_id = audit_entry.get("subject_id")
        category: str | None = None
        summary: str | None = None
        meta_lines: list[str] = []

        if action == AuditActionType.KEEPER_PROMPT_UPDATED.value:
            category = "KP 提示处理"
            if details.get("status"):
                summary = f"KP 提示已更新为 {details['status']}"
            elif details.get("reason") == "scene_changed":
                affected = len(details.get("affected_prompt_ids") or [])
                summary = f"场景切换后已自动收起旧提示（{affected}）"
            elif details.get("reason") == "beat_expired":
                affected = len(details.get("affected_prompt_ids") or [])
                summary = f"Beat 过期后已自动收起旧提示（{affected}）"
            else:
                summary = "KP 提示已更新"
            if subject_id:
                meta_lines.append(f"prompt_id: {subject_id}")
        elif action == AuditActionType.REVIEW_DECISION.value:
            category = "草稿审阅"
            review_status = str(details.get("review_status") or "")
            summary = {
                "approved": "草稿已批准并写入权威历史",
                "edited": "草稿经编辑后已写入权威历史",
                "rejected": "草稿已拒绝，未写入权威历史",
                "regenerated": "草稿已要求重新生成",
            }.get(review_status, "草稿审阅已完成")
            draft_id = details.get("draft_id")
            if draft_id:
                meta_lines.append(f"draft_id: {draft_id}")
            decision = details.get("decision")
            if decision:
                meta_lines.append(f"decision: {decision}")
        elif action == AuditActionType.DRAFT_CREATED.value:
            category = "草稿创建"
            summary = "已生成待审草稿"
            if subject_id:
                meta_lines.append(f"draft_id: {subject_id}")

        if not category or not summary:
            continue
        entries.append(
            {
                "created_at": _format_datetime(audit_entry.get("created_at")),
                "category": category,
                "summary": summary,
                "meta_lines": meta_lines,
            }
        )
    return entries


def _render_recap_timeline(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return '<p class="empty-state">当前还没有可回顾的关键事件。</p>'

    ordered_entries = sorted(
        entries,
        key=lambda entry: str(entry.get("created_at") or ""),
        reverse=True,
    )
    items: list[str] = []
    for entry in ordered_entries[:18]:
        meta_lines = "".join(
            f'<p class="meta-line">{escape(str(line))}</p>'
            for line in entry.get("meta_lines") or []
        )
        items.append(
            f"""
            <article class="attention-card">
              <div class="activity-header">
                <h3>{escape(str(entry.get("category") or "关键事件"))}</h3>
                <span class="activity-meta">{escape(str(entry.get("created_at") or "—"))}</span>
              </div>
              <p>{escape(str(entry.get("summary") or ""))}</p>
              {meta_lines}
            </article>
            """
        )
    return "".join(items)


def _render_playtest_recap_page(
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
    session_status = snapshot.get("status") or SessionStatus.PLANNED.value
    playtest_group = snapshot.get("playtest_group")
    timeline_entries = _render_recap_timeline(
        [
            *_build_recap_event_entries(snapshot),
            *_build_recap_audit_entries(snapshot),
        ]
    )

    body = f"""
      <section class="hero">
        <h1>会话回顾</h1>
        <div class="hero-meta">
          <span>session_id: <code>{escape(session_id)}</code></span>
          <span>KP：{escape(str(snapshot.get('keeper_name', 'KP')))}</span>
          <span>当前状态：{_render_session_status_display(session_status)}</span>
          {
              f'<span>分组：{escape(_playtest_group_label(playtest_group))}</span>'
              if playtest_group
              else ''
          }
        </div>
        <div class="nav-links">
          {_render_session_index_link()}
          {_render_launcher_link(session_id)}
        </div>
      </section>
      {_render_detail(detail)}
      {
          '<section class="feedback feedback-success"><h2>该局已完成</h2><p>这是当前 session 的最小回顾页面，用于快速回看关键推进与管理动作。</p></section>'
          if session_status == SessionStatus.COMPLETED.value
          else ''
      }
      <section class="panel">
        <h2>会话摘要</h2>
        <div class="summary-grid">
          <article class="summary-card">
            <h3>当前状态</h3>
            <ul>
              <li>session_id：<code>{escape(session_id)}</code></li>
              <li>keeper_name：{escape(str(snapshot.get('keeper_name', 'KP')))}</li>
              <li>status：{escape(_session_status_label(session_status))} <span class="mono">{escape(str(session_status))}</span></li>
              <li>当前场景：{escape(str(current_scene.get('title', '未知场景')))}</li>
              <li>当前 beat：{escape(str(current_beat_id or '无'))}</li>
              <li>当前 beat 标题：{escape(str(current_beat_title or '无'))}</li>
              {
                  f'<li>分组：{escape(_playtest_group_label(playtest_group))}</li>'
                  if playtest_group
                  else ''
              }
            </ul>
          </article>
          <article class="summary-card">
            <h3>查看说明</h3>
            <ul>
              <li>时间线按最近事件在前展示。</li>
              <li>这里只串起关键推进与管理动作，不展开原始 payload。</li>
              <li>若当前局仍未 completed，也可把这里当作当前回顾页使用。</li>
            </ul>
          </article>
        </div>
      </section>
      <section class="panel">
        <h2>时间线</h2>
        <div class="attention-grid">
          {timeline_entries}
        </div>
      </section>
    """
    return _render_shell(
        title=f"Session {session_id} Recap",
        body=body,
        status_code=status_code,
    )


def _render_playtest_session_index_page(
    *,
    sessions: list[dict[str, Any]],
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    if not sessions:
        session_cards = '<p class="empty-state">当前还没有 session。先创建一局，再从这里进入。</p>'
    else:
        grouped_cards: dict[str, list[str]] = {}
        group_order: list[str] = []
        for session in sessions:
            scenario = session.get("scenario") or {}
            progress_state = session.get("progress_state") or {}
            current_scene = session.get("current_scene") or {}
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
            session_id = str(session.get("session_id") or "")
            group_label = _playtest_group_label(session.get("playtest_group"))
            if group_label not in grouped_cards:
                grouped_cards[group_label] = []
                group_order.append(group_label)
            grouped_cards[group_label].append(
                f"""
                <article class="attention-card">
                  <div class="activity-header">
                    <h3>{escape(str(scenario.get('title', '未命名会话')))}</h3>
                    <span class="activity-meta">{_render_session_status_display(session.get('status'))}</span>
                  </div>
                  <p class="meta-line">session_id: <code>{escape(session_id)}</code></p>
                  <p class="meta-line">分组：{escape(group_label)}</p>
                  <p class="meta-line">KP：{escape(str(session.get('keeper_name') or 'KP'))}</p>
                  <p class="meta-line">当前场景：{escape(str(current_scene.get('title', '未知场景')))}</p>
                  <p class="meta-line">当前 beat：<span class="mono">{escape(str(current_beat_id or '无'))}</span></p>
                  <p class="meta-line">当前 beat 标题：{escape(str(current_beat_title or '无'))}</p>
                  <div class="quick-actions">
                    <a class="action-link" href="/playtest/sessions/{escape(session_id)}/home">打开 launcher</a>
                    <a class="action-link" href="/playtest/sessions/{escape(session_id)}/keeper">打开 keeper 页面</a>
                    <a class="action-link" href="/playtest/sessions/{escape(session_id)}">打开 checkpoint 页面</a>
                  </div>
                </article>
                """
            )
        session_cards = "".join(
            f"""
            <section class="panel">
              <h3>
                分组：
                {
                    f'<a href="/playtest/groups/{quote(group_label)}">{escape(group_label)}</a>'
                    if group_label != "未分组"
                    else escape(group_label)
                }
              </h3>
              <p class="meta-line">本组 session 数：<span class="mono">{escape(str(len(grouped_cards[group_label])))}</span></p>
              <div class="attention-grid">
                {"".join(grouped_cards[group_label])}
              </div>
            </section>
            """
            for group_label in group_order
        )
    body = f"""
      <section class="hero">
        <h1>Playtest Sessions</h1>
        <div class="hero-meta">
          <span>当前已发现会话数：{escape(str(len(sessions)))}</span>
          <span>可从这里进入已有 session，或创建一局新的 playtest。</span>
        </div>
        <div class="nav-links">
          {_render_session_create_link()}
          {_render_knowledge_index_link()}
        </div>
      </section>
      <section class="panel">
        <h2>Session 列表</h2>
        <div class="attention-grid">
          {session_cards}
        </div>
      </section>
    """
    return _render_shell(
        title="Playtest Sessions",
        body=body,
        status_code=status_code,
    )


def _render_playtest_group_page(
    *,
    group_name: str,
    sessions: list[dict[str, Any]],
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    normalized_group = _playtest_group_label(group_name, empty_label="")
    create_href = (
        "/playtest/sessions/create"
        if not normalized_group
        else f'/playtest/sessions/create?{escape(urlencode({"playtest_group": normalized_group}))}'
    )
    if not sessions:
        session_cards = '<p class="empty-state">当前分组下还没有 session。</p>'
    else:
        cards: list[str] = []
        for session in sessions:
            scenario = session.get("scenario") or {}
            progress_state = session.get("progress_state") or {}
            current_scene = session.get("current_scene") or {}
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
            session_id = str(session.get("session_id") or "")
            cards.append(
                f"""
                <article class="attention-card">
                  <div class="activity-header">
                    <h3>{escape(str(scenario.get('title', '未命名会话')))}</h3>
                    <span class="activity-meta">{_render_session_status_display(session.get('status'))}</span>
                  </div>
                  <p class="meta-line">session_id: <code>{escape(session_id)}</code></p>
                  <p class="meta-line">KP：{escape(str(session.get('keeper_name') or 'KP'))}</p>
                  <p class="meta-line">当前场景：{escape(str(current_scene.get('title', '未知场景')))}</p>
                  <p class="meta-line">当前 beat：<span class="mono">{escape(str(current_beat_id or '无'))}</span></p>
                  <p class="meta-line">当前 beat 标题：{escape(str(current_beat_title or '无'))}</p>
                  <div class="quick-actions">
                    <a class="action-link" href="/playtest/sessions/{escape(session_id)}/home">打开 launcher</a>
                    <a class="action-link" href="/playtest/sessions/{escape(session_id)}/keeper">打开 keeper 页面</a>
                    <a class="action-link" href="/playtest/sessions/{escape(session_id)}">打开 checkpoint 页面</a>
                  </div>
                </article>
                """
            )
        session_cards = "".join(cards)
    body = f"""
      <section class="hero">
        <h1>分组：{escape(normalized_group or group_name)}</h1>
        <div class="hero-meta">
          <span>本组 session 数：{escape(str(len(sessions)))}</span>
          <span>用来浏览同一轮测试、同一批 session 或同一主题实验下的相关局。</span>
        </div>
        <div class="nav-links">
          {_render_session_index_link()}
          <a href="{create_href}">在本组继续开局</a>
        </div>
      </section>
      <section class="panel">
        <h2>本组 session</h2>
        <div class="attention-grid">
          {session_cards}
        </div>
      </section>
    """
    return _render_shell(
        title=f"分组 {normalized_group or group_name}",
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
          {_render_session_index_link()}
          {_render_launcher_link(session_id)}
          <a href="/playtest/sessions/{escape(session_id)}/keeper">打开主持人工作台</a>
          <a href="/sessions/{escape(session_id)}/snapshot">查看 snapshot JSON</a>
          <a href="/sessions/{escape(session_id)}/export">查看 export JSON</a>
        </div>
        """
        if has_live_session
        else f'<div class="nav-links">{_render_session_index_link()}<span class="muted">当前页面只承载导入的 checkpoint 记录，没有对应的本地 source session。</span></div>'
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


def _render_keeper_prompt_status_label(status_value: Any) -> str:
    return {
        "pending": "待处理",
        "acknowledged": "已确认",
        "dismissed": "已忽略",
        "completed": "已完成",
    }.get(str(status_value or "pending"), str(status_value or "pending"))


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


def _render_session_lifecycle_panel(
    *,
    session_snapshot: dict[str, Any],
    keeper_view: dict[str, Any],
    checkpoints: list[dict[str, Any]],
    session_id: str,
    operator_id: str,
) -> str:
    status_value = str(session_snapshot.get("status") or SessionStatus.PLANNED.value)
    progress_state = keeper_view.get("progress_state") or {}
    workflow = keeper_view.get("keeper_workflow") or {}
    summary = workflow.get("summary") or {}
    current_scene = keeper_view.get("current_scene") or {}
    current_beat_id = progress_state.get("current_beat")
    completed_objective_count = len(progress_state.get("completed_objective_history") or [])
    unresolved_objective_count = int(
        summary.get("unresolved_objective_count")
        or len(workflow.get("unresolved_objectives") or [])
    )
    investigator_count = sum(
        1
        for participant in session_snapshot.get("participants") or []
        if isinstance(participant, dict)
        and participant.get("kind") == "human"
        and participant.get("actor_id") != session_snapshot.get("keeper_id")
    )
    allowed_transitions = {
        SessionStatus.PLANNED.value: [(SessionStatus.ACTIVE.value, "切换到 active")],
        SessionStatus.ACTIVE.value: [
            (SessionStatus.PAUSED.value, "切换到 paused"),
            (SessionStatus.COMPLETED.value, "标记为 completed"),
        ],
        SessionStatus.PAUSED.value: [
            (SessionStatus.ACTIVE.value, "恢复为 active"),
            (SessionStatus.COMPLETED.value, "标记为 completed"),
        ],
        SessionStatus.COMPLETED.value: [],
    }
    action_buttons = "".join(
        f"""
        <button type="submit" name="target_status" value="{escape(target_status)}">{escape(label)}</button>
        """
        for target_status, label in allowed_transitions.get(status_value, [])
    )
    lifecycle_form = (
        f"""
        <form method="post" action="/playtest/sessions/{escape(session_id)}/keeper/lifecycle#lifecycle-control" data-submit-label="提交中...">
          <input type="hidden" name="operator_id" value="{escape(operator_id)}" />
          <div class="checkpoint-secondary-actions">
            {action_buttons}
          </div>
        </form>
        """
        if action_buttons
        else '<p class="empty-state">当前状态没有额外可切换的下一个生命周期状态。</p>'
    )
    closeout_block = (
        f"""
        <article class="summary-card">
          <h3>本局收尾摘要</h3>
          <ul>
            <li>当前场景：{escape(str(current_scene.get('title', '未知场景')))}</li>
            <li>当前 beat：{escape(str(current_beat_id or '无'))}</li>
            <li>已完成目标：{escape(str(completed_objective_count))}</li>
            <li>未完成目标：{escape(str(unresolved_objective_count))}</li>
            <li>检查点数量：{escape(str(len(checkpoints)))}</li>
            <li>调查员数量：{escape(str(investigator_count))}</li>
          </ul>
        </article>
        """
        if status_value == SessionStatus.COMPLETED.value
        else ""
    )
    return f"""
      <section class="panel" id="lifecycle-control">
        <h2>会话生命周期</h2>
        <div class="summary-grid">
          <article class="summary-card">
            <h3>当前状态</h3>
            <p class="meta-line">当前状态：{_render_session_status_display(status_value)}</p>
            <p class="help">只提供 planned / active / paused / completed 的最小切换，不改变现有主链写能力。</p>
            {lifecycle_form}
          </article>
          {closeout_block}
        </div>
      </section>
    """


def _render_keeper_live_control_panel(
    *,
    keeper_view: dict[str, Any],
    session_id: str,
    operator_id: str,
) -> str:
    workflow = keeper_view.get("keeper_workflow") or {}
    summary = workflow.get("summary") or {}
    unresolved_objectives = workflow.get("unresolved_objectives") or []
    recent_completed_raw = summary.get("recently_completed_objectives") or []
    seen_completed_ids: set[str] = set()
    recent_completed: list[dict[str, Any]] = []
    for objective in recent_completed_raw:
        objective_id = str(objective.get("objective_id") or "")
        if not objective_id or objective_id in seen_completed_ids:
            continue
        seen_completed_ids.add(objective_id)
        recent_completed.append(objective)
    scenario = keeper_view.get("scenario") or {}
    revealable_scenes = [
        scene for scene in scenario.get("scenes") or [] if isinstance(scene, dict) and not scene.get("revealed")
    ]
    revealable_clues = [
        clue
        for clue in scenario.get("clues") or []
        if isinstance(clue, dict) and clue.get("status") != "shared_with_party"
    ]

    unresolved_block = (
        "".join(
            f"""
            <article class="activity-item" id="objective-control-{escape(str(objective.get('objective_id', '')))}">
              <div class="activity-header">
                <h3>{escape(str(objective.get('text', objective.get('objective_id', '未命名目标'))))}</h3>
                <span class="activity-meta">{escape(str(objective.get('scene_id') or objective.get('beat_id') or 'objective'))}</span>
              </div>
              <p class="meta-line">objective_id: <span class="mono">{escape(str(objective.get('objective_id', '')))}</span></p>
              <form method="post" action="/playtest/sessions/{escape(session_id)}/keeper/objectives/{escape(str(objective.get('objective_id', '')))}/complete#live-control" data-submit-label="提交中...">
                <input type="hidden" name="operator_id" value="{escape(operator_id)}" />
                <button type="submit">标记完成</button>
              </form>
            </article>
            """
            for objective in unresolved_objectives[:4]
        )
        if unresolved_objectives
        else '<p class="empty-state">当前没有可手动推进的未完成目标。</p>'
    )

    completed_block = (
        "".join(
            f"""
            <article class="activity-item" id="objective-control-{escape(str(objective.get('objective_id', '')))}">
              <div class="activity-header">
                <h3>{escape(str(objective.get('text', objective.get('objective_id', '未命名目标'))))}</h3>
                <span class="activity-meta">{escape(str(objective.get('completed_at') or 'recent'))}</span>
              </div>
              <p class="meta-line">objective_id: <span class="mono">{escape(str(objective.get('objective_id', '')))}</span></p>
              <form method="post" action="/playtest/sessions/{escape(session_id)}/keeper/objectives/{escape(str(objective.get('objective_id', '')))}/reopen#live-control" data-submit-label="提交中...">
                <input type="hidden" name="operator_id" value="{escape(operator_id)}" />
                <button type="submit" class="danger">取消完成</button>
              </form>
            </article>
            """
            for objective in recent_completed[:4]
        )
        if recent_completed
        else '<p class="empty-state">当前没有最近完成、可回退的目标。</p>'
    )

    scene_block = (
        "".join(
            f"""
            <article class="activity-item">
              <div class="activity-header">
                <h3>{escape(str(scene.get('title', scene.get('scene_id', '未命名场景'))))}</h3>
                <span class="activity-meta">{escape(str(scene.get('phase', 'scene')))}</span>
              </div>
              <p class="meta-line">scene_id: <span class="mono">{escape(str(scene.get('scene_id', '')))}</span></p>
              <form method="post" action="/playtest/sessions/{escape(session_id)}/keeper/reveal/scenes/{escape(str(scene.get('scene_id', '')))}#live-control" data-submit-label="提交中...">
                <input type="hidden" name="operator_id" value="{escape(operator_id)}" />
                <button type="submit">公开场景</button>
              </form>
            </article>
            """
            for scene in revealable_scenes[:4]
        )
        if revealable_scenes
        else '<p class="empty-state">当前没有待公开的场景。</p>'
    )

    clue_block = (
        "".join(
            f"""
            <article class="activity-item">
              <div class="activity-header">
                <h3>{escape(str(clue.get('title', clue.get('clue_id', '未命名线索'))))}</h3>
                <span class="activity-meta">{escape(str(clue.get('status', 'undiscovered')))}</span>
              </div>
              <p class="meta-line">clue_id: <span class="mono">{escape(str(clue.get('clue_id', '')))}</span></p>
              <form method="post" action="/playtest/sessions/{escape(session_id)}/keeper/reveal/clues/{escape(str(clue.get('clue_id', '')))}#live-control" data-submit-label="提交中...">
                <input type="hidden" name="operator_id" value="{escape(operator_id)}" />
                <button type="submit">公开线索</button>
              </form>
            </article>
            """
            for clue in revealable_clues[:5]
        )
        if revealable_clues
        else '<p class="empty-state">当前没有待公开的线索。</p>'
    )

    current_beat_id = str((keeper_view.get("progress_state") or {}).get("current_beat") or "")
    current_scene = keeper_view.get("current_scene") or {}
    beats_by_id = {
        str(beat.get("beat_id")): beat
        for beat in scenario.get("beats") or []
        if isinstance(beat, dict) and beat.get("beat_id")
    }
    current_beat = beats_by_id.get(current_beat_id)
    next_beat_candidates: list[dict[str, Any]] = []
    if current_beat is not None:
        for next_beat_id in current_beat.get("next_beats") or []:
            candidate = beats_by_id.get(str(next_beat_id))
            if candidate is None:
                continue
            candidate_status = str(candidate.get("status") or "locked")
            if candidate_status in {"blocked", "completed", "current"}:
                continue
            next_beat_candidates.append(candidate)
    beat_block = (
        f"""
        <article class="summary-card" id="beat-progression">
          <h3>Beat 推进</h3>
          <div class="activity-item" id="beat-progression-current-{escape(current_beat_id)}">
            <div class="activity-header">
              <h4>{escape(str(current_beat.get('title') if current_beat else '无'))}</h4>
              <span class="activity-meta">{escape(str(current_scene.get('title', '未知场景')))}</span>
            </div>
            <p class="meta-line">当前 beat：<span class="mono">{escape(current_beat_id or '无')}</span></p>
          </div>
          <h4>合法下一步</h4>
          <div class="recent-list">
            {
                ''.join(
                    f'''
                    <article class="activity-item" id="beat-progression-option-{escape(str(candidate.get("beat_id", "")))}">
                      <div class="activity-header">
                        <h4>{escape(str(candidate.get('title', candidate.get('beat_id', '未命名剧情节点'))))}</h4>
                        <span class="activity-meta">{escape(str(candidate.get('status', 'locked')))}</span>
                      </div>
                      <p class="meta-line">beat_id: <span class="mono">{escape(str(candidate.get('beat_id', '')))}</span></p>
                      <form method="post" action="/playtest/sessions/{escape(session_id)}/keeper/beats/{escape(str(candidate.get('beat_id', '')))}/advance#beat-progression" data-submit-label="提交中...">
                        <input type="hidden" name="operator_id" value="{escape(operator_id)}" />
                        <button type="submit">推进到此 beat</button>
                      </form>
                    </article>
                    '''
                    for candidate in next_beat_candidates
                )
                if next_beat_candidates
                else '<p class="empty-state">当前 beat 没有可手动推进的合法下一节点。</p>'
            }
          </div>
        </article>
        """
        if current_beat is not None
        else """
        <article class="summary-card" id="beat-progression">
          <h3>Beat 推进</h3>
          <p class="empty-state">当前没有正在推进的 beat。</p>
        </article>
        """
    )

    return f"""
      <section class="panel" id="live-control">
        <h2>实时控场</h2>
        <div class="summary-grid">
          <article class="summary-card" id="objective-control">
            <h3>目标控制</h3>
            <div class="recent-list">{unresolved_block}</div>
            <h3>最近完成目标</h3>
            <div class="recent-list">{completed_block}</div>
          </article>
          <article class="summary-card" id="reveal-control">
            <h3>Reveal 控制</h3>
            <h4>待公开场景</h4>
            <div class="recent-list">{scene_block}</div>
            <h4>待公开线索</h4>
            <div class="recent-list">{clue_block}</div>
          </article>
          {beat_block}
        </div>
      </section>
    """


def _render_san_aftermath_panel(prompts: list[dict[str, Any]]) -> str:
    san_prompts = [
        prompt for prompt in prompts if str(prompt.get("category") or "") == "san_aftermath"
    ]
    if not san_prompts:
        items = '<p class="empty-state">当前没有待裁定的理智后续。</p>'
    else:
        cards: list[str] = []
        for prompt in san_prompts[:4]:
            prompt_id = str(prompt.get("prompt_id") or "prompt")
            notes = prompt.get("notes") or []
            notes_block = (
                f'<p class="meta-line">备注：{escape(str(notes[-1]))}</p>' if notes else ""
            )
            cards.append(
                f"""
                <article class="activity-item">
                  <div class="activity-header">
                    <h3>{escape(str(prompt.get('prompt_text') or '未命名理智后续'))}</h3>
                    <span class="activity-meta">{escape(_render_keeper_prompt_status_label(prompt.get('status')))}</span>
                  </div>
                  <p>{escape(str(prompt.get('trigger_reason') or '当前没有额外的理智变化说明。'))}</p>
                  <p class="meta-line">状态：{escape(_render_keeper_prompt_status_label(prompt.get('status')))}</p>
                  {notes_block}
                  <p class="meta-line"><a class="action-link" href="#prompt-{escape(prompt_id)}">处理此理智后续</a></p>
                </article>
                """
            )
        items = "".join(cards)
    return (
        '<section class="panel" id="san-aftermath">'
        "<h2>理智后续待裁定</h2>"
        f'<div class="recent-list">{items}</div>'
        "</section>"
    )


def _resolve_live_control_jump_target(payload: dict[str, Any]) -> tuple[str, str] | None:
    control_type = payload.get("control_type")
    if control_type == "session_lifecycle":
        return "#lifecycle-control", "回到 lifecycle 控制"
    if control_type in {"objective_complete", "objective_reopen"}:
        objective_id = payload.get("objective_id")
        if objective_id:
            return f"#objective-control-{objective_id}", "回到 objective 控制"
        return "#objective-control", "回到 objective 控制"
    if control_type == "advance_beat":
        target_beat_id = payload.get("target_beat_id")
        if target_beat_id:
            return f"#beat-progression-current-{target_beat_id}", "回到 beat 推进"
        return "#beat-progression", "回到 beat 推进"
    if control_type in {"reveal_clue", "reveal_scene"}:
        return "#reveal-control", "回到 reveal 控制"
    return None


def _render_live_control_jump_link(payload: dict[str, Any]) -> str:
    jump_target = _resolve_live_control_jump_target(payload)
    if jump_target is None:
        return ""
    href, label = jump_target
    return f'<a class="action-link" href="{escape(href)}">{escape(label)}</a>'


def _render_live_control_type_label(payload: dict[str, Any]) -> str:
    control_type = payload.get("control_type")
    label = {
        "session_lifecycle": "Session 状态",
        "objective_complete": "Objective 已完成",
        "objective_reopen": "Objective 已恢复未完成",
        "reveal_clue": "Reveal 线索",
        "reveal_scene": "Reveal 场景",
        "advance_beat": "Beat 推进",
    }.get(str(control_type))
    if label is None:
        return ""
    return f'控场类型：<span class="mono">{escape(label)}</span>'


def _render_recent_activity(events: list[dict[str, Any]]) -> str:
    if not events:
        return '<p class="empty-state">最近还没有可见活动。</p>'
    items: list[str] = []
    for event in reversed(events[-6:]):
        event_type = event.get("event_type", "event")
        text = event.get("text", "无摘要")
        created_at = _format_datetime(event.get("created_at", ""))
        payload = event.get("structured_payload") or {}
        jump_link = _render_live_control_jump_link(payload) if isinstance(payload, dict) else ""
        type_label = _render_live_control_type_label(payload) if isinstance(payload, dict) else ""
        items.append(
            f"""
            <article class="activity-item">
              <div class="activity-header">
                <h3>{escape(str(text))}</h3>
                <span class="activity-meta">{escape(created_at)}</span>
              </div>
              {f'<p class="meta-line">{type_label}</p>' if type_label else ''}
              <p class="meta-line">event_type: <span class="mono">{escape(str(event_type))}</span></p>
              {f'<p class="meta-line">{jump_link}</p>' if jump_link else ''}
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
    live_control_events = [
        event
        for event in reversed((keeper_view.get("visible_events") or [])[-10:])
        if isinstance(event.get("structured_payload"), dict)
        and (event.get("structured_payload") or {}).get("control_type")
        in {
            "session_lifecycle",
            "objective_complete",
            "objective_reopen",
            "advance_beat",
            "reveal_clue",
            "reveal_scene",
        }
    ]

    if not prompt_results and not visible_reviewed and not rejected_outcomes and not live_control_events:
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
    live_control_cards: list[str] = []
    for event in live_control_events[:4]:
        payload = event.get("structured_payload") or {}
        jump_link = _render_live_control_jump_link(payload) if isinstance(payload, dict) else ""
        type_label = _render_live_control_type_label(payload) if isinstance(payload, dict) else ""
        live_control_cards.append(
            f"""
            <article class="activity-item">
              <div class="activity-header">
                <h3>{escape(str(event.get('text', '未命名控场结果')))}</h3>
                <span class="activity-meta">{escape(str(event.get('created_at') or ''))}</span>
              </div>
              {f'<p class="meta-line">{type_label}</p>' if type_label else ''}
              <p class="meta-line">event_type: <span class="mono">{escape(str(event.get('event_type') or 'manual_action'))}</span></p>
              {f'<p class="meta-line">{jump_link}</p>' if jump_link else ''}
            </article>
            """
        )
    live_control_block = (
        "".join(live_control_cards)
        if live_control_cards
        else '<p class="empty-state">还没有最近控场结果。</p>'
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
          <article class="summary-card">
            <h3>最近控场结果</h3>
            <div class="recent-list">{live_control_block}</div>
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


def _render_keeper_runtime_assistance_panel(
    runtime_assistance: dict[str, list[dict[str, Any]]] | None,
    *,
    session_id: str,
) -> str:
    assistance = runtime_assistance or {}
    rule_hints = assistance.get("rule_hints") or []
    knowledge_hints = assistance.get("knowledge_hints") or []
    default_query_text = str(rule_hints[0].get("title") or "").strip() if rule_hints else ""
    rules_query_href = (
        f"/playtest/sessions/{escape(session_id)}/rules?{escape(urlencode({'query_text': default_query_text}), quote=True)}"
        if default_query_text
        else f"/playtest/sessions/{escape(session_id)}/rules"
    )

    if rule_hints:
        rule_items: list[str] = []
        for hint in rule_hints[:3]:
            citations = hint.get("citations") or []
            rule_items.append(
                f"""
                <article class="activity-item">
                  <div class="activity-header">
                    <h3>{escape(str(hint.get('title') or '规则提示'))}</h3>
                    <span class="activity-meta">{escape(str(hint.get('context_label') or '当前局面'))}</span>
                  </div>
                  <p>{escape(str(hint.get('summary') or '未命中可用规则依据。'))}</p>
                  {
                      f"<p class=\"meta-line\">引用：{escape('；'.join(str(citation) for citation in citations))}</p>"
                      if citations
                      else ""
                  }
                </article>
                """
            )
        rule_block = "".join(rule_items)
    else:
        rule_block = '<p class="empty-state">当前局面还没有明显相关的规则提示。</p>'

    if knowledge_hints:
        knowledge_items: list[str] = []
        for hint in knowledge_hints[:3]:
            meta_parts = [
                f"来源：{hint['source_title']}"
                for key in ["source_title"]
                if hint.get(key)
            ]
            if hint.get("query_text"):
                meta_parts.append(f"关联上下文：{hint['query_text']}")
            knowledge_items.append(
                f"""
                <article class="activity-item">
                  <div class="activity-header">
                    <h3>{escape(str(hint.get('title') or '资料摘要'))}</h3>
                  </div>
                  <p>{escape(str(hint.get('summary') or ''))}</p>
                  {
                      f"<p class=\"meta-line\">{escape(' · '.join(str(part) for part in meta_parts))}</p>"
                      if meta_parts
                      else ""
                  }
                </article>
                """
            )
        knowledge_block = "".join(knowledge_items)
    else:
        knowledge_block = '<p class="empty-state">当前局面还没有明显相关的知识摘要。</p>'

    return f"""
      <section class="panel" id="runtime-assistance">
        <div class="checkpoint-summary-header">
          <h2>规则与知识辅助</h2>
          <a class="action-link" href="{rules_query_href}">更多规则查询</a>
        </div>
        <div class="summary-grid">
          <article class="summary-card">
            <h3>当前相关规则提示</h3>
            <div class="recent-list">{rule_block}</div>
          </article>
          <article class="summary-card">
            <h3>当前相关知识摘要</h3>
            <div class="recent-list">{knowledge_block}</div>
          </article>
        </div>
      </section>
    """


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
    runtime_assistance: dict[str, list[dict[str, Any]]] | None = None,
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
    session_status = snapshot.get("status") or SessionStatus.PLANNED.value
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
          <span>当前状态：{_render_session_status_display(session_status)}</span>
          <span class="status-pill{' warn' if warnings else ''}">
            {escape('存在降级/外部来源告警' if warnings else '状态正常')}
          </span>
        </div>
        <div class="nav-links">
          {_render_session_index_link()}
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
              <li>当前状态：{escape(_session_status_label(session_status))} <span class="mono">{escape(str(session_status))}</span></li>
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
      {_render_session_lifecycle_panel(
          session_snapshot=snapshot,
          keeper_view=current_view,
          checkpoints=checkpoints,
          session_id=session_id,
          operator_id=keeper_id,
      )}
      <section class="panel" id="attention">
        <h2>待处理</h2>
        <div class="attention-grid">
          {_render_attention_block(title='KP 提示', items=prompt_items, empty_text='当前没有待处理的 KP 提示。')}
          {_render_attention_block(title='待审草稿', items=draft_items, empty_text='当前没有待审草稿。')}
          {_render_attention_block(title='未完成目标', items=objective_items, empty_text='当前没有未完成目标。')}
        </div>
      </section>
      {_render_san_aftermath_panel(active_prompts)}
      {_render_keeper_live_control_panel(
          keeper_view=current_view,
          session_id=session_id,
          operator_id=keeper_id,
      )}
      {_render_keeper_runtime_assistance_panel(runtime_assistance, session_id=session_id)}
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

def _render_rules_query_results(result: dict[str, Any] | None) -> str:
    if result is None:
        return '<p class="empty-state">输入一条当前想确认的规则问题，再继续查询。</p>'
    matched_chunks = result.get("matched_chunks") or []
    if not matched_chunks:
        return '<p class="empty-state">当前查询没有命中规则摘要。</p>'

    answer_draft = result.get("chinese_answer_draft")
    citations = result.get("citations") or []
    cards: list[str] = []
    for chunk in matched_chunks[:4]:
        cards.append(
            f"""
            <article class="activity-item">
              <div class="activity-header">
                <h3>{escape(str(chunk.get('resolved_topic') or chunk.get('topic_key') or '规则命中'))}</h3>
              </div>
              <p>{escape(str(chunk.get('text') or ''))}</p>
              {
                  f"<p class=\"meta-line\">引用：{escape(str(chunk.get('short_citation')))}</p>"
                  if chunk.get("short_citation")
                  else ""
              }
            </article>
            """
        )
    citation_block = (
        f'<p class="feedback-code">引用：{escape("；".join(str(citation) for citation in citations))}</p>'
        if citations
        else ""
    )
    answer_block = (
        f'<section class="feedback feedback-success"><h2>规则查询结果</h2><p>{escape(str(answer_draft))}</p>{citation_block}</section>'
        if answer_draft
        else '<section class="feedback feedback-success"><h2>规则查询结果</h2></section>'
    )
    return answer_block + '<div class="recent-list">' + "".join(cards) + "</div>"


def _render_playtest_rules_query_page(
    *,
    session_id: str,
    session_snapshot: dict[str, Any] | None,
    query_text: str | None = None,
    query_result: dict[str, Any] | None = None,
    detail: dict[str, Any] | str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    snapshot = session_snapshot or {}
    current_scene = snapshot.get("current_scene") or {}
    progress_state = snapshot.get("progress_state") or {}
    session_status = snapshot.get("status") or SessionStatus.PLANNED.value
    body = f"""
      <section class="hero">
        <h1>规则查询</h1>
        <div class="hero-meta">
          <span>session_id: <code>{escape(session_id)}</code></span>
          <span>当前场景：{escape(str(current_scene.get('title', '未知场景')))}</span>
          <span>当前 beat：{escape(str(progress_state.get('current_beat') or '无'))}</span>
          <span>当前状态：{_render_session_status_display(session_status)}</span>
        </div>
        <div class="nav-links">
          {_render_session_index_link()}
          {_render_launcher_link(session_id)}
          <a href="/playtest/sessions/{escape(session_id)}/keeper">返回主持人工作台</a>
        </div>
      </section>
      {_render_detail(detail)}
      <section class="panel">
        <h2>继续查规则</h2>
        <form method="post" action="/playtest/sessions/{escape(session_id)}/rules" data-submit-label="查询中...">
          <label for="query_text">query_text</label>
          <input id="query_text" name="query_text" type="text" value="{escape(query_text or '', quote=True)}" placeholder="例如：侦察能发现隐藏线索吗" />
          <button type="submit">查询规则</button>
        </form>
      </section>
      <section class="panel">
        <h2>命中摘要</h2>
        {_render_rules_query_results(query_result)}
      </section>
    """
    return _render_shell(
        title=f"会话 {session_id} 规则查询",
        body=body,
        status_code=status_code,
        include_form_script=True,
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
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
]:
    session, keeper_view, checkpoints, warnings = service.get_keeper_workspace(session_id)
    return (
        session.model_dump(mode="json"),
        keeper_view.model_dump(mode="json"),
        [checkpoint.model_dump(mode="json") for checkpoint in checkpoints],
        [warning.model_dump(mode="json") for warning in warnings],
        service.get_keeper_runtime_assistance(keeper_view=keeper_view),
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


def _render_playtest_recap_from_service(
    *,
    service: SessionService,
    session_id: str,
    detail: dict[str, Any] | str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    try:
        snapshot = service.snapshot_session(session_id)
    except LookupError as exc:
        return _render_playtest_recap_page(
            session_id=session_id,
            session_snapshot=None,
            detail=detail or extract_error_detail(exc),
            status_code=(
                status_code
                if status_code != status.HTTP_200_OK
                else status.HTTP_404_NOT_FOUND
            ),
        )
    return _render_playtest_recap_page(
        session_id=session_id,
        session_snapshot=snapshot,
        detail=detail,
        status_code=status_code,
    )


def _render_playtest_rules_query_from_service(
    *,
    service: SessionService,
    knowledge_service: KnowledgeService,
    session_id: str,
    query_text: str | None = None,
    query_result: dict[str, Any] | None = None,
    detail: dict[str, Any] | str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    try:
        snapshot = service.snapshot_session(session_id)
    except LookupError as exc:
        return _render_playtest_rules_query_page(
            session_id=session_id,
            session_snapshot=None,
            query_text=query_text,
            query_result=query_result,
            detail=detail or extract_error_detail(exc),
            status_code=(
                status_code
                if status_code != status.HTTP_200_OK
                else status.HTTP_404_NOT_FOUND
            ),
        )

    if query_result is None and query_text is not None and query_text.strip():
        try:
            result = knowledge_service.query_rules(
                RuleQueryRequest(
                    query_text=query_text.strip(),
                    viewer_role=ViewerRole.KEEPER.value,
                )
            )
            query_result = result.model_dump(mode="json")
        except (ValidationError, LookupError, ValueError) as exc:
            return _render_playtest_rules_query_page(
                session_id=session_id,
                session_snapshot=snapshot,
                query_text=query_text,
                query_result=None,
                detail=(
                    _build_validation_detail(exc)
                    if isinstance(exc, ValidationError)
                    else extract_error_detail(exc)
                ),
                status_code=_playtest_status_for_exception(exc),
            )

    return _render_playtest_rules_query_page(
        session_id=session_id,
        session_snapshot=snapshot,
        query_text=query_text,
        query_result=query_result,
        detail=detail,
        status_code=status_code,
    )

def _render_playtest_session_index_from_service(
    *,
    service: SessionService,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    sessions = [session.model_dump(mode="json") for session in service.list_sessions()]
    return _render_playtest_session_index_page(
        sessions=sessions,
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


def _investigator_skill_options(
    viewer_summary: dict[str, Any] | None,
    own_character_state: dict[str, Any],
) -> list[tuple[str, int]]:
    viewer_character = (
        viewer_summary.get("character")
        if isinstance(viewer_summary, dict) and isinstance(viewer_summary.get("character"), dict)
        else {}
    )
    merged_skills: dict[str, int] = {}
    character_skills = viewer_character.get("skills")
    if isinstance(character_skills, dict):
        for skill_name, score in character_skills.items():
            normalized_skill_name = str(skill_name).strip()
            if normalized_skill_name:
                merged_skills[normalized_skill_name] = int(score)
    baseline_skills = own_character_state.get("skill_baseline")
    if isinstance(baseline_skills, dict):
        for skill_name, score in baseline_skills.items():
            normalized_skill_name = str(skill_name).strip()
            if normalized_skill_name:
                merged_skills.setdefault(normalized_skill_name, int(score))
    return sorted(merged_skills.items(), key=lambda item: (-item[1], item[0]))


def _investigator_attribute_label_pairs() -> list[tuple[str, str]]:
    return [
        ("strength", "力量"),
        ("constitution", "体质"),
        ("size", "体型"),
        ("dexterity", "敏捷"),
        ("appearance", "外貌"),
        ("intelligence", "智力"),
        ("power", "意志"),
        ("education", "教育"),
    ]


def _investigator_attribute_options(
    viewer_summary: dict[str, Any] | None,
) -> list[tuple[str, str, int]]:
    viewer_character = (
        viewer_summary.get("character")
        if isinstance(viewer_summary, dict) and isinstance(viewer_summary.get("character"), dict)
        else {}
    )
    attributes = (
        viewer_character.get("attributes")
        if isinstance(viewer_character.get("attributes"), dict)
        else {}
    )
    options: list[tuple[str, str, int]] = []
    for attribute_name, label in _investigator_attribute_label_pairs():
        if attribute_name in attributes:
            options.append((attribute_name, label, int(attributes[attribute_name])))
    return options


def _render_skill_check_outcome_label(outcome_value: Any) -> str:
    return {
        "critical_success": "大成功",
        "extreme_success": "极难成功",
        "hard_success": "困难成功",
        "success": "成功",
        "failure": "失败",
        "fumble": "大失败",
    }.get(str(outcome_value or ""), str(outcome_value or "未知"))


def _render_investigator_check_result(
    *,
    message: str,
    check_type_label: str,
    subject_value: str,
    numeric_value: Any,
    numeric_label: str = "数值",
    roll_total: Any,
    outcome_value: Any,
    extra_lines: list[str] | None = None,
) -> str:
    rendered_extra_lines = "".join(
        f"<p>{escape(str(line))}</p>" for line in (extra_lines or [])
    )
    return (
        '<section class="feedback feedback-success">'
        "<h2>最近一次检定结果</h2>"
        f"<p>{escape(str(message))}</p>"
        f"<p>类型：{escape(check_type_label)}</p>"
        f"<p>项目：{escape(subject_value)}</p>"
        f"<p>{escape(numeric_label)}：{escape(str(numeric_value))}</p>"
        f"<p>掷骰结果：{escape(str(roll_total))}</p>"
        f"<p>判定：{escape(_render_skill_check_outcome_label(outcome_value))}</p>"
        f"{rendered_extra_lines}"
        "</section>"
    )


def _render_investigator_skill_check_result(skill_check_result: dict[str, Any] | None) -> str:
    if skill_check_result is None:
        return ""
    roll = skill_check_result.get("roll") or {}
    return _render_investigator_check_result(
        message=str(skill_check_result.get("message", "已完成技能检定")),
        check_type_label="技能检定",
        subject_value=str(skill_check_result.get("skill_name", "—")),
        numeric_value=skill_check_result.get("skill_value", "—"),
        roll_total=roll.get("total", "—"),
        outcome_value=roll.get("outcome"),
    )


def _render_investigator_attribute_check_result(attribute_check_result: dict[str, Any] | None) -> str:
    if attribute_check_result is None:
        return ""
    roll = attribute_check_result.get("roll") or {}
    attribute_name = str(attribute_check_result.get("attribute_name", ""))
    attribute_label = dict(_investigator_attribute_label_pairs()).get(attribute_name, attribute_name or "—")
    return _render_investigator_check_result(
        message=str(attribute_check_result.get("message", "已完成属性检定")),
        check_type_label="属性检定",
        subject_value=attribute_label,
        numeric_value=attribute_check_result.get("attribute_value", "—"),
        roll_total=roll.get("total", "—"),
        outcome_value=roll.get("outcome"),
    )


def _render_investigator_san_check_result(san_check_result: dict[str, Any] | None) -> str:
    if san_check_result is None:
        return ""
    roll = san_check_result.get("roll") or {}
    applied_expression = str(san_check_result.get("applied_loss_expression") or "—")
    resolved_loss = san_check_result.get("resolved_sanity_loss", "—")
    return _render_investigator_check_result(
        message=str(san_check_result.get("message", "已完成理智检定")),
        check_type_label="理智检定",
        subject_value=str(san_check_result.get("source_label", "—")),
        numeric_label="检定前 SAN",
        numeric_value=san_check_result.get("previous_sanity", "—"),
        roll_total=roll.get("total", "—"),
        outcome_value=roll.get("outcome"),
        extra_lines=[
            f"成功损失：{san_check_result.get('success_loss', '—')}",
            f"失败损失：{san_check_result.get('failure_loss', '—')}",
            f"本次 SAN 损失：{resolved_loss}（依据 {applied_expression}）",
            f"检定后 SAN：{san_check_result.get('current_sanity', '—')}",
        ],
    )


def _render_investigator_skill_check_panel(
    *,
    session_id: str,
    viewer_id: str,
    skill_options: list[tuple[str, int]],
    selected_skill_name: str | None,
    session_status: str,
) -> str:
    if session_status == SessionStatus.COMPLETED.value:
        return """
      <section class="panel">
        <h2>快速技能检定</h2>
        <p class="empty-state">本局已结束，当前页面不再进行新的技能检定。</p>
      </section>
        """
    if not skill_options:
        return """
      <section class="panel">
        <h2>快速技能检定</h2>
        <p class="empty-state">当前角色没有可用于快速检定的技能。</p>
      </section>
        """
    normalized_selected_skill = str(selected_skill_name or skill_options[0][0])
    options_html = "".join(
        f'<option value="{escape(skill_name)}"{" selected" if skill_name == normalized_selected_skill else ""}>{escape(skill_name)} ({escape(str(skill_score))})</option>'
        for skill_name, skill_score in skill_options
    )
    return f"""
      <section class="panel">
        <h2>快速技能检定</h2>
        <form method="post" action="/playtest/sessions/{escape(session_id)}/investigator/{escape(viewer_id)}/skill-check" data-submit-label="检定中...">
          <label>
            skill_name
            <select name="skill_name">
              {options_html}
            </select>
          </label>
          <p class="help">从当前角色已有技能中选择一项，快速进行一次普通检定。</p>
          <button type="submit">开始检定</button>
        </form>
      </section>
    """


def _render_investigator_attribute_check_panel(
    *,
    session_id: str,
    viewer_id: str,
    attribute_options: list[tuple[str, str, int]],
    selected_attribute_name: str | None,
    session_status: str,
) -> str:
    if session_status == SessionStatus.COMPLETED.value:
        return """
      <section class="panel">
        <h2>快速属性检定</h2>
        <p class="empty-state">本局已结束，当前页面不再进行新的属性检定。</p>
      </section>
        """
    if not attribute_options:
        return """
      <section class="panel">
        <h2>快速属性检定</h2>
        <p class="empty-state">当前角色没有可用于快速检定的基础属性。</p>
      </section>
        """
    normalized_selected_attribute = str(selected_attribute_name or attribute_options[0][0])
    options_html = "".join(
        f'<option value="{escape(attribute_name)}"{" selected" if attribute_name == normalized_selected_attribute else ""}>{escape(label)} ({escape(str(attribute_value))})</option>'
        for attribute_name, label, attribute_value in attribute_options
    )
    return f"""
      <section class="panel">
        <h2>快速属性检定</h2>
        <form method="post" action="/playtest/sessions/{escape(session_id)}/investigator/{escape(viewer_id)}/attribute-check" data-submit-label="检定中...">
          <label>
            attribute_name
            <select name="attribute_name">
              {options_html}
            </select>
          </label>
          <p class="help">从当前角色的 8 项基础属性中选择一项，快速进行一次普通检定。</p>
          <button type="submit">开始属性检定</button>
        </form>
      </section>
    """


def _render_investigator_san_check_panel(
    *,
    session_id: str,
    viewer_id: str,
    source_label: str | None,
    success_loss: str | None,
    failure_loss: str | None,
    session_status: str,
) -> str:
    if session_status == SessionStatus.COMPLETED.value:
        return """
      <section class="panel">
        <h2>快速理智检定</h2>
        <p class="empty-state">本局已结束，当前页面不再进行新的理智检定。</p>
      </section>
        """
    return f"""
      <section class="panel">
        <h2>快速理智检定</h2>
        <form method="post" action="/playtest/sessions/{escape(session_id)}/investigator/{escape(viewer_id)}/san-check" data-submit-label="检定中...">
          <label>
            source_label
            <input type="text" name="source_label" value="{escape(str(source_label or ''))}" placeholder="例如：哈斯塔的模糊倒影" required />
          </label>
          <label>
            success_loss
            <input type="text" name="success_loss" value="{escape(str(success_loss or ''))}" placeholder="例如：0 或 1" required />
          </label>
          <label>
            failure_loss
            <input type="text" name="failure_loss" value="{escape(str(failure_loss or ''))}" placeholder="例如：1d3 或 1d6" required />
          </label>
          <p class="help">按本次遭遇填写来源标签与 success/failure 损失；当前只支持整数或 NdM，不使用固定怪物 SAN 表。</p>
          <button type="submit">开始理智检定</button>
        </form>
      </section>
    """


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


def _render_clue_status_label(status_value: Any) -> str:
    status_labels = {
        "undiscovered": "未发现",
        "discovered": "已发现",
        "partially_understood": "部分理解",
        "shared_with_party": "已分享给全队",
        "private_to_actor": "仅自己可见",
    }
    return status_labels.get(str(status_value), str(status_value or "未知"))


def _render_investigator_clue_details(visible_clues: list[dict[str, Any]]) -> str:
    if not visible_clues:
        return '<p class="empty-state">当前还没有你可见的线索。</p>'
    items: list[str] = []
    for clue in visible_clues:
        title = str(clue.get("title") or "未命名线索")
        description = str(clue.get("text") or "当前没有更多线索说明。")
        status_label = _render_clue_status_label(clue.get("status"))
        clue_id = clue.get("clue_id")
        items.append(
            f"""
            <article class="activity-item">
              <div class="activity-header">
                <h3>{escape(title)}</h3>
              </div>
              <p>{escape(description)}</p>
              <p class="meta-line">状态：{escape(status_label)}</p>
              {
                  f'<p class="meta-line">clue_id: <span class="mono">{escape(str(clue_id))}</span></p>'
                  if clue_id
                  else ''
              }
            </article>
            """
        )
    return '<div class="recent-list">' + "".join(items) + "</div>"


def _render_investigator_character_panel(
    *,
    viewer_name: str,
    scene_title: str,
    viewer_summary: dict[str, Any] | None,
    own_character_state: dict[str, Any],
) -> str:
    viewer_character = (
        viewer_summary.get("character")
        if isinstance(viewer_summary, dict) and isinstance(viewer_summary.get("character"), dict)
        else {}
    )
    occupation = viewer_character.get("occupation")
    age = viewer_character.get("age")
    attributes = viewer_character.get("attributes") if isinstance(viewer_character.get("attributes"), dict) else {}
    skills = viewer_character.get("skills") if isinstance(viewer_character.get("skills"), dict) else {}
    inventory = [str(item) for item in own_character_state.get("inventory") or [] if str(item).strip()]
    attribute_labels = dict(_investigator_attribute_label_pairs())
    attribute_summary = " · ".join(
        f"{attribute_labels[key]} {value}"
        for key, value in attributes.items()
        if key in attribute_labels
    )
    top_skills = sorted(
        ((str(skill), int(score)) for skill, score in skills.items()),
        key=lambda item: (-item[1], item[0]),
    )[:5]
    skill_summary = " · ".join(
        f"{skill} {score}" for skill, score in top_skills
    )
    return f"""
      <p class="meta-line">角色：{escape(str(viewer_name))}</p>
      <p class="meta-line">当前场景：{escape(str(scene_title))}</p>
      {
          f'<p class="meta-line">职业：{escape(str(occupation))} · 年龄：{escape(str(age))}</p>'
          if occupation or age is not None
          else ''
      }
      <ul>
        <li>HP：{escape(str(own_character_state.get('current_hit_points', '—')))}</li>
        <li>MP：{escape(str(own_character_state.get('current_magic_points', '—')))}</li>
        <li>SAN：{escape(str(own_character_state.get('current_sanity', '—')))}</li>
      </ul>
      {
          f'<p class="meta-line">关键属性：{escape(attribute_summary)}</p>'
          if attribute_summary
          else ''
      }
      {
          f'<p class="meta-line">擅长技能：{escape(skill_summary)}</p>'
          if skill_summary
          else ''
      }
      <p class="meta-line">随身物品</p>
      {_render_list_or_empty(inventory, empty_text="当前没有可见的随身物品。")}
    """


def _render_investigator_private_notes(
    own_character_state: dict[str, Any],
    own_private_state: dict[str, Any],
) -> str:
    entries: list[tuple[str, str]] = []
    for note in own_private_state.get("private_notes") or []:
        note_text = str(note).strip()
        if note_text:
            entries.append(("角色私密记录", note_text))
    for note in own_character_state.get("private_notes") or []:
        note_text = str(note).strip()
        if note_text:
            entries.append(("会话备注", note_text))
    if not entries:
        return '<p class="empty-state">当前没有你的私有备注。</p>'
    items = [
        f"""
        <article class="activity-item">
          <p class="meta-line">{escape(label)}</p>
          <p>{escape(note)}</p>
        </article>
        """
        for label, note in entries
    ]
    return '<div class="recent-list">' + "".join(items) + "</div>"


def _render_investigator_page(
    *,
    session_id: str,
    viewer_id: str,
    investigator_view: dict[str, Any] | None,
    session_status: str | None = None,
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
    action_result: dict[str, Any] | None = None,
    skill_check_result: dict[str, Any] | None = None,
    attribute_check_result: dict[str, Any] | None = None,
    san_check_result: dict[str, Any] | None = None,
    action_text: str | None = None,
    selected_skill_name: str | None = None,
    selected_attribute_name: str | None = None,
    san_check_source_label: str | None = None,
    san_check_success_loss: str | None = None,
    san_check_failure_loss: str | None = None,
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
        clue
        for clue in view.get("scenario", {}).get("clues", [])
        if isinstance(clue, dict) and clue.get("title")
    ]
    scene_title = view.get("current_scene", {}).get("title", "未知场景")
    state_version = view.get("state_version", "—")
    action_form_value = escape(action_text or "")
    normalized_session_status = str(session_status or SessionStatus.PLANNED.value)
    skill_options = _investigator_skill_options(
        viewer_summary if isinstance(viewer_summary, dict) else None,
        own_character_state,
    )
    attribute_options = _investigator_attribute_options(
        viewer_summary if isinstance(viewer_summary, dict) else None
    )
    completed_notice = (
        '<section class="warning-box"><h2>本局已结束</h2>'
        '<p>当前页面保留结束后的查看状态；你仍可查看自己的可见信息和最近结果。</p>'
        "</section>"
        if normalized_session_status == SessionStatus.COMPLETED.value
        else ""
    )
    action_panel = (
        """
      <section class="panel">
        <h2>提交玩家行动</h2>
        <p class="empty-state">本局已结束，当前页面不再提交新的玩家行动。</p>
      </section>
        """
        if normalized_session_status == SessionStatus.COMPLETED.value
        else f"""
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
        """
    )

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
      {completed_notice}
      {_render_notice(notice)}
      {_render_detail(detail)}
      {_render_investigator_action_result(action_result)}
      {_render_investigator_skill_check_result(skill_check_result)}
      {_render_investigator_attribute_check_result(attribute_check_result)}
      {_render_investigator_san_check_result(san_check_result)}
      <section class="panel">
        <h2>我的摘要</h2>
        <div class="summary-grid">
          <article class="summary-card">
            <h3>当前状态</h3>
            {_render_investigator_character_panel(
                viewer_name=str(viewer_name),
                scene_title=str(scene_title),
                viewer_summary=viewer_summary if isinstance(viewer_summary, dict) else None,
                own_character_state=own_character_state,
            )}
          </article>
          <article class="summary-card">
            <h3>可见线索</h3>
            {_render_investigator_clue_details(visible_clues)}
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
            <h3>私有备注与记录</h3>
            {_render_investigator_private_notes(own_character_state, own_private_state)}
          </article>
        </div>
      </section>
      {action_panel}
      {_render_investigator_skill_check_panel(
          session_id=session_id,
          viewer_id=viewer_id,
          skill_options=skill_options,
          selected_skill_name=selected_skill_name,
          session_status=normalized_session_status,
      )}
      {_render_investigator_attribute_check_panel(
          session_id=session_id,
          viewer_id=viewer_id,
          attribute_options=attribute_options,
          selected_attribute_name=selected_attribute_name,
          session_status=normalized_session_status,
      )}
      {_render_investigator_san_check_panel(
          session_id=session_id,
          viewer_id=viewer_id,
          source_label=san_check_source_label,
          success_loss=san_check_success_loss,
          failure_loss=san_check_failure_loss,
          session_status=normalized_session_status,
      )}
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
        snapshot, keeper_view, checkpoints, warnings, runtime_assistance = _load_keeper_workspace_context(
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
            runtime_assistance=None,
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
        runtime_assistance=runtime_assistance,
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
    skill_check_result: dict[str, Any] | None = None,
    attribute_check_result: dict[str, Any] | None = None,
    san_check_result: dict[str, Any] | None = None,
    action_text: str | None = None,
    selected_skill_name: str | None = None,
    selected_attribute_name: str | None = None,
    san_check_source_label: str | None = None,
    san_check_success_loss: str | None = None,
    san_check_failure_loss: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    try:
        investigator_view = _load_investigator_page_context(service, session_id, viewer_id)
        session_snapshot = service.snapshot_session(session_id)
    except (LookupError, ValueError) as exc:
        fallback_detail = detail or extract_error_detail(exc)
        return _render_investigator_page(
            session_id=session_id,
            viewer_id=viewer_id,
            investigator_view=None,
            session_status=None,
            notice=notice,
            detail=fallback_detail,
            action_result=action_result,
            skill_check_result=skill_check_result,
            attribute_check_result=attribute_check_result,
            san_check_result=san_check_result,
            action_text=action_text,
            selected_skill_name=selected_skill_name,
            selected_attribute_name=selected_attribute_name,
            san_check_source_label=san_check_source_label,
            san_check_success_loss=san_check_success_loss,
            san_check_failure_loss=san_check_failure_loss,
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
        session_status=str(session_snapshot.get("status") or SessionStatus.PLANNED.value),
        notice=notice,
        detail=detail,
        action_result=action_result,
        skill_check_result=skill_check_result,
        attribute_check_result=attribute_check_result,
        san_check_result=san_check_result,
        action_text=action_text,
        selected_skill_name=selected_skill_name,
        selected_attribute_name=selected_attribute_name,
        san_check_source_label=san_check_source_label,
        san_check_success_loss=san_check_success_loss,
        san_check_failure_loss=san_check_failure_loss,
        status_code=status_code,
    )

@router.get("/sessions", response_class=HTMLResponse)
def playtest_session_index_page(
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    return _render_playtest_session_index_from_service(service=service)


@router.get("/groups/{group_name}", response_class=HTMLResponse)
def playtest_group_page(
    group_name: str,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    sessions = [
        session.model_dump(mode="json")
        for session in service.list_sessions()
        if _playtest_group_label(session.playtest_group, empty_label="") == group_name
    ]
    return _render_playtest_group_page(
        group_name=group_name,
        sessions=sessions,
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


@router.get("/sessions/{session_id}/recap", response_class=HTMLResponse)
def playtest_recap_page(
    session_id: str,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    return _render_playtest_recap_from_service(service=service, session_id=session_id)


@router.get("/sessions/{session_id}/keeper", response_class=HTMLResponse)
def keeper_dashboard_page(
    session_id: str,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    return _render_keeper_dashboard_from_service(service=service, session_id=session_id)


@router.get("/sessions/{session_id}/rules", response_class=HTMLResponse)
def playtest_rules_query_page(
    session_id: str,
    query_text: str | None = None,
    service: SessionService = Depends(get_session_service),
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
) -> HTMLResponse:
    return _render_playtest_rules_query_from_service(
        service=service,
        knowledge_service=knowledge_service,
        session_id=session_id,
        query_text=query_text,
    )


@router.post("/sessions/{session_id}/rules", response_class=HTMLResponse)
async def submit_playtest_rules_query(
    session_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    query_text = _normalize_form_text(form.get("query_text")) or ""
    try:
        query_request = RuleQueryRequest(
            query_text=query_text,
            viewer_role=ViewerRole.KEEPER.value,
        )
        result = knowledge_service.query_rules(query_request)
        return _render_playtest_rules_query_from_service(
            service=service,
            knowledge_service=knowledge_service,
            session_id=session_id,
            query_text=query_text,
            query_result=result.model_dump(mode="json"),
        )
    except (ValidationError, LookupError, ValueError) as exc:
        return _render_playtest_exception(
            _render_playtest_rules_query_from_service,
            exc=exc,
            service=service,
            knowledge_service=knowledge_service,
            session_id=session_id,
            query_text=query_text,
        )


@router.post("/sessions/{session_id}/keeper/lifecycle", response_class=HTMLResponse)
async def update_session_lifecycle_via_keeper_dashboard(
    session_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    try:
        response = service.update_keeper_session_lifecycle(
            session_id,
            UpdateSessionLifecycleRequest(
                operator_id=form.get("operator_id", ""),
                target_status=form.get("target_status", ""),
            ),
        )
        return _render_keeper_dashboard_from_service(
            service=service,
            session_id=session_id,
            notice=response.message,
        )
    except (ValidationError, LookupError, PermissionError, ConflictError, ValueError) as exc:
        return _render_playtest_exception(
            _render_keeper_dashboard_from_service,
            exc=exc,
            service=service,
            session_id=session_id,
        )


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


@router.post("/sessions/{session_id}/investigator/{viewer_id}/skill-check", response_class=HTMLResponse)
async def submit_investigator_skill_check_via_ui(
    session_id: str,
    viewer_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    skill_name = _normalize_form_text(form.get("skill_name")) or ""
    try:
        response = service.perform_investigator_skill_check(
            session_id,
            InvestigatorSkillCheckRequest(
                actor_id=viewer_id,
                skill_name=skill_name,
            ),
        )
        return _render_investigator_page_from_service(
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            notice=response.message,
            skill_check_result=response.model_dump(mode="json"),
            selected_skill_name=response.skill_name,
        )
    except (ValidationError, LookupError, ConflictError, ValueError) as exc:
        return _render_playtest_exception(
            _render_investigator_page_from_service,
            exc=exc,
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            selected_skill_name=skill_name,
        )


@router.post("/sessions/{session_id}/investigator/{viewer_id}/attribute-check", response_class=HTMLResponse)
async def submit_investigator_attribute_check_via_ui(
    session_id: str,
    viewer_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    attribute_name = _normalize_form_text(form.get("attribute_name")) or ""
    try:
        response = service.perform_investigator_attribute_check(
            session_id,
            InvestigatorAttributeCheckRequest(
                actor_id=viewer_id,
                attribute_name=attribute_name,
            ),
        )
        return _render_investigator_page_from_service(
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            notice=response.message,
            attribute_check_result=response.model_dump(mode="json"),
            selected_attribute_name=response.attribute_name,
        )
    except (ValidationError, LookupError, ValueError) as exc:
        return _render_playtest_exception(
            _render_investigator_page_from_service,
            exc=exc,
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            selected_attribute_name=attribute_name,
        )


@router.post("/sessions/{session_id}/investigator/{viewer_id}/san-check", response_class=HTMLResponse)
async def submit_investigator_san_check_via_ui(
    session_id: str,
    viewer_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    source_label = _normalize_form_text(form.get("source_label")) or ""
    success_loss = _normalize_form_text(form.get("success_loss")) or ""
    failure_loss = _normalize_form_text(form.get("failure_loss")) or ""
    try:
        response = service.perform_investigator_san_check(
            session_id,
            InvestigatorSanCheckRequest(
                actor_id=viewer_id,
                source_label=source_label,
                success_loss=success_loss,
                failure_loss=failure_loss,
            ),
        )
        return _render_investigator_page_from_service(
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            notice=response.message,
            san_check_result=response.model_dump(mode="json"),
            san_check_source_label=response.source_label,
            san_check_success_loss=response.success_loss,
            san_check_failure_loss=response.failure_loss,
        )
    except (ValidationError, LookupError, ValueError) as exc:
        return _render_playtest_exception(
            _render_investigator_page_from_service,
            exc=exc,
            service=service,
            session_id=session_id,
            viewer_id=viewer_id,
            san_check_source_label=source_label,
            san_check_success_loss=success_loss,
            san_check_failure_loss=failure_loss,
        )


@router.post("/sessions/{session_id}/keeper/objectives/{objective_id}/complete", response_class=HTMLResponse)
async def complete_objective_via_keeper_dashboard(
    session_id: str,
    objective_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    try:
        response = service.complete_keeper_objective(
            session_id,
            objective_id,
            KeeperLiveControlRequest(operator_id=form.get("operator_id", "")),
        )
        return _render_keeper_dashboard_from_service(
            service=service,
            session_id=session_id,
            notice=response.message,
        )
    except (ValidationError, LookupError, PermissionError, ConflictError, ValueError) as exc:
        return _render_playtest_exception(
            _render_keeper_dashboard_from_service,
            exc=exc,
            service=service,
            session_id=session_id,
        )


@router.post("/sessions/{session_id}/keeper/objectives/{objective_id}/reopen", response_class=HTMLResponse)
async def reopen_objective_via_keeper_dashboard(
    session_id: str,
    objective_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    try:
        response = service.reopen_keeper_objective(
            session_id,
            objective_id,
            KeeperLiveControlRequest(operator_id=form.get("operator_id", "")),
        )
        return _render_keeper_dashboard_from_service(
            service=service,
            session_id=session_id,
            notice=response.message,
        )
    except (ValidationError, LookupError, PermissionError, ConflictError, ValueError) as exc:
        return _render_playtest_exception(
            _render_keeper_dashboard_from_service,
            exc=exc,
            service=service,
            session_id=session_id,
        )


@router.post("/sessions/{session_id}/keeper/beats/{beat_id}/advance", response_class=HTMLResponse)
async def advance_beat_via_keeper_dashboard(
    session_id: str,
    beat_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    try:
        response = service.advance_keeper_beat(
            session_id,
            beat_id,
            KeeperLiveControlRequest(operator_id=form.get("operator_id", "")),
        )
        return _render_keeper_dashboard_from_service(
            service=service,
            session_id=session_id,
            notice=response.message,
        )
    except (ValidationError, LookupError, PermissionError, ConflictError, ValueError) as exc:
        return _render_playtest_exception(
            _render_keeper_dashboard_from_service,
            exc=exc,
            service=service,
            session_id=session_id,
        )


@router.post("/sessions/{session_id}/keeper/reveal/clues/{clue_id}", response_class=HTMLResponse)
async def reveal_clue_via_keeper_dashboard(
    session_id: str,
    clue_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    try:
        response = service.reveal_keeper_clue(
            session_id,
            clue_id,
            KeeperLiveControlRequest(operator_id=form.get("operator_id", "")),
        )
        return _render_keeper_dashboard_from_service(
            service=service,
            session_id=session_id,
            notice=response.message,
        )
    except (ValidationError, LookupError, PermissionError, ConflictError, ValueError) as exc:
        return _render_playtest_exception(
            _render_keeper_dashboard_from_service,
            exc=exc,
            service=service,
            session_id=session_id,
        )


@router.post("/sessions/{session_id}/keeper/reveal/scenes/{scene_id}", response_class=HTMLResponse)
async def reveal_scene_via_keeper_dashboard(
    session_id: str,
    scene_id: str,
    request: Request,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    try:
        response = service.reveal_keeper_scene(
            session_id,
            scene_id,
            KeeperLiveControlRequest(operator_id=form.get("operator_id", "")),
        )
        return _render_keeper_dashboard_from_service(
            service=service,
            session_id=session_id,
            notice=response.message,
        )
    except (ValidationError, LookupError, PermissionError, ConflictError, ValueError) as exc:
        return _render_playtest_exception(
            _render_keeper_dashboard_from_service,
            exc=exc,
            service=service,
            session_id=session_id,
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
