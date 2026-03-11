from __future__ import annotations

from fastapi import Request

from coc_runner.application.knowledge_service import KnowledgeService
from coc_runner.application.session_service import SessionService


def get_session_service(request: Request) -> SessionService:
    return request.app.state.session_service


def get_knowledge_service(request: Request) -> KnowledgeService:
    return request.app.state.knowledge_service
