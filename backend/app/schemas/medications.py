from typing import Literal

from pydantic import BaseModel


class Candidate(BaseModel):
    item_seq: str
    item_name: str


class RecognizedMedication(BaseModel):
    recognized_name: str
    match_status: Literal["MATCHED", "AMBIGUOUS", "UNMATCHED"]
    item_seq: str | None = None
    item_name: str | None = None
    candidates: list[Candidate]


class ScanResponse(BaseModel):
    ocr_provider: str
    recognized_text: str
    medications: list[RecognizedMedication]


class Medication(BaseModel):
    item_seq: str
    item_name: str
    efficacy: str
    usage: str
    cautions: list[str]
    summary: list[str]


class GuideResponse(BaseModel):
    data_source: str = "DEMO"
    medications: list[Medication]
    audio_url: str | None = None
    audio_status: Literal["DISABLED", "COMPLETED", "FAILED"] = "DISABLED"
    medical_disclaimer: str = "본 안내는 제공된 시연용 의약품 정보를 요약한 참고 자료이며 의료진과 약사의 지시를 대체하지 않습니다."
