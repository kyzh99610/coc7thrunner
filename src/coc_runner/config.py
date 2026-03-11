from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel, Field

from coc_runner.domain.models import LanguagePreference


class Settings(BaseModel):
    app_name: str = "CoC Runner MVP"
    db_url: str = Field(default="sqlite:///./data/coc_runner.db")
    default_language: LanguagePreference = LanguagePreference.ZH_CN
    behavior_memory_limit: int = Field(default=5, ge=1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        db_url=os.getenv("COC_RUNNER_DB_URL", "sqlite:///./data/coc_runner.db"),
        default_language=os.getenv("COC_RUNNER_DEFAULT_LANGUAGE", LanguagePreference.ZH_CN),
        behavior_memory_limit=int(os.getenv("COC_RUNNER_BEHAVIOR_MEMORY_LIMIT", "5")),
    )
