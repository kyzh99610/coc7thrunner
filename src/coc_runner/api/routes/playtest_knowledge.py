from __future__ import annotations

from html import escape
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from coc_runner.api.dependencies import get_knowledge_service
from coc_runner.api.routes.playtest_shared import (
    _build_validation_detail,
    _format_datetime,
    _normalize_form_text,
    _playtest_status_for_exception,
    _read_form_payload,
    _render_detail,
    _render_knowledge_index_link,
    _render_session_index_link,
    _render_shell,
)
from coc_runner.application.knowledge_service import KnowledgeService
from coc_runner.error_details import build_knowledge_error_detail, extract_error_detail
from knowledge.schemas import KnowledgeSourceRegistration, TextIngestRequest


router = APIRouter()


def _knowledge_kind_label(kind_value: Any) -> str:
    return {
        "rulebook": "规则书",
        "character_sheet": "人物卡",
        "house_rule": "房规",
        "module": "模组资料",
        "campaign_note": "跑团笔记",
    }.get(str(kind_value), str(kind_value or "未知"))


def _knowledge_format_label(format_value: Any) -> str:
    return {
        "plain_text": "纯文本",
        "markdown": "Markdown",
        "pdf": "PDF",
        "json": "JSON",
        "csv": "CSV",
        "xlsx": "XLSX",
    }.get(str(format_value), str(format_value or "未知"))


def _knowledge_ingest_status_label(status_value: Any) -> str:
    return {
        "registered": "已登记",
        "ingested": "已入库",
    }.get(str(status_value), str(status_value or "未知"))


def _summarize_text_preview(text_value: Any, *, limit: int = 180) -> str:
    text = " ".join(str(text_value or "").split())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _render_notice(notice: str | None) -> str:
    if not notice:
        return ""
    return (
        '<section class="feedback feedback-success">'
        f"<p>{escape(notice)}</p>"
        "</section>"
    )


def _knowledge_kind_options() -> list[tuple[str, str]]:
    return [
        ("rulebook", "规则书"),
        ("character_sheet", "人物卡"),
        ("house_rule", "房规"),
        ("module", "模组资料"),
        ("campaign_note", "跑团笔记"),
    ]


def _knowledge_format_options() -> list[tuple[str, str]]:
    return [
        ("plain_text", "纯文本"),
        ("markdown", "Markdown"),
        ("pdf", "PDF"),
        ("json", "JSON"),
        ("csv", "CSV"),
        ("xlsx", "XLSX"),
    ]


def _default_register_form_values() -> dict[str, str]:
    return {
        "source_id": "",
        "source_title_zh": "",
        "source_kind": "rulebook",
        "source_format": "plain_text",
    }


def _normalize_register_form_values(form: dict[str, str]) -> dict[str, str]:
    values = _default_register_form_values()
    values["source_id"] = form.get("source_id", "")
    values["source_title_zh"] = form.get("source_title_zh", "")
    values["source_kind"] = form.get("source_kind", values["source_kind"])
    values["source_format"] = form.get("source_format", values["source_format"])
    return values


def _build_playtest_source_registration_request(
    form_values: dict[str, str],
) -> KnowledgeSourceRegistration:
    source_id = _normalize_form_text(form_values.get("source_id")) or ""
    source_title_zh = _normalize_form_text(form_values.get("source_title_zh")) or None
    return KnowledgeSourceRegistration.model_validate(
        {
            "source_id": source_id,
            "source_title_zh": source_title_zh,
            "source_kind": form_values.get("source_kind") or "rulebook",
            "source_format": form_values.get("source_format") or "plain_text",
            "document_identity": source_id,
        }
    )


def _render_playtest_knowledge_index_page(
    *,
    sources: list[dict[str, Any]],
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
    register_form_values: dict[str, str] | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    form_values = register_form_values or _default_register_form_values()
    kind_options = "".join(
        f'<option value="{escape(value)}" {"selected" if form_values.get("source_kind") == value else ""}>{escape(label)}</option>'
        for value, label in _knowledge_kind_options()
    )
    format_options = "".join(
        f'<option value="{escape(value)}" {"selected" if form_values.get("source_format") == value else ""}>{escape(label)}</option>'
        for value, label in _knowledge_format_options()
    )
    if not sources:
        source_cards = '<p class="empty-state">当前还没有已登记的知识资料。</p>'
    else:
        cards: list[str] = []
        for source in sources:
            source_id = str(source.get("source_id") or "")
            cards.append(
                f"""
                <article class="attention-card">
                  <div class="activity-header">
                    <h3>{escape(str(source.get('source_title_zh') or source_id or '未命名资料'))}</h3>
                    <span class="activity-meta">{escape(_knowledge_ingest_status_label(source.get('ingest_status')))}</span>
                  </div>
                  <p class="meta-line">source_id: <code>{escape(source_id)}</code></p>
                  <p class="meta-line">类型：{escape(_knowledge_kind_label(source.get('source_kind')))}</p>
                  <p class="meta-line">格式：{escape(_knowledge_format_label(source.get('source_format')))}</p>
                  <p class="meta-line">chunk_count：<span class="mono">{escape(str(source.get('chunk_count', 0)))}</span></p>
                  <p class="meta-line">最后更新时间：{escape(_format_datetime(source.get('updated_at') or source.get('registered_at') or ''))}</p>
                  {
                      '<p class="meta-line">包含人物卡提取结果</p>'
                      if source.get("character_sheet_extraction")
                      else ''
                  }
                  <div class="quick-actions">
                    <a class="action-link" href="/playtest/knowledge/{escape(source_id)}">查看资料详情</a>
                  </div>
                </article>
                """
            )
        source_cards = "".join(cards)
    body = f"""
      <section class="hero">
        <h1>准备资料</h1>
        <div class="hero-meta">
          <span>当前已登记资料数：{escape(str(len(sources)))}</span>
          <span>这里列出当前可用于准备与跑团参考的知识资料。</span>
        </div>
        <div class="nav-links">
          {_render_session_index_link()}
        </div>
      </section>
      {_render_notice(notice)}
      {_render_detail(detail)}
      <section class="panel">
        <h2>新增资料</h2>
        <form method="post" action="/playtest/knowledge/register-source" data-submit-label="登记中...">
          <label>
            source_id
            <input type="text" name="source_id" value="{escape(form_values.get('source_id', ''))}" required />
          </label>
          <label>
            source_title_zh
            <input type="text" name="source_title_zh" value="{escape(form_values.get('source_title_zh', ''))}" required />
          </label>
          <label>
            source_kind
            <select name="source_kind">{kind_options}</select>
          </label>
          <label>
            source_format
            <select name="source_format">{format_options}</select>
          </label>
          <p class="help">先登记一个资料源，再进入详情页补文本内容。</p>
          <button type="submit">新增资料</button>
        </form>
      </section>
      <section class="panel">
        <h2>Knowledge Sources</h2>
        <div class="attention-grid">
          {source_cards}
        </div>
      </section>
    """
    return _render_shell(
        title="准备资料",
        body=body,
        status_code=status_code,
    )


def _render_playtest_knowledge_source_page(
    *,
    source_id: str,
    source: dict[str, Any] | None,
    preview_chunks: list[dict[str, Any]],
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
    ingest_text_value: str = "",
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    source_state = source or {}
    normalized_text_preview = _summarize_text_preview(
        source_state.get("normalized_text") or source_state.get("raw_text"),
        limit=260,
    )
    extraction = source_state.get("character_sheet_extraction") or {}
    summary_text = normalized_text_preview
    if not summary_text and extraction:
        investigator_name = extraction.get("investigator_name") or "未命名调查员"
        occupation = extraction.get("occupation") or "未标注职业"
        skill_count = len(extraction.get("skills") or {})
        summary_text = (
            f"人物卡提取：{investigator_name} / {occupation} / 已识别 {skill_count} 项技能。"
        )
    preview_cards: str
    if preview_chunks:
        cards: list[str] = []
        for chunk in preview_chunks:
            cards.append(
                f"""
                <article class="activity-item">
                  <div class="activity-header">
                    <h3>{escape(str(chunk.get('title_zh') or chunk.get('resolved_topic') or chunk.get('topic_key') or '资料片段'))}</h3>
                  </div>
                  <p>{escape(_summarize_text_preview(chunk.get('content') or chunk.get('text'), limit=180))}</p>
                  {
                      f'<p class="meta-line">引用：{escape(str(chunk.get("short_citation")))}</p>'
                      if chunk.get("short_citation")
                      else ''
                  }
                </article>
                """
            )
        preview_cards = '<div class="recent-list">' + "".join(cards) + "</div>"
    elif extraction:
        preview_cards = f"""
        <div class="recent-list">
          <article class="activity-item">
            <div class="activity-header">
              <h3>人物卡提取结果</h3>
            </div>
            <p>调查员：{escape(str(extraction.get('investigator_name') or '未命名调查员'))}</p>
            <p class="meta-line">职业：{escape(str(extraction.get('occupation') or '未标注职业'))}</p>
            <p class="meta-line">技能数：<span class="mono">{escape(str(len(extraction.get('skills') or {})))}</span></p>
          </article>
        </div>
        """
    else:
        preview_cards = '<p class="empty-state">当前资料还没有可展示的内容预览。</p>'
    ingest_panel = (
        f"""
      <section class="panel">
        <h2>添加文本内容</h2>
        <form method="post" action="/playtest/knowledge/{escape(source_id)}/ingest-text" data-submit-label="入库中...">
          <label>
            content
            <textarea name="content" rows="8" placeholder="贴入要入库的资料文本。">{escape(ingest_text_value)}</textarea>
          </label>
          <p class="help">提交后会更新资料摘要和内容预览。</p>
          <button type="submit">保存文本并入库</button>
        </form>
      </section>
        """
        if source is not None
        else ""
    )
    body = f"""
      <section class="hero">
        <h1>{escape(str(source_state.get('source_title_zh') or source_id or '资料详情'))}</h1>
        <div class="hero-meta">
          <span>source_id: <code>{escape(source_id)}</code></span>
          <span>类型：{escape(_knowledge_kind_label(source_state.get('source_kind')))}</span>
          <span>格式：{escape(_knowledge_format_label(source_state.get('source_format')))}</span>
          <span>状态：{escape(_knowledge_ingest_status_label(source_state.get('ingest_status')))}</span>
        </div>
        <div class="nav-links">
          {_render_session_index_link()}
          {_render_knowledge_index_link("返回资料列表")}
        </div>
      </section>
      {_render_notice(notice)}
      {_render_detail(detail)}
      <section class="panel">
        <h2>资料摘要</h2>
        <div class="summary-grid">
          <article class="summary-card">
            <h3>基本信息</h3>
            <p class="meta-line">source_id: <code>{escape(source_id)}</code></p>
            <p class="meta-line">类型：{escape(_knowledge_kind_label(source_state.get('source_kind')))}</p>
            <p class="meta-line">格式：{escape(_knowledge_format_label(source_state.get('source_format')))}</p>
            <p class="meta-line">规则集：<span class="mono">{escape(str(source_state.get('ruleset') or 'coc7e'))}</span></p>
            <p class="meta-line">chunk_count：<span class="mono">{escape(str(source_state.get('chunk_count', 0)))}</span></p>
            <p class="meta-line">最后更新时间：{escape(_format_datetime(source_state.get('updated_at') or source_state.get('registered_at') or ''))}</p>
          </article>
          <article class="summary-card">
            <h3>摘要</h3>
            {
                f'<p>{escape(summary_text)}</p>'
                if summary_text
                else '<p class="empty-state">当前资料还没有可显示的摘要。</p>'
            }
          </article>
        </div>
      </section>
      {ingest_panel}
      <section class="panel">
        <h2>内容预览</h2>
        {preview_cards}
      </section>
    """
    return _render_shell(
        title=f"知识资料 {source_id}",
        body=body,
        status_code=status_code,
    )


def _playtest_knowledge_detail(exc: BaseException, *, source_id: str) -> dict[str, Any] | str:
    detail = extract_error_detail(exc)
    if isinstance(detail, dict):
        return detail
    return build_knowledge_error_detail(
        code="knowledge_source_not_found",
        message=str(detail),
        scope="knowledge_source_lookup",
        source_id=source_id,
    )


def _playtest_knowledge_form_detail(
    exc: BaseException,
    *,
    code: str,
    scope: str,
    source_id: str,
) -> dict[str, Any] | str:
    if isinstance(exc, ValidationError):
        return _build_validation_detail(exc)
    detail = extract_error_detail(exc)
    if isinstance(detail, dict):
        return detail
    return build_knowledge_error_detail(
        code=code,
        message=str(detail),
        scope=scope,
        source_id=source_id,
    )


def _render_playtest_knowledge_index_from_service(
    *,
    knowledge_service: KnowledgeService,
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
    register_form_values: dict[str, str] | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    return _render_playtest_knowledge_index_page(
        sources=[
            source.model_dump(mode="json")
            for source in knowledge_service.list_sources()
        ],
        notice=notice,
        detail=detail,
        register_form_values=register_form_values,
        status_code=status_code,
    )


def _render_playtest_knowledge_source_from_service(
    *,
    knowledge_service: KnowledgeService,
    source_id: str,
    notice: str | None = None,
    detail: dict[str, Any] | str | None = None,
    ingest_text_value: str = "",
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    try:
        source, preview_chunks = knowledge_service.get_source_preview(source_id, limit=3)
    except LookupError as exc:
        return _render_playtest_knowledge_source_page(
            source_id=source_id,
            source=None,
            preview_chunks=[],
            notice=notice,
            detail=detail or _playtest_knowledge_detail(exc, source_id=source_id),
            ingest_text_value=ingest_text_value,
            status_code=(
                status_code
                if status_code != status.HTTP_200_OK
                else status.HTTP_404_NOT_FOUND
            ),
        )
    return _render_playtest_knowledge_source_page(
        source_id=source_id,
        source=source.model_dump(mode="json"),
        preview_chunks=[chunk.model_dump(mode="json") for chunk in preview_chunks],
        notice=notice,
        detail=detail,
        ingest_text_value=ingest_text_value,
        status_code=status_code,
    )


@router.get("/knowledge", response_class=HTMLResponse)
async def view_playtest_knowledge_index(
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
) -> HTMLResponse:
    return _render_playtest_knowledge_index_from_service(
        knowledge_service=knowledge_service
    )


@router.post("/knowledge/register-source", response_class=HTMLResponse)
async def register_playtest_knowledge_source(
    request: Request,
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
) -> HTMLResponse:
    form = _normalize_register_form_values(await _read_form_payload(request))
    source_id = _normalize_form_text(form.get("source_id")) or ""
    try:
        register_request = _build_playtest_source_registration_request(form)
        result = knowledge_service.register_source(register_request)
        return _render_playtest_knowledge_index_from_service(
            knowledge_service=knowledge_service,
            notice=result.message,
        )
    except (ValidationError, LookupError, ValueError) as exc:
        return _render_playtest_knowledge_index_from_service(
            knowledge_service=knowledge_service,
            detail=_playtest_knowledge_form_detail(
                exc,
                code="knowledge_source_registration_invalid",
                scope="knowledge_source_registration",
                source_id=source_id,
            ),
            register_form_values=form,
            status_code=_playtest_status_for_exception(exc),
        )


@router.get("/knowledge/{source_id}", response_class=HTMLResponse)
async def view_playtest_knowledge_source(
    source_id: str,
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
) -> HTMLResponse:
    return _render_playtest_knowledge_source_from_service(
        knowledge_service=knowledge_service,
        source_id=source_id,
    )


@router.post("/knowledge/{source_id}/ingest-text", response_class=HTMLResponse)
async def ingest_playtest_knowledge_text(
    source_id: str,
    request: Request,
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
) -> HTMLResponse:
    form = await _read_form_payload(request)
    content = form.get("content", "")
    try:
        ingest_request = TextIngestRequest.model_validate(
            {
                "source_id": source_id,
                "content": _normalize_form_text(content) or "",
            }
        )
        result = knowledge_service.ingest_text(ingest_request)
        return _render_playtest_knowledge_source_from_service(
            knowledge_service=knowledge_service,
            source_id=source_id,
            notice=result.message,
        )
    except (ValidationError, LookupError, ValueError) as exc:
        return _render_playtest_knowledge_source_from_service(
            knowledge_service=knowledge_service,
            source_id=source_id,
            detail=_playtest_knowledge_form_detail(
                exc,
                code="knowledge_ingest_text_invalid",
                scope="knowledge_ingest_text",
                source_id=source_id,
            ),
            ingest_text_value=content,
            status_code=_playtest_status_for_exception(exc),
        )
