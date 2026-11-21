import base64
import time
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.exceptions import BusinessException
from app.core.response_codes import Code


class OCRField(BaseModel):
    inferText: str
    inferConfidence: float = Field(ge=0, le=1)
    lineBreak: bool = False


class OCRImage(BaseModel):
    inferResult: str
    fields: list[OCRField] = Field(default_factory=list)


class OCRResponse(BaseModel):
    images: list[OCRImage] = Field(min_length=1, max_length=1)


class ClovaOCRClient:
    def __init__(self, settings):
        self.settings = settings

    async def recognize(self, content, content_type):
        settings = self.settings
        if (
            not settings.CLOVA_OCR_INVOKE_URL
            or not settings.CLOVA_OCR_SECRET.get_secret_value()
        ):
            raise BusinessException(
                Code.OCR_SERVICE_UNAVAILABLE,
                "CLOVA OCR Invoke URL과 Secret을 설정하세요.",
            )
        formats = {"image/jpeg": "jpg", "image/png": "png"}
        if content_type not in formats:
            raise BusinessException(
                Code.IMAGE_FORMAT_UNSUPPORTED,
                "CLOVA OCR 업로드는 JPEG 또는 PNG를 사용하세요.",
            )
        payload = {
            "version": "V2",
            "requestId": str(uuid4()),
            "timestamp": int(time.time() * 1000),
            "lang": "ko",
            "images": [
                {
                    "format": formats[content_type],
                    "name": "upload",
                    "data": base64.b64encode(content).decode("ascii"),
                }
            ],
        }
        try:
            async with httpx.AsyncClient(
                timeout=settings.OCR_REQUEST_TIMEOUT_SECONDS
            ) as client:
                response = await client.post(
                    settings.CLOVA_OCR_INVOKE_URL,
                    headers={
                        "X-OCR-SECRET": settings.CLOVA_OCR_SECRET.get_secret_value()
                    },
                    json=payload,
                )
                if response.status_code in {401, 403}:
                    raise BusinessException(
                        Code.OCR_SERVICE_UNAVAILABLE,
                        "CLOVA OCR 인증과 도메인 설정을 확인하세요.",
                    )
                if response.status_code == 429:
                    raise BusinessException(Code.RATE_LIMIT_EXCEEDED)
                response.raise_for_status()
                result = OCRResponse.model_validate(response.json())
        except httpx.TimeoutException:
            raise BusinessException(Code.OCR_REQUEST_TIMEOUT) from None
        except (httpx.HTTPError, ValidationError, ValueError):
            raise BusinessException(Code.OCR_RECOGNITION_FAILED) from None
        image = result.images[0]
        if image.inferResult != "SUCCESS":
            raise BusinessException(Code.OCR_RECOGNITION_FAILED)
        text = "".join(
            field.inferText + ("\n" if field.lineBreak else " ")
            for field in image.fields
        ).strip()
        if not text:
            raise BusinessException(Code.OCR_TEXT_NOT_FOUND)
        return text
