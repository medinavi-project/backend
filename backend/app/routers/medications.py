from fastapi import APIRouter, Request, Response, UploadFile

from app.core.response import ApiResponse
from app.schemas.medications import MedicationScanResponse
from app.services.medication_presentation import present_scan
from app.services.voice_service import speech_text

router = APIRouter(prefix="/api/v1/medications", tags=["의약품"])


@router.post(
    "/scan",
    response_model=ApiResponse[MedicationScanResponse],
    description="이미지 → OCR → 약품 식별 → 식약처 근거 조회 → CLOVA 안내를 한 요청으로 처리합니다.",
)
async def scan(file: UploadFile, request: Request):
    try:
        data = present_scan(await request.app.state.ocr.scan(file))
    finally:
        await file.close()
    voice = request.app.state.voice
    if voice.configured:
        for card in data.medications:
            text = speech_text(card.name, card.description.purpose)
            card.audio_url = await voice.register(text)
            card.audio_status = "READY"
    return ApiResponse(
        code="SUCCESS", message="의약품 이미지 처리가 완료되었습니다.", data=data
    )


@router.get(
    "/audio/{audio_id}",
    response_class=Response,
    responses={200: {"content": {"audio/mpeg": {}}}},
    description="스캔 응답의 audio_url. 약 이름과 효능을 CLOVA Voice로 읽은 mp3 (1시간 유효).",
)
async def audio(audio_id: str, request: Request):
    mp3 = await request.app.state.voice.audio(audio_id)
    return Response(mp3, media_type="audio/mpeg")
