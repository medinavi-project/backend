from app.core.exceptions import BusinessException
from app.core.response_codes import Code


class OCRService:
    async def scan(self, file):
        if file.content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise BusinessException(Code.IMAGE_FORMAT_UNSUPPORTED)
        total = 0
        while chunk := await file.read(65536):
            total += len(chunk)
            if total > 5 * 1024 * 1024:
                raise BusinessException(Code.IMAGE_SIZE_EXCEEDED)
        if total == 0:
            raise BusinessException(Code.IMAGE_INVALID)
        raise BusinessException(Code.OCR_SERVICE_UNAVAILABLE)
