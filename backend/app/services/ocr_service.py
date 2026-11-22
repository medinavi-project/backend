from app.core.exceptions import BusinessException
from app.core.response_codes import Code
from app.schemas.medications import OCRResult


class OCRService:
    def __init__(self, settings=None):
        self.settings = settings

    async def extract(self, file):
        if file.content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise BusinessException(Code.IMAGE_FORMAT_UNSUPPORTED)
        total = 0
        chunks = []
        while chunk := await file.read(65536):
            total += len(chunk)
            chunks.append(chunk)
            if total > 5 * 1024 * 1024:
                raise BusinessException(Code.IMAGE_SIZE_EXCEEDED)
        if total == 0:
            raise BusinessException(Code.IMAGE_INVALID)
        if self.settings is None or self.settings.OCR_PROVIDER == "disabled":
            raise BusinessException(Code.OCR_SERVICE_UNAVAILABLE)
        if self.settings.OCR_PROVIDER == "mock":
            text = "[MOCK OCR] 시연용 텍스트입니다. 실제 약품 정보가 아닙니다."
        else:
            from app.ocr.clova import ClovaOCRClient

            text = await ClovaOCRClient(self.settings).recognize(
                b"".join(chunks), file.content_type
            )
        if not text.strip():
            raise BusinessException(Code.OCR_TEXT_NOT_FOUND)
        if len(text) > 20000:
            raise BusinessException(Code.OCR_RECOGNITION_FAILED)
        return OCRResult(recognized_text=text)

    async def scan(self, file):
        from app.schemas.medications import MedicationScanResult
        from app.services.medication_scan_service import MedicationScanService

        result = await self.extract(file)
        if not self.settings.MEDICATION_AI_ENABLED:
            return MedicationScanResult(
                recognized_text=result.recognized_text,
                status="OCR_ONLY",
                data_source="OCR",
                analysis_source="DISABLED",
            )
        return await MedicationScanService(self.settings).analyze(
            result.recognized_text
        )
