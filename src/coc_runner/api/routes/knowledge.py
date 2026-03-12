from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from knowledge.schemas import (
    CharacterSheetImportRequest,
    CharacterSheetImportResponse,
    FileIngestRequest,
    FileIngestResponse,
    KnowledgeSourceRegistration,
    KnowledgeSourceResponse,
    KnowledgeSourceState,
    TextIngestRequest,
    TextIngestResponse,
)

from coc_runner.api.dependencies import get_knowledge_service
from coc_runner.application.knowledge_service import KnowledgeService
from coc_runner.error_details import build_knowledge_error_detail, extract_error_detail


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _knowledge_detail(
    exc: BaseException,
    *,
    code: str,
    scope: str,
    source_id: str,
) -> dict[str, object] | str:
    detail = extract_error_detail(exc)
    if isinstance(detail, dict):
        return detail
    return build_knowledge_error_detail(
        code=code,
        message=str(detail),
        scope=scope,
        source_id=source_id,
    )


@router.post(
    "/register-source",
    response_model=KnowledgeSourceResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_source(
    request: KnowledgeSourceRegistration,
    service: KnowledgeService = Depends(get_knowledge_service),
) -> KnowledgeSourceResponse:
    try:
        return service.register_source(request)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_knowledge_detail(
                exc,
                code="knowledge_source_not_found",
                scope="knowledge_source_lookup",
                source_id=request.source_id,
            ),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_knowledge_detail(
                exc,
                code="knowledge_source_registration_invalid",
                scope="knowledge_source_registration",
                source_id=request.source_id,
            ),
        ) from exc


@router.post(
    "/ingest-text",
    response_model=TextIngestResponse,
    status_code=status.HTTP_200_OK,
)
def ingest_text(
    request: TextIngestRequest,
    service: KnowledgeService = Depends(get_knowledge_service),
) -> TextIngestResponse:
    try:
        return service.ingest_text(request)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_knowledge_detail(
                exc,
                code="knowledge_source_not_found",
                scope="knowledge_source_lookup",
                source_id=request.source_id,
            ),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_knowledge_detail(
                exc,
                code="knowledge_ingest_text_invalid",
                scope="knowledge_ingest_text",
                source_id=request.source_id,
            ),
        ) from exc


@router.post(
    "/ingest-file",
    response_model=FileIngestResponse,
    status_code=status.HTTP_200_OK,
)
def ingest_file(
    request: FileIngestRequest,
    service: KnowledgeService = Depends(get_knowledge_service),
) -> FileIngestResponse:
    try:
        return service.ingest_file(request)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_knowledge_detail(
                exc,
                code="knowledge_source_not_found",
                scope="knowledge_source_lookup",
                source_id=request.source_id,
            ),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_knowledge_detail(
                exc,
                code="knowledge_ingest_file_invalid",
                scope="knowledge_ingest_file",
                source_id=request.source_id,
            ),
        ) from exc


@router.post(
    "/import-character-sheet",
    response_model=CharacterSheetImportResponse,
    status_code=status.HTTP_200_OK,
)
def import_character_sheet(
    request: CharacterSheetImportRequest,
    service: KnowledgeService = Depends(get_knowledge_service),
) -> CharacterSheetImportResponse:
    try:
        return service.import_character_sheet(request)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_knowledge_detail(
                exc,
                code="knowledge_source_not_found",
                scope="knowledge_source_lookup",
                source_id=request.source_id,
            ),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_knowledge_detail(
                exc,
                code="knowledge_character_import_invalid",
                scope="knowledge_character_import",
                source_id=request.source_id,
            ),
        ) from exc


@router.get("/sources/{source_id}", response_model=KnowledgeSourceState)
def get_source(
    source_id: str,
    service: KnowledgeService = Depends(get_knowledge_service),
) -> KnowledgeSourceState:
    try:
        return service.get_source(source_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_knowledge_detail(
                exc,
                code="knowledge_source_not_found",
                scope="knowledge_source_lookup",
                source_id=source_id,
            ),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_knowledge_detail(
                exc,
                code="knowledge_source_invalid",
                scope="knowledge_source_request",
                source_id=source_id,
            ),
        ) from exc
