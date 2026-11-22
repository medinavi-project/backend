from typing import Literal

from pydantic import BaseModel


class Candidate(BaseModel):
    item_seq: str
    item_name: str
    similarity_score: float | None = None
    strength_match: bool | None = None
    form_match: bool | None = None
    permit_status: str | None = None
    match_reason: str | None = None


class RecognizedMedication(BaseModel):
    recognized_name: str
    match_status: Literal["MATCHED", "AMBIGUOUS", "UNMATCHED"]
    item_seq: str | None = None
    item_name: str | None = None
    review_reason: str | None = None
    candidates: list[Candidate]


class OCRResult(BaseModel):
    recognized_text: str


class IdentifiedMedicationDTO(BaseModel):
    display_precautions: str | None = None
    # Plain-language rewrite of the official effects (AI, guarded; not AI_VALIDATED).
    display_purpose: str | None = None
    item_seq: str
    item_name: str
    recognized_names: list[str]
    summary: list[dict] = []
    summary_status: str
    official_info: dict | None = None
    precautions: list[dict] = []
    # Set when identity came from the bag-corroborated rule (typo or inactive).
    identity_note: str | None = None


class MedicationScanResult(BaseModel):
    identified_count: int = 0
    uncertain_count: int = 0
    summarized_count: int = 0
    identified_medications: list[IdentifiedMedicationDTO] = []
    uncertain_medications: list[RecognizedMedication] = []
    recognized_text: str
    status: Literal[
        "COMPLETED", "NEEDS_CONFIRMATION", "INCOMPLETE", "OCR_ONLY", "PARTIAL"
    ]
    medications: list[RecognizedMedication] = []
    answer: str | None = None
    official_drug_info: list[dict] = []
    official_precautions: list[dict] = []
    sources: dict = {}
    warnings: list[str | dict] = []
    dur_status: str = "NOT_CHECKED"
    dur_details: dict = {}
    information_status: str = "NOT_AVAILABLE"
    ai_summary: dict | None = None
    validation_status: str = "NOT_RUN"
    processing_status: str | None = None
    data_source: str = "MFDS"
    analysis_source: str = "CLOVA"
    identification_method: str = "EXACT_OFFICIAL_NAME"
    audio_url: str | None = None
    audio_status: Literal["DISABLED"] = "DISABLED"
    medical_disclaimer: str = (
        "의약품 참고 정보이며 의료진과 약사의 지시를 대체하지 않습니다."
    )


class MedicationDescription(BaseModel):
    purpose: str | None = None
    how_to_take: str | None = None
    precautions: str | None = None
    side_effects: str | None = None
    storage: str | None = None


class MedicationCard(BaseModel):
    item_seq: str
    name: str
    description: MedicationDescription
    guidance_status: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE"]
    # User-facing caution: auto-matched from an OCR typo, or permit not active.
    warning: str | None = None


class UncertainMedicationCard(BaseModel):
    recognized_name: str
    reason: str
    action_message: str


class MedicationScanResponse(BaseModel):
    status: Literal[
        "COMPLETED", "NEEDS_CONFIRMATION", "INCOMPLETE", "OCR_ONLY", "PARTIAL"
    ]
    identified_count: int
    uncertain_count: int
    medications: list[MedicationCard]
    uncertain_medications: list[UncertainMedicationCard]
