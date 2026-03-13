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


def _render_page(
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

    html = f"""
    <!doctype html>
    <html lang="zh-CN">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>Session {escape(session_id)} Checkpoints</title>
        <style>
          :root {{
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
          }}
          body {{
            margin: 0;
            font-family: "Microsoft YaHei UI", "Noto Sans SC", sans-serif;
            background: linear-gradient(180deg, #efe7d6 0%, var(--bg) 100%);
            color: var(--ink);
          }}
          main {{
            max-width: 980px;
            margin: 0 auto;
            padding: 32px 20px 48px;
          }}
          .hero, .panel, .checkpoint-card, .feedback {{
            background: var(--card);
            border: 1px solid var(--line);
            border-radius: 16px;
            box-shadow: 0 10px 30px rgba(43, 33, 24, 0.06);
          }}
          .hero, .panel, .feedback {{
            padding: 20px;
            margin-bottom: 18px;
          }}
          .hero h1 {{
            margin: 0 0 8px;
            font-size: 28px;
          }}
          .hero-meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px 18px;
            color: var(--muted);
          }}
          .panel h2 {{
            margin-top: 0;
          }}
          form {{
            display: grid;
            gap: 12px;
          }}
          label {{
            display: grid;
            gap: 6px;
            font-size: 14px;
          }}
          input, textarea, button {{
            font: inherit;
          }}
          input, textarea {{
            border: 1px solid var(--line);
            border-radius: 10px;
            padding: 10px 12px;
            background: #fff;
          }}
          button {{
            border: none;
            border-radius: 999px;
            padding: 10px 14px;
            background: var(--accent);
            color: #fff;
            cursor: pointer;
          }}
          button.danger {{
            background: var(--danger);
          }}
          button:disabled {{
            opacity: 0.6;
            cursor: wait;
          }}
          .checkpoint-list {{
            display: grid;
            gap: 14px;
          }}
          .checkpoint-card {{
            padding: 18px;
          }}
          .checkpoint-card-header {{
            display: flex;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 16px;
          }}
          .checkpoint-card-header h3 {{
            margin: 0 0 6px;
          }}
          .checkpoint-meta {{
            display: grid;
            gap: 6px;
            min-width: 180px;
            color: var(--muted);
            font-size: 13px;
            text-align: right;
          }}
          .checkpoint-actions {{
            display: grid;
            gap: 14px;
          }}
          .checkpoint-secondary-actions {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
          }}
          .feedback-success {{
            border-color: rgba(36, 92, 61, 0.25);
          }}
          .feedback-error {{
            border-color: rgba(139, 47, 47, 0.25);
          }}
          .feedback-code, .muted, .empty-state, .help {{
            color: var(--muted);
          }}
          .warning-box {{
            margin-top: 14px;
            padding: 14px;
            border-radius: 12px;
            background: rgba(122, 91, 17, 0.08);
            color: var(--warn);
          }}
          code {{
            background: rgba(43, 33, 24, 0.08);
            padding: 2px 6px;
            border-radius: 6px;
          }}
        </style>
      </head>
      <body>
        <main>
          <section class="hero">
            <h1>{escape(str(scenario_title))}</h1>
            <div class="hero-meta">
              <span>session_id: <code>{escape(session_id)}</code></span>
              <span>当前场景：{escape(str(current_scene))}</span>
              <span>版本：{escape(str(state_version))}</span>
              <span>KP：{escape(str(keeper_name))}</span>
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
        </main>
        <script>
          document.querySelectorAll("form[data-submit-label]").forEach((form) => {{
            form.addEventListener("submit", (event) => {{
              const confirmMessage = form.dataset.confirm;
              if (confirmMessage && !window.confirm(confirmMessage)) {{
                event.preventDefault();
                return;
              }}
              const button = form.querySelector("button[type='submit']");
              if (!button) {{
                return;
              }}
              button.disabled = true;
              button.dataset.originalText = button.textContent;
              button.textContent = form.dataset.submitLabel || "处理中...";
            }});
          }});
        </script>
      </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=status_code)


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


def _render_page_from_service(
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
        return _render_page(
            session_id=session_id,
            session_snapshot=None,
            checkpoints=[],
            notice=notice,
            detail=fallback_detail,
            restore_result=restore_result,
            status_code=status_code,
        )
    return _render_page(
        session_id=session_id,
        session_snapshot=snapshot,
        checkpoints=checkpoints,
        notice=notice,
        detail=detail,
        restore_result=restore_result,
        status_code=status_code,
    )


@router.get("/sessions/{session_id}", response_class=HTMLResponse)
def session_checkpoint_page(
    session_id: str,
    service: SessionService = Depends(get_session_service),
) -> HTMLResponse:
    return _render_page_from_service(service=service, session_id=session_id)


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
        return _render_page_from_service(
            service=service,
            session_id=session_id,
            notice="检查点已创建",
        )
    except ValidationError as exc:
        return _render_page_from_service(
            service=service,
            session_id=session_id,
            detail=_build_validation_detail(exc),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except LookupError as exc:
        return _render_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except ConflictError as exc:
        return _render_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_409_CONFLICT,
        )
    except ValueError as exc:
        return _render_page_from_service(
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
        return _render_page_from_service(
            service=service,
            session_id=session_id,
            notice="检查点已更新",
        )
    except ValidationError as exc:
        return _render_page_from_service(
            service=service,
            session_id=session_id,
            detail=_build_validation_detail(exc),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except LookupError as exc:
        return _render_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except ConflictError as exc:
        return _render_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_409_CONFLICT,
        )
    except ValueError as exc:
        return _render_page_from_service(
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
        return _render_page_from_service(
            service=service,
            session_id=session_id,
            notice="检查点已删除",
        )
    except LookupError as exc:
        return _render_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except ConflictError as exc:
        return _render_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_409_CONFLICT,
        )
    except ValueError as exc:
        return _render_page_from_service(
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
        return _render_page_from_service(
            service=service,
            session_id=session_id,
            restore_result=restore_response.model_dump(mode="json"),
            status_code=status.HTTP_200_OK,
        )
    except LookupError as exc:
        return _render_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except ConflictError as exc:
        return _render_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_409_CONFLICT,
        )
    except ValueError as exc:
        return _render_page_from_service(
            service=service,
            session_id=session_id,
            detail=extract_error_detail(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
