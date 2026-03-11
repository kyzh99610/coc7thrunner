from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from knowledge.schemas import RuleQueryRequest, RuleQueryResult

from coc_runner.api.dependencies import get_knowledge_service
from coc_runner.application.knowledge_service import KnowledgeService


router = APIRouter(tags=["rules"])


@router.post("/rules/query", response_model=RuleQueryResult, status_code=status.HTTP_200_OK)
def query_rules(
    request: RuleQueryRequest,
    service: KnowledgeService = Depends(get_knowledge_service),
) -> RuleQueryResult:
    try:
        return service.query_rules(request)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
