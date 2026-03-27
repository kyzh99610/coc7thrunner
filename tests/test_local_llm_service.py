from __future__ import annotations

import os
import shutil
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient

from coc_runner.application.local_llm_service import (
    LocalLLMAssistantRequest,
    LocalLLMService,
    OllamaLocalLLMProvider,
    build_local_llm_generation_restriction,
)
from coc_runner.config import Settings, get_settings
from coc_runner.main import create_app


class _RecordingProvider:
    provider_kind = "openai_compatible"
    provider_name = "stub-local"
    model = "stub-model"

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.called = False
        self.system_prompt = ""
        self.user_prompt = ""
        self.temperature = None
        self.max_output_tokens = None

    def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_output_tokens: int,
    ) -> str:
        self.called = True
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        return self.response_text


class _FailingProvider:
    provider_kind = "openai_compatible"
    provider_name = "stub-local"
    model = "stub-model"

    def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_output_tokens: int,
    ) -> str:
        raise RuntimeError("provider offline")


class _HTTPXResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


def _keeper_request() -> LocalLLMAssistantRequest:
    return LocalLLMAssistantRequest(
        workspace_key="keeper_workspace",
        task_key="situation_summary",
        task_label="当前局势摘要",
        context={"session": {"session_id": "session-1", "status": "active"}},
    )


def _keeper_narrative_request() -> LocalLLMAssistantRequest:
    return LocalLLMAssistantRequest(
        workspace_key="keeper_narrative_scaffolding",
        task_key="scene_framing",
        task_label="下一幕开场建议",
        context={
            "session": {
                "session_id": "session-1",
                "current_scene": "旅店账房",
                "current_beat_title": "核对账房记录",
            }
        },
    )


def _experimental_ai_kp_demo_request() -> LocalLLMAssistantRequest:
    return LocalLLMAssistantRequest(
        workspace_key="experimental_ai_kp_demo",
        task_key="demo_loop",
        task_label="AI KP 剧情支架提案",
        context={
            "session": {
                "session_id": "session-1",
                "current_scene": "旅店账房",
            },
            "compressed_context": {
                "situation_summary": "账房里旧账册、缺页记录和老板的回避构成当前压力。",
                "next_focus": ["确认 204 房缺页记录。"],
            },
        },
    )


def _experimental_ai_investigator_demo_request() -> LocalLLMAssistantRequest:
    return LocalLLMAssistantRequest(
        workspace_key="experimental_ai_investigator_demo",
        task_key="demo_loop",
        task_label="AI Investigator 行动提案",
        context={
            "viewer": {"actor_id": "investigator-1", "display_name": "林舟"},
            "session": {"session_id": "session-1", "current_scene": "旅店账房"},
            "visible_clues": [{"title": "旧账册缺页", "summary": "204 房记录被抽走。"}],
            "recent_events": [{"event_type": "player_action", "text": "调查员已进入账房。"}],
        },
    )


def _experimental_ai_keeper_continuity_draft_request() -> LocalLLMAssistantRequest:
    return LocalLLMAssistantRequest(
        workspace_key="experimental_ai_keeper_continuity_draft",
        task_key="draft_bridge",
        task_label="Keeper continuity bridge 草稿",
        context={
            "compressed_context": {
                "situation_summary": "账房里的缺页记录和二楼动静构成当前压力。",
                "next_focus": ["确认 204 房登记与楼上动静的关系。"],
            },
            "current_ai_kp_output": {
                "title": "AI KP 剧情支架提案",
                "summary": "建议把老板的回避和二楼压力一起立起来。",
            },
            "current_ai_investigator_output": {
                "title": "AI Investigator 行动提案",
                "summary": "调查员会先追问 204 房登记。",
            },
        },
    )


def _experimental_ai_visible_continuity_draft_request() -> LocalLLMAssistantRequest:
    return LocalLLMAssistantRequest(
        workspace_key="experimental_ai_visible_continuity_draft",
        task_key="draft_bridge",
        task_label="Visible continuity bridge 草稿",
        context={
            "viewer": {"actor_id": "investigator-1", "display_name": "林舟"},
            "session": {"session_id": "session-1", "current_scene": "旅店账房"},
            "current_ai_investigator_output": {
                "title": "AI Investigator 行动提案",
                "summary": "先确认缺页，再试探老板对 204 房的反应。",
            },
            "recent_events": [{"event_type": "player_action", "text": "调查员注意到账册缺页。"}],
        },
    )


def test_local_llm_service_disabled_short_circuits_provider() -> None:
    provider = _RecordingProvider(
        '{"title":"t","summary":"s","bullets":[],"suggested_questions":[],"draft_text":null,"safety_notes":[]}'
    )
    service = LocalLLMService(provider, enabled=False)

    result = service.generate_assistant(_keeper_request())

    assert result.status == "disabled"
    assert provider.called is False
    assert result.assistant is None
    assert "未启用" in str(result.error_message)


def test_local_llm_service_provider_failure_returns_unavailable() -> None:
    service = LocalLLMService(_FailingProvider(), enabled=True)

    result = service.generate_assistant(_keeper_request())

    assert result.status == "unavailable"
    assert result.assistant is None
    assert "provider offline" in str(result.error_message)


def test_local_llm_service_parses_structured_json_response() -> None:
    provider = _RecordingProvider(
        """
        ```json
        {
          "title": "Keeper 草稿",
          "summary": "这是非权威的 Keeper 辅助摘要。",
          "bullets": ["当前局势处于可继续推进状态。"],
          "suggested_questions": ["是否需要先处理待办提示？"],
          "draft_text": "可先把焦点放回账房场景。",
          "draft_kind": "prompt_note_draft",
          "suggested_target": "prompt_note",
          "source_context_label": "基于当前 keeper workspace 摘要与待处理 prompts。",
          "safety_notes": ["战斗和伤势仍需现有规则链裁定。"]
        }
        ```
        """
    )
    service = LocalLLMService(provider, enabled=True)

    result = service.generate_assistant(_keeper_request())

    assert result.status == "success"
    assert result.assistant is not None
    assert result.assistant.title == "Keeper 草稿"
    assert "非权威" in result.assistant.summary
    assert result.assistant.draft_kind == "prompt_note_draft"
    assert result.assistant.suggested_target == "prompt_note"
    assert result.assistant.source_context_label == "基于当前 keeper workspace 摘要与待处理 prompts。"
    assert result.provider_name == "stub-local"
    assert result.model == "stub-model"
    assert provider.called is True
    assert "你是一个本地、可选、非权威的跑团辅助模型" in provider.system_prompt
    assert "当前任务：当前局势摘要" in provider.user_prompt


def test_local_llm_service_supports_keeper_narrative_prompt_template() -> None:
    provider = _RecordingProvider(
        """
        {
          "title": "场景建议",
          "summary": "这是非权威的剧情支架建议。",
          "bullets": ["先把账房压迫感立起来。"],
          "suggested_questions": ["要不要先让秦老板回避正面回答？"],
          "draft_text": "开场可先把潮气和旧账册一起摆出来。",
          "draft_kind": "scene_framing_note_draft",
          "suggested_target": "narrative_work_note",
          "source_context_label": "基于当前 keeper workspace：旅店账房 / 核对账房记录。",
          "safety_notes": ["不会自动推进剧情。"]
        }
        """
    )
    service = LocalLLMService(provider, enabled=True)

    result = service.generate_assistant(_keeper_narrative_request())

    assert result.status == "success"
    assert result.assistant is not None
    assert result.assistant.draft_kind == "scene_framing_note_draft"
    assert result.assistant.suggested_target == "narrative_work_note"
    assert "你正在协助 Keeper 的 narrative scaffolding" in provider.user_prompt
    assert "当前任务：下一幕开场建议" in provider.user_prompt


def test_local_llm_service_supports_experimental_ai_kp_demo_prompt_template() -> None:
    provider = _RecordingProvider(
        """
        {
          "title": "AI KP 剧情支架提案",
          "summary": "这是 experimental / non-authoritative 的 AI KP 候选叙事输出。",
          "bullets": ["先立起账房压迫感。"],
          "suggested_questions": ["要不要先把老板的回避写出来？"],
          "draft_text": "可先从账房里的潮气和旧账册开场。",
          "source_context_label": "基于当前 keeper-side compressed context 与近期事件摘要。",
          "safety_notes": ["不会自动推进 session。"]
        }
        """
    )
    service = LocalLLMService(provider, enabled=True)

    result = service.generate_assistant(_experimental_ai_kp_demo_request())

    assert result.status == "success"
    assert result.assistant is not None
    assert "isolated experimental AI KP demo harness" in provider.user_prompt
    assert "当前任务：AI KP 剧情支架提案" in provider.user_prompt


def test_local_llm_service_supports_experimental_ai_investigator_demo_prompt_template() -> None:
    provider = _RecordingProvider(
        """
        {
          "title": "AI Investigator 行动提案",
          "summary": "这是 experimental / non-authoritative 的调查员行动提案。",
          "bullets": ["先确认 204 房缺页编号。"],
          "suggested_questions": ["老板为什么回避 204 房？"],
          "draft_text": "调查员会先检查缺页，再顺势追问老板。",
          "source_context_label": "基于林舟的可见状态摘要。",
          "safety_notes": ["不会自动执行检定或推进状态。"]
        }
        """
    )
    service = LocalLLMService(provider, enabled=True)

    result = service.generate_assistant(_experimental_ai_investigator_demo_request())

    assert result.status == "success"
    assert result.assistant is not None
    assert "isolated experimental AI investigator demo harness" in provider.user_prompt
    assert "当前任务：AI Investigator 行动提案" in provider.user_prompt


def test_local_llm_service_supports_experimental_keeper_continuity_draft_prompt_template() -> None:
    provider = _RecordingProvider(
        """
        {
          "title": "Keeper continuity bridge 草稿",
          "summary": "这是 experimental / non-authoritative 的 keeper continuity 草稿。",
          "bullets": ["保留老板回避 204 房登记这条内部压力。"],
          "suggested_questions": ["下一轮是否要把压力推向二楼动静？"],
          "draft_text": "可先把老板回避与二楼脚步声并列成下一轮的 keeper continuity。",
          "source_context_label": "基于当前实验轮的 AI KP / AI investigator 输出与 keeper-side compressed context。",
          "safety_notes": ["仅当前页临时使用。", "不会自动进入下一轮。"]
        }
        """
    )
    service = LocalLLMService(provider, enabled=True)

    result = service.generate_assistant(_experimental_ai_keeper_continuity_draft_request())

    assert result.status == "success"
    assert result.assistant is not None
    assert "isolated experimental keeper continuity drafting harness" in provider.user_prompt
    assert "当前任务：Keeper continuity bridge 草稿" in provider.user_prompt


def test_local_llm_service_supports_experimental_visible_continuity_draft_prompt_template() -> None:
    provider = _RecordingProvider(
        """
        {
          "title": "Visible continuity bridge 草稿",
          "summary": "这是 experimental / non-authoritative 的 visible continuity 草稿。",
          "bullets": ["老板回避 204 房登记，调查员注意到账册缺页。"],
          "suggested_questions": ["是否继续追问 204 房？"],
          "draft_text": "调查员目前只确认了账册缺页和老板的回避，下一轮可继续沿 204 房追问。",
          "source_context_label": "基于当前调查员可见状态摘要与本轮公开行动提案。",
          "safety_notes": ["仅当前页临时使用。", "不会自动进入下一轮。"]
        }
        """
    )
    service = LocalLLMService(provider, enabled=True)

    result = service.generate_assistant(_experimental_ai_visible_continuity_draft_request())

    assert result.status == "success"
    assert result.assistant is not None
    assert "isolated experimental visible continuity drafting harness" in provider.user_prompt
    assert "当前任务：Visible continuity bridge 草稿" in provider.user_prompt


def test_build_local_llm_generation_restriction_for_ollama_stays_small_and_experimental_only() -> None:
    restriction = build_local_llm_generation_restriction(
        provider_kind="ollama",
        requested_temperature=0.9,
        requested_max_output_tokens=1200,
    )

    assert restriction.provider_kind == "ollama"
    assert restriction.temperature == 0.2
    assert restriction.max_output_tokens == 700
    assert restriction.structured_output_required is True
    assert restriction.fallback_mode == "return_unavailable"
    assert restriction.allowed_workspace_keys == {
        "experimental_ai_kp_demo",
        "experimental_ai_investigator_demo",
        "experimental_ai_keeper_continuity_draft",
        "experimental_ai_visible_continuity_draft",
    }
    assert "keeper_workspace" not in restriction.allowed_workspace_keys
    assert "knowledge_detail" not in restriction.allowed_workspace_keys
    assert "session_recap" not in restriction.allowed_workspace_keys


def test_local_llm_service_ollama_restriction_blocks_non_experimental_workspace_with_fallback() -> None:
    provider = _RecordingProvider(
        '{"title":"t","summary":"s","bullets":[],"suggested_questions":[],"draft_text":null,"safety_notes":[]}'
    )
    provider.provider_kind = "ollama"
    provider.provider_name = "ollama_local"
    service = LocalLLMService(provider, enabled=True)

    result = service.generate_assistant(_keeper_request())

    assert result.status == "unavailable"
    assert result.assistant is None
    assert provider.called is False
    assert "仅开放给 experimental AI demo / continuity 受控路径" in str(
        result.error_message
    )


def test_local_llm_service_ollama_restriction_clamps_generation_args_for_experimental_workspace() -> None:
    provider = _RecordingProvider(
        """
        {
          "title": "AI KP 剧情支架提案",
          "summary": "这是 experimental / non-authoritative 的 AI KP 候选叙事输出。",
          "bullets": ["先立起账房压迫感。"],
          "suggested_questions": ["要不要先把老板的回避写出来？"],
          "draft_text": "可先从账房里的潮气和旧账册开场。",
          "source_context_label": "基于当前 keeper-side compressed context 与近期事件摘要。",
          "safety_notes": ["不会自动推进 session。"]
        }
        """
    )
    provider.provider_kind = "ollama"
    provider.provider_name = "ollama_local"
    service = LocalLLMService(
        provider,
        enabled=True,
        temperature=0.9,
        max_output_tokens=1200,
    )

    result = service.generate_assistant(_experimental_ai_kp_demo_request())

    assert result.status == "success"
    assert provider.called is True
    assert provider.temperature == 0.2
    assert provider.max_output_tokens == 700


def test_ollama_local_llm_provider_normalizes_base_url_and_uses_chat_completions_api() -> None:
    recorded: dict[str, object] = {}

    def _fake_post(url: str, **kwargs):
        recorded["url"] = url
        recorded["headers"] = kwargs["headers"]
        recorded["json"] = kwargs["json"]
        recorded["timeout"] = kwargs["timeout"]
        return _HTTPXResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"title":"t","summary":"s","bullets":[],"suggested_questions":[],"draft_text":null,"safety_notes":[]}'
                        }
                    }
                ]
            }
        )

    provider = OllamaLocalLLMProvider(
        base_url="http://127.0.0.1:11434",
        model="qwen2.5:7b",
        timeout_seconds=12.5,
    )

    with patch("coc_runner.application.local_llm_service.httpx.post", side_effect=_fake_post):
        raw_output = provider.generate_text(
            system_prompt="system",
            user_prompt="user",
            temperature=0.2,
            max_output_tokens=512,
        )

    assert provider.provider_name == "ollama_local"
    assert provider.provider_kind == "ollama"
    assert provider.base_url == "http://127.0.0.1:11434/v1"
    assert raw_output.startswith("{")
    assert recorded["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert recorded["timeout"] == 12.5
    assert recorded["json"] == {
        "model": "qwen2.5:7b",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
        "temperature": 0.2,
        "max_tokens": 512,
    }
    assert "Authorization" not in recorded["headers"]


def test_get_settings_reads_ollama_provider_and_output_cap_from_env() -> None:
    try:
        with patch.dict(
            os.environ,
            {
                "COC_RUNNER_LOCAL_LLM_ENABLED": "1",
                "COC_RUNNER_LOCAL_LLM_PROVIDER": "ollama",
                "COC_RUNNER_LOCAL_LLM_BASE_URL": "http://127.0.0.1:11434",
                "COC_RUNNER_LOCAL_LLM_MODEL": "qwen2.5:7b",
                "COC_RUNNER_LOCAL_LLM_TIMEOUT_SECONDS": "12.5",
                "COC_RUNNER_LOCAL_LLM_MAX_OUTPUT_TOKENS": "640",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            settings = get_settings()
    finally:
        get_settings.cache_clear()

    assert settings.local_llm_enabled is True
    assert settings.local_llm_provider == "ollama"
    assert settings.local_llm_base_url == "http://127.0.0.1:11434"
    assert settings.local_llm_model == "qwen2.5:7b"
    assert settings.local_llm_timeout_seconds == 12.5
    assert settings.local_llm_max_output_tokens == 640


def test_create_app_builds_ollama_local_llm_service_from_settings() -> None:
    base_dir = Path("test-artifacts")
    base_dir.mkdir(exist_ok=True)
    run_dir = base_dir / f"ollama_local_llm_service_{uuid4().hex}"
    run_dir.mkdir()
    db_path = run_dir / "ollama-app.db"
    try:
        app = create_app(
            Settings(
                db_url=f"sqlite:///{db_path.as_posix()}",
                local_llm_enabled=True,
                local_llm_provider="ollama",
                local_llm_base_url="http://127.0.0.1:11434",
                local_llm_model="qwen2.5:7b",
                local_llm_max_output_tokens=1200,
            )
        )

        with TestClient(app):
            service = app.state.local_llm_service

        assert isinstance(service.provider, OllamaLocalLLMProvider)
        assert service.provider.base_url == "http://127.0.0.1:11434/v1"
        assert service.max_output_tokens == 700
        assert service.generation_restriction.provider_kind == "ollama"
        assert "keeper_workspace" not in service.generation_restriction.allowed_workspace_keys
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
