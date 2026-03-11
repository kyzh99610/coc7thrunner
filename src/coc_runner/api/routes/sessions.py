from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError

from coc_runner.api.dependencies import get_session_service
from coc_runner.application.session_service import SessionService
from coc_runner.domain.errors import ConflictError
from coc_runner.domain.models import (
    ApplyCharacterImportRequest,
    ApplyCharacterImportResponse,
    InvestigatorView,
    KPDraftRequest,
    LanguagePreference,
    ManualActionRequest,
    PlayerActionRequest,
    PlayerActionResponse,
    ReviewDraftRequest,
    ReviewDraftResponse,
    RollbackRequest,
    RollbackResponse,
    SessionImportResponse,
    SessionStartRequest,
    SessionStartResponse,
    UpdateKeeperPromptRequest,
    UpdateKeeperPromptResponse,
    ViewerRole,
)


router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("/start", response_model=SessionStartResponse, status_code=status.HTTP_201_CREATED)
def start_session(
    request: SessionStartRequest,
    service: SessionService = Depends(get_session_service),
) -> SessionStartResponse:
    try:
        return service.start_session(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/{session_id}/player-action",
    response_model=PlayerActionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_player_action(
    session_id: str,
    request: PlayerActionRequest,
    service: SessionService = Depends(get_session_service),
) -> PlayerActionResponse:
    try:
        return service.submit_player_action(session_id, request)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/{session_id}/kp-draft",
    response_model=PlayerActionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_kp_draft(
    session_id: str,
    request: KPDraftRequest,
    service: SessionService = Depends(get_session_service),
) -> PlayerActionResponse:
    try:
        return service.submit_kp_draft(session_id, request)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/{session_id}/manual-action",
    response_model=PlayerActionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_manual_action(
    session_id: str,
    request: ManualActionRequest,
    service: SessionService = Depends(get_session_service),
) -> PlayerActionResponse:
    try:
        return service.submit_manual_action(session_id, request)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        detail = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else str(exc)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/{session_id}/apply-character-import",
    response_model=ApplyCharacterImportResponse,
    status_code=status.HTTP_200_OK,
)
def apply_character_import(
    session_id: str,
    request: ApplyCharacterImportRequest,
    service: SessionService = Depends(get_session_service),
) -> ApplyCharacterImportResponse:
    try:
        return service.apply_character_import(session_id, request)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/{session_id}/keeper-prompts/{prompt_id}/status",
    response_model=UpdateKeeperPromptResponse,
    status_code=status.HTTP_200_OK,
)
def update_keeper_prompt_status(
    session_id: str,
    prompt_id: str,
    request: UpdateKeeperPromptRequest,
    service: SessionService = Depends(get_session_service),
) -> UpdateKeeperPromptResponse:
    try:
        return service.update_keeper_prompt_status(session_id, prompt_id, request)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/{session_id}/draft-actions/{draft_id}/review",
    response_model=ReviewDraftResponse,
    status_code=status.HTTP_200_OK,
)
def review_draft_action(
    session_id: str,
    draft_id: str,
    request: ReviewDraftRequest,
    service: SessionService = Depends(get_session_service),
) -> ReviewDraftResponse:
    try:
        return service.review_draft_action(session_id, draft_id, request)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{session_id}/state", response_model=InvestigatorView)
def get_session_state(
    session_id: str,
    viewer_id: str | None = Query(default=None),
    viewer_role: ViewerRole = Query(default=ViewerRole.INVESTIGATOR),
    language_preference: LanguagePreference | None = Query(default=None),
    service: SessionService = Depends(get_session_service),
) -> InvestigatorView:
    try:
        return service.get_session_view(
            session_id,
            viewer_id=viewer_id,
            viewer_role=viewer_role,
            language_preference=language_preference,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{session_id}/export", response_model=dict[str, Any])
def export_session(
    session_id: str,
    language_preference: LanguagePreference | None = Query(default=None),
    service: SessionService = Depends(get_session_service),
) -> dict[str, Any]:
    try:
        return service.export_session(
            session_id,
            language_preference=language_preference,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{session_id}/snapshot", response_model=dict[str, Any])
def snapshot_session(
    session_id: str,
    language_preference: LanguagePreference | None = Query(default=None),
    service: SessionService = Depends(get_session_service),
) -> dict[str, Any]:
    try:
        return service.snapshot_session(
            session_id,
            language_preference=language_preference,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/import", response_model=SessionImportResponse, status_code=status.HTTP_201_CREATED)
def import_session(
    payload: dict[str, Any],
    language_preference: LanguagePreference | None = Query(default=None),
    service: SessionService = Depends(get_session_service),
) -> SessionImportResponse:
    try:
        return service.import_session(
            payload,
            language_preference=language_preference,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{session_id}/rollback", response_model=RollbackResponse)
def rollback_session(
    session_id: str,
    request: RollbackRequest,
    service: SessionService = Depends(get_session_service),
) -> RollbackResponse:
    try:
        return service.rollback_session(session_id, request)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
