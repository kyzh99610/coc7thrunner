from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from coc_runner.domain.models import LanguagePreference


PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts" / "local_llm"
NON_AUTHORITATIVE_DISCLAIMER = (
    "仅供摘要、解释、建议或草稿参考，不会直接修改 authoritative state。"
)
OLLAMA_MAX_OUTPUT_TOKENS_CAP = 700
OLLAMA_ALLOWED_WORKSPACE_KEYS = (
    "experimental_ai_kp_demo",
    "experimental_ai_investigator_demo",
    "experimental_ai_keeper_continuity_draft",
    "experimental_ai_visible_continuity_draft",
)
LocalLLMProviderKind = Literal["openai_compatible", "ollama"]
LocalLLMWorkspaceKey = Literal[
    "keeper_workspace",
    "keeper_narrative_scaffolding",
    "knowledge_detail",
    "session_recap",
    "experimental_ai_kp_demo",
    "experimental_ai_investigator_demo",
    "experimental_ai_keeper_continuity_draft",
    "experimental_ai_visible_continuity_draft",
]
ALL_LOCAL_LLM_WORKSPACE_KEYS: tuple[LocalLLMWorkspaceKey, ...] = (
    "keeper_workspace",
    "keeper_narrative_scaffolding",
    "knowledge_detail",
    "session_recap",
    "experimental_ai_kp_demo",
    "experimental_ai_investigator_demo",
    "experimental_ai_keeper_continuity_draft",
    "experimental_ai_visible_continuity_draft",
)


@dataclass(frozen=True, slots=True)
class LocalLLMGenerationRestriction:
    provider_kind: LocalLLMProviderKind
    allowed_workspace_keys: frozenset[LocalLLMWorkspaceKey]
    temperature: float
    max_output_tokens: int
    structured_output_required: bool = True
    fallback_mode: Literal["return_unavailable"] = "return_unavailable"


def build_local_llm_generation_restriction(
    *,
    provider_kind: LocalLLMProviderKind,
    requested_temperature: float,
    requested_max_output_tokens: int,
) -> LocalLLMGenerationRestriction:
    normalized_temperature = max(0.0, float(requested_temperature))
    normalized_max_output_tokens = max(1, int(requested_max_output_tokens))
    if provider_kind == "ollama":
        return LocalLLMGenerationRestriction(
            provider_kind="ollama",
            allowed_workspace_keys=frozenset(OLLAMA_ALLOWED_WORKSPACE_KEYS),
            temperature=0.2,
            max_output_tokens=min(
                normalized_max_output_tokens,
                OLLAMA_MAX_OUTPUT_TOKENS_CAP,
            ),
        )
    return LocalLLMGenerationRestriction(
        provider_kind="openai_compatible",
        allowed_workspace_keys=frozenset(ALL_LOCAL_LLM_WORKSPACE_KEYS),
        temperature=normalized_temperature,
        max_output_tokens=normalized_max_output_tokens,
    )


class LocalLLMAssistantRequest(BaseModel):
    workspace_key: LocalLLMWorkspaceKey
    task_key: str = Field(min_length=1, max_length=80)
    task_label: str = Field(min_length=1, max_length=120)
    context: dict[str, Any]
    language_preference: LanguagePreference = LanguagePreference.ZH_CN


class LocalLLMAssistantPayload(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=1200)
    bullets: list[str] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list)
    draft_text: str | None = Field(default=None, max_length=2400)
    draft_kind: str | None = Field(default=None, max_length=80)
    suggested_target: str | None = Field(default=None, max_length=80)
    source_context_label: str | None = Field(default=None, max_length=160)
    safety_notes: list[str] = Field(default_factory=list)

    @field_validator("bullets", "suggested_questions", "safety_notes", mode="before")
    @classmethod
    def _normalize_text_list(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            text = " ".join(str(item or "").split())
            if text:
                normalized.append(text)
        return normalized[:5]

    @field_validator("suggested_target", mode="before")
    @classmethod
    def _normalize_suggested_target(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = " ".join(str(value).split())
        return normalized or None

    @field_validator("draft_kind", "source_context_label", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = " ".join(str(value).split())
        return normalized or None


class LocalLLMAssistantResult(BaseModel):
    status: Literal["disabled", "unavailable", "success"]
    workspace_key: str
    task_key: str
    task_label: str
    assistant: LocalLLMAssistantPayload | None = None
    provider_name: str | None = None
    model: str | None = None
    error_message: str | None = None
    non_authoritative: bool = True
    disclaimer: str = NON_AUTHORITATIVE_DISCLAIMER


class LocalLLMProvider(Protocol):
    provider_kind: LocalLLMProviderKind
    provider_name: str
    model: str

    def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_output_tokens: int,
    ) -> str: ...


class OpenAICompatibleLocalLLMProvider:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.provider_kind = "openai_compatible"
        self.provider_name = "openai_compatible_local"

    def _build_request_payload(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }

    def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_output_tokens: int,
    ) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=self._build_request_payload(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            ),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise ValueError("local llm returned empty content")
        return content


def _normalize_ollama_chat_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        normalized = normalized[: -len("/chat/completions")]
    if not normalized.endswith("/v1"):
        normalized = f"{normalized}/v1"
    return normalized


class OllamaLocalLLMProvider(OpenAICompatibleLocalLLMProvider):
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        super().__init__(
            base_url=_normalize_ollama_chat_base_url(base_url),
            model=model,
            api_key=None,
            timeout_seconds=timeout_seconds,
        )
        self.provider_kind = "ollama"
        self.provider_name = "ollama_local"

    def _build_request_payload(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        payload = super()._build_request_payload(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        payload["response_format"] = {"type": "json_object"}
        payload["reasoning_effort"] = "none"
        return payload


class LocalLLMService:
    def __init__(
        self,
        provider: LocalLLMProvider | None = None,
        *,
        enabled: bool = False,
        configuration_error: str | None = None,
        temperature: float = 0.2,
        max_output_tokens: int = 700,
        generation_restriction: LocalLLMGenerationRestriction | None = None,
    ) -> None:
        self.provider = provider
        self.enabled = enabled
        self.configuration_error = configuration_error
        provider_kind = getattr(provider, "provider_kind", "openai_compatible")
        self.generation_restriction = generation_restriction or build_local_llm_generation_restriction(
            provider_kind=provider_kind,
            requested_temperature=temperature,
            requested_max_output_tokens=max_output_tokens,
        )
        self.temperature = self.generation_restriction.temperature
        self.max_output_tokens = self.generation_restriction.max_output_tokens

    def generate_assistant(
        self,
        request: LocalLLMAssistantRequest,
    ) -> LocalLLMAssistantResult:
        result_base = {
            "workspace_key": request.workspace_key,
            "task_key": request.task_key,
            "task_label": request.task_label,
        }
        if not self.enabled:
            return LocalLLMAssistantResult(
                status="disabled",
                error_message="当前未启用本地 LLM；主流程不依赖它。",
                **result_base,
            )
        if self.provider is None:
            return LocalLLMAssistantResult(
                status="unavailable",
                error_message=(
                    self.configuration_error
                    or "本地 LLM 已启用，但当前 provider 不可用。"
                ),
                **result_base,
            )
        if request.workspace_key not in self.generation_restriction.allowed_workspace_keys:
            return LocalLLMAssistantResult(
                status="unavailable",
                error_message=self._restriction_error_message(request.workspace_key),
                provider_name=getattr(self.provider, "provider_name", None),
                model=getattr(self.provider, "model", None),
                **result_base,
            )
        try:
            system_prompt, user_prompt = self._build_prompts(request)
            raw_output = self.provider.generate_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=self.temperature,
                max_output_tokens=self.max_output_tokens,
            )
            assistant = self._parse_assistant_payload(raw_output)
            return LocalLLMAssistantResult(
                status="success",
                assistant=assistant,
                provider_name=self.provider.provider_name,
                model=self.provider.model,
                **result_base,
            )
        except Exception as exc:  # pragma: no cover - exercised via tests with stubs
            return LocalLLMAssistantResult(
                status="unavailable",
                error_message=self._error_message(exc),
                provider_name=getattr(self.provider, "provider_name", None),
                model=getattr(self.provider, "model", None),
                **result_base,
            )

    def _build_prompts(
        self,
        request: LocalLLMAssistantRequest,
    ) -> tuple[str, str]:
        system_prompt = _load_prompt_template("system_prompt.txt")
        workspace_template = _load_prompt_template(
            {
                "keeper_workspace": "keeper_assistant_user_prompt.txt",
                "keeper_narrative_scaffolding": "keeper_narrative_scaffolding_user_prompt.txt",
                "knowledge_detail": "knowledge_assistant_user_prompt.txt",
                "session_recap": "recap_assistant_user_prompt.txt",
                "experimental_ai_kp_demo": "experimental_ai_kp_demo_user_prompt.txt",
                "experimental_ai_investigator_demo": "experimental_ai_investigator_demo_user_prompt.txt",
                "experimental_ai_keeper_continuity_draft": "experimental_ai_keeper_continuity_draft_user_prompt.txt",
                "experimental_ai_visible_continuity_draft": "experimental_ai_visible_continuity_draft_user_prompt.txt",
            }[request.workspace_key]
        )
        context_json = json.dumps(
            request.context,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        user_prompt = (
            workspace_template.replace("{{task_label}}", request.task_label).replace(
                "{{context_json}}",
                context_json,
            )
        )
        return system_prompt, user_prompt

    def _restriction_error_message(self, workspace_key: LocalLLMWorkspaceKey) -> str:
        if self.generation_restriction.provider_kind == "ollama":
            return (
                "当前本地 Ollama 仅开放给 experimental AI demo / continuity 受控路径；"
                "keeper workspace、knowledge、recap 等入口保持 fallback/no-op。"
            )
        return f"当前本地 LLM 配置不允许工作区 {workspace_key}。"

    @staticmethod
    def _parse_assistant_payload(raw_output: str) -> LocalLLMAssistantPayload:
        normalized = raw_output.strip()
        if normalized.startswith("```"):
            lines = normalized.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            normalized = "\n".join(lines).strip()
        json_start = normalized.find("{")
        json_end = normalized.rfind("}")
        if json_start == -1 or json_end == -1 or json_end < json_start:
            raise ValueError("local llm did not return a JSON object")
        return LocalLLMAssistantPayload.model_validate(
            json.loads(normalized[json_start : json_end + 1])
        )

    @staticmethod
    def _error_message(exc: Exception) -> str:
        if isinstance(exc, ValidationError):
            return "本地 LLM 返回的结构化内容无效。"
        if isinstance(exc, httpx.TimeoutException):
            return "本地 LLM 调用超时。"
        if isinstance(exc, httpx.HTTPStatusError):
            try:
                payload = exc.response.json()
            except ValueError:
                payload = None
            error_value = payload.get("error") if isinstance(payload, dict) else None
            error_message = ""
            if isinstance(error_value, dict):
                error_message = " ".join(str(error_value.get("message") or "").split())
            elif isinstance(error_value, str):
                error_message = " ".join(error_value.split())
            if error_message:
                lowered = error_message.lower()
                if "model" in lowered and "not found" in lowered:
                    return f"本地 LLM 模型不可用：{error_message}"
                return f"本地 LLM HTTP 调用失败：{error_message}"
            return f"本地 LLM HTTP 调用失败：{exc.response.status_code}"
        if isinstance(exc, httpx.RequestError):
            return "本地 LLM 当前不可连接。"
        message = " ".join(str(exc).split())
        return message or "本地 LLM 当前不可用。"


@lru_cache(maxsize=None)
def _load_prompt_template(file_name: str) -> str:
    return (PROMPT_DIR / file_name).read_text(encoding="utf-8")
