from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from coc_runner.api.exception_handlers import request_validation_exception_handler
from coc_runner.api.routes.health import router as health_router
from coc_runner.api.routes.knowledge import router as knowledge_router
from coc_runner.api.routes.playtest import router as playtest_router
from coc_runner.api.routes.rules import router as rules_router
from coc_runner.api.routes.sessions import router as sessions_router
from coc_runner.application.dice_execution import (
    DiceStyleExecutionBackend,
    DiceStyleSubprocessClient,
    build_default_dice_style_subprocess_command,
)
from coc_runner.application.knowledge_service import KnowledgeService
from coc_runner.application.session_service import SessionService
from coc_runner.config import Settings, get_settings
from coc_runner.infrastructure.database import Database
from coc_runner.infrastructure.knowledge_repositories import SqlAlchemyKnowledgeRepository
from coc_runner.infrastructure.repositories import SqlAlchemySessionRepository


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    database = Database(runtime_settings.db_url)
    dice_execution_backend = None
    if runtime_settings.dice_backend_mode == "dice_style_subprocess":
        dice_execution_backend = DiceStyleExecutionBackend(
            client=DiceStyleSubprocessClient(
                command=build_default_dice_style_subprocess_command(),
                timeout_seconds=runtime_settings.dice_subprocess_timeout_seconds,
            )
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.create_all()
        session_repository = SqlAlchemySessionRepository(database.session_factory)
        knowledge_repository = SqlAlchemyKnowledgeRepository(database.session_factory)
        app.state.session_service = SessionService(
            session_repository,
            knowledge_repository=knowledge_repository,
            dice_execution_backend=dice_execution_backend,
            default_language=runtime_settings.default_language,
            behavior_memory_limit=runtime_settings.behavior_memory_limit,
        )
        app.state.knowledge_service = KnowledgeService(
            knowledge_repository,
            default_language=runtime_settings.default_language,
        )
        app.state.settings = runtime_settings
        yield

    app = FastAPI(title=runtime_settings.app_name, lifespan=lifespan)
    app.add_exception_handler(
        RequestValidationError,
        request_validation_exception_handler,
    )
    app.include_router(health_router)
    app.include_router(sessions_router)
    app.include_router(playtest_router)
    app.include_router(knowledge_router)
    app.include_router(rules_router)
    return app


app = create_app()
