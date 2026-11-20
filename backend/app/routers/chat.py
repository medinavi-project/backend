from uuid import UUID

from fastapi import APIRouter, Request

from app.core.response import ApiResponse
from app.schemas.chat import (
    CreateRequest,
    CreateResponse,
    MessageRequest,
    MessageResponse,
    Result,
)

router = APIRouter(prefix="/api/v1/chat/sessions", tags=["문진"])


@router.post("", status_code=201, response_model=ApiResponse[CreateResponse])
async def create(body: CreateRequest, request: Request):
    return ApiResponse(
        code="CREATED",
        message="문진 세션이 생성되었습니다.",
        data=await request.app.state.chat.create(body),
    )


@router.post("/{session_id}/messages", response_model=ApiResponse[MessageResponse])
async def message(session_id: UUID, body: MessageRequest, request: Request):
    data = await request.app.state.chat.message(session_id, body)
    return ApiResponse(
        code="SUCCESS", message="문진 메시지가 처리되었습니다.", data=data
    )


@router.get("/{session_id}/result", response_model=ApiResponse[Result])
async def result(session_id: UUID, request: Request):
    return ApiResponse(
        code="SUCCESS",
        message="문진 상태 및 결과를 조회했습니다.",
        data=await request.app.state.chat.result(session_id),
    )
