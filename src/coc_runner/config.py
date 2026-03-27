from __future__ import annotations

from dataclasses import dataclass
import os
from functools import lru_cache
import json
from typing import Literal

from pydantic import BaseModel, Field

from coc_runner.domain.models import LanguagePreference

OLLAMA_DEFAULT_MODEL = "qwen3:14b"
OLLAMA_QUALITY_MODEL = "qwen3:30b"
OLLAMA_FALLBACK_MODEL = "gemma3:12b"
OLLAMA_BLOCKED_RUNTIME_DEFAULT_PREFIXES = ("qwen3-coder-next",)


@dataclass(frozen=True, slots=True)
class OllamaModelSelection:
    selected_model: str
    selection_reason: Literal[
        "default_qwen3_14b",
        "configured_model",
        "blocked_runtime_default",
    ]
    default_model: str = OLLAMA_DEFAULT_MODEL
    quality_model: str = OLLAMA_QUALITY_MODEL
    fallback_model: str = OLLAMA_FALLBACK_MODEL


def resolve_ollama_model_selection(configured_model: str | None) -> OllamaModelSelection:
    normalized = " ".join(str(configured_model or "").split())
    lowered = normalized.lower()
    if not normalized or lowered == "local-model":
        return OllamaModelSelection(
            selected_model=OLLAMA_DEFAULT_MODEL,
            selection_reason="default_qwen3_14b",
        )
    if any(lowered.startswith(prefix) for prefix in OLLAMA_BLOCKED_RUNTIME_DEFAULT_PREFIXES):
        return OllamaModelSelection(
            selected_model=OLLAMA_DEFAULT_MODEL,
            selection_reason="blocked_runtime_default",
        )
    return OllamaModelSelection(
        selected_model=normalized,
        selection_reason="configured_model",
    )


class Settings(BaseModel):
    app_name: str = "CoC Runner MVP"
    db_url: str = Field(default="sqlite:///./data/coc_runner.db")
    default_language: LanguagePreference = LanguagePreference.ZH_CN
    behavior_memory_limit: int = Field(default=5, ge=1)
    local_llm_enabled: bool = False
    local_llm_provider: Literal["openai_compatible", "ollama"] = "openai_compatible"
    local_llm_base_url: str | None = None
    local_llm_model: str = Field(default="local-model", min_length=1)
    local_llm_api_key: str | None = None
    local_llm_timeout_seconds: float = Field(default=10.0, gt=0)
    local_llm_max_output_tokens: int = Field(default=700, ge=1, le=4000)
    dice_backend_mode: Literal["local", "dice_style_subprocess"] = "local"
    dice_subprocess_timeout_seconds: float = Field(default=3.0, gt=0)
    dice_style_provider_command: tuple[str, ...] = ()

    @property
    def ollama_model_selection(self) -> OllamaModelSelection:
        return resolve_ollama_model_selection(self.local_llm_model)

    @property
    def local_llm_runtime_model(self) -> str:
        if self.local_llm_provider == "ollama":
            return self.ollama_model_selection.selected_model
        return self.local_llm_model


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    dice_style_provider_command_raw = os.getenv("COC_RUNNER_DICE_STYLE_PROVIDER_COMMAND_JSON")
    dice_style_provider_command = ()
    if dice_style_provider_command_raw:
        parsed_provider_command = json.loads(dice_style_provider_command_raw)
        if not isinstance(parsed_provider_command, list) or not all(
            isinstance(part, str) and part for part in parsed_provider_command
        ):
            raise ValueError("COC_RUNNER_DICE_STYLE_PROVIDER_COMMAND_JSON must be a JSON string array")
        dice_style_provider_command = tuple(parsed_provider_command)
    return Settings(
        db_url=os.getenv("COC_RUNNER_DB_URL", "sqlite:///./data/coc_runner.db"),
        default_language=os.getenv("COC_RUNNER_DEFAULT_LANGUAGE", LanguagePreference.ZH_CN),
        behavior_memory_limit=int(os.getenv("COC_RUNNER_BEHAVIOR_MEMORY_LIMIT", "5")),
        local_llm_enabled=os.getenv("COC_RUNNER_LOCAL_LLM_ENABLED", "").strip().lower()
        in {"1", "true", "yes", "on"},
        local_llm_provider=os.getenv(
            "COC_RUNNER_LOCAL_LLM_PROVIDER",
            "openai_compatible",
        ),
        local_llm_base_url=os.getenv("COC_RUNNER_LOCAL_LLM_BASE_URL") or None,
        local_llm_model=os.getenv("COC_RUNNER_LOCAL_LLM_MODEL", "local-model"),
        local_llm_api_key=os.getenv("COC_RUNNER_LOCAL_LLM_API_KEY") or None,
        local_llm_timeout_seconds=float(
            os.getenv("COC_RUNNER_LOCAL_LLM_TIMEOUT_SECONDS", "10.0")
        ),
        local_llm_max_output_tokens=int(
            os.getenv("COC_RUNNER_LOCAL_LLM_MAX_OUTPUT_TOKENS", "700")
        ),
        dice_backend_mode=os.getenv("COC_RUNNER_DICE_BACKEND_MODE", "local"),
        dice_subprocess_timeout_seconds=float(
            os.getenv("COC_RUNNER_DICE_SUBPROCESS_TIMEOUT_SECONDS", "3.0")
        ),
        dice_style_provider_command=dice_style_provider_command,
    )
