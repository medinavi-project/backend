from fastapi import APIRouter, Request, UploadFile

from app.core.response import ApiResponse
from app.schemas.medications import MedicationScanResponse
from app.services.medication_presentation import present_scan

router = APIRouter(prefix="/api/v1/medications", tags=["의약품"])


@router.post(
    "/scan",
    response_model=ApiResponse[MedicationScanResponse],
    description="이미지 → OCR → 약품 식별 → 식약처 근거 조회 → CLOVA 안내를 한 요청으로 처리합니다.",
)
async def scan(file: UploadFile, request: Request):
    try:
        return ApiResponse(
            code="SUCCESS",
            message="의약품 이미지 처리가 완료되었습니다.",
            data=present_scan(await request.app.state.ocr.scan(file)),
        )
    finally:
        await file.close()
