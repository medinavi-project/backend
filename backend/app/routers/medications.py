from fastapi import APIRouter, Request, UploadFile

from app.core.response import ApiResponse
from app.schemas.chat import MedicationRequest
from app.schemas.medications import GuideResponse, ScanResponse

router = APIRouter(prefix="/api/v1/medications", tags=["의약품"])


@router.post(
    "/scan",
    response_model=ApiResponse[ScanResponse],
    description="OCR 미구현: 유효한 업로드 요청에는 503 OCR_SERVICE_UNAVAILABLE을 반환합니다.",
)
async def scan(file: UploadFile, request: Request):
    try:
        return await request.app.state.ocr.scan(file)
    finally:
        await file.close()


@router.post("/guide", response_model=ApiResponse[GuideResponse])
async def guide(body: MedicationRequest, request: Request):
    return ApiResponse(
        code="SUCCESS",
        message="시연용 복약 안내가 생성되었습니다.",
        data=await request.app.state.medications.guide(body),
    )
