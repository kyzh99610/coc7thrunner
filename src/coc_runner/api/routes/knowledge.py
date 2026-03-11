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


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/sources/{source_id}", response_model=KnowledgeSourceState)
def get_source(
    source_id: str,
    service: KnowledgeService = Depends(get_knowledge_service),
) -> KnowledgeSourceState:
    try:
        return service.get_source(source_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
