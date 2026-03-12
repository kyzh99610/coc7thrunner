from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from knowledge.schemas import RuleQueryRequest, RuleQueryResult

from coc_runner.api.dependencies import get_knowledge_service
from coc_runner.application.knowledge_service import KnowledgeService
from coc_runner.error_details import (
    build_rules_query_error_detail,
    extract_error_detail,
)


router = APIRouter(tags=["rules"])


@router.post("/rules/query", response_model=RuleQueryResult, status_code=status.HTTP_200_OK)
def query_rules(
    request: RuleQueryRequest,
    service: KnowledgeService = Depends(get_knowledge_service),
) -> RuleQueryResult:
    try:
        return service.query_rules(request)
    except LookupError as exc:
        detail = extract_error_detail(exc)
        if not isinstance(detail, dict):
            detail = build_rules_query_error_detail(
                code="rules_query_not_found",
                message=str(detail),
                scope="rules_query_lookup",
                query_text=request.query_text,
                viewer_role=request.viewer_role,
                viewer_id=request.viewer_id,
            )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from exc
    except ValueError as exc:
        detail = extract_error_detail(exc)
        if not isinstance(detail, dict):
            detail = build_rules_query_error_detail(
                code="rules_query_invalid",
                message=str(detail),
                scope="rules_query_request",
                query_text=request.query_text,
                viewer_role=request.viewer_role,
                viewer_id=request.viewer_id,
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc
