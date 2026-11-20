from app.core.exceptions import BusinessException
from app.core.response_codes import Code
from app.schemas.medications import GuideResponse, Medication


class MedicationService:
    def __init__(self, data, provider):
        self.data, self.provider = data, provider

    async def guide(self, request):
        available = {m["item_seq"]: m for m in self.data.medications()}
        if any(key not in available for key in request.item_seqs):
            raise BusinessException(Code.MEDICATION_NOT_FOUND)
        results = []
        for key in request.item_seqs:
            medication = available[key]
            output = await self.provider.summarize_medication(medication)
            allowed = {
                medication["efficacy"],
                medication["usage"],
                *medication["cautions"],
            }
            if not all(line in allowed for line in output.summary):
                raise BusinessException(
                    Code.MEDICATION_GUIDE_FAILED, "원본 정보 범위를 벗어난 요약입니다."
                )
            results.append(Medication(**medication, summary=output.summary))
        return GuideResponse(medications=results)
