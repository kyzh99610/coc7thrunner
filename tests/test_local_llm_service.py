from __future__ import annotations

from coc_runner.application.local_llm_service import (
    LocalLLMAssistantRequest,
    LocalLLMService,
)


class _RecordingProvider:
    provider_name = "stub-local"
    model = "stub-model"

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.called = False
        self.system_prompt = ""
        self.user_prompt = ""

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
        return self.response_text


class _FailingProvider:
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


def _keeper_request() -> LocalLLMAssistantRequest:
    return LocalLLMAssistantRequest(
        workspace_key="keeper_workspace",
        task_key="situation_summary",
        task_label="当前局势摘要",
        context={"session": {"session_id": "session-1", "status": "active"}},
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
          "suggested_target": "prompt_note",
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
    assert result.assistant.suggested_target == "prompt_note"
    assert result.provider_name == "stub-local"
    assert result.model == "stub-model"
    assert provider.called is True
    assert "你是一个本地、可选、非权威的跑团辅助模型" in provider.system_prompt
    assert "当前任务：当前局势摘要" in provider.user_prompt
