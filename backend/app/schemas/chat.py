from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

DISCLAIMER = "본 결과는 AI 기반 임시 참고 정보이며 의학적 진단이 아닙니다. 정확한 판단은 의료진의 진료를 받아야 합니다."


class Status(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    ANALYZING = "ANALYZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EMERGENCY = "EMERGENCY"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateRequest(Input):
    initial_message: str = Field(min_length=1, max_length=1000)


class MessageRequest(Input):
    client_message_id: UUID
    message: str = Field(min_length=1, max_length=1000)


class Symptoms(BaseModel):
    model_config = ConfigDict(extra="forbid")
    main_symptom: str | None = None
    body_location: str | None = None
    onset: str | None = None
    severity: int | None = Field(default=None, ge=0, le=10)
    additional_symptoms: list[str] = Field(default_factory=list)
    region: str | None = None


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class Disease(BaseModel):
    disease_name: str
    reason: str


class Result(BaseModel):
    session_id: UUID
    status: Status
    assessment_status: (
        Literal["PRELIMINARY", "INSUFFICIENT_INFORMATION", "INSUFFICIENT_EVIDENCE"]
        | None
    ) = None
    analysis_source: str | None = None
    symptom_summary: str | None = None
    suspected_diseases: list[Disease] = Field(default_factory=list)
    recommended_department: str | None = None
    references: list[str] = Field(default_factory=list)
    medical_disclaimer: str = DISCLAIMER
    failure_code: str | None = None


class CreateResponse(BaseModel):
    session_id: UUID
    status: Status
    next_question: str | None
    turn_count: int
    expires_at: datetime
    emergency_message: str | None = None


class MessageResponse(BaseModel):
    session_id: UUID
    status: Status
    next_question: str | None
    turn_count: int
    collected_symptoms: Symptoms
    result_available: bool
    emergency_message: str | None = None


class Receipt(BaseModel):
    message: str
    response: MessageResponse


class Session(BaseModel):
    session_id: UUID
    status: Status = Status.IN_PROGRESS
    turn_count: int = 0
    messages: list[Message] = Field(default_factory=list)
    symptoms: Symptoms = Field(default_factory=Symptoms)
    result: Result | None = None
    created_at: datetime
    updated_at: datetime
    answered_fields: set[str] = Field(default_factory=set)
    receipts: dict[str, Receipt] = Field(default_factory=dict)
    failure_code: str | None = None
    engine_state: dict = Field(default_factory=dict)
    emergency_message: str | None = None


class MedicationRequest(Input):
    item_seqs: list[str] = Field(min_length=1, max_length=10)

    @field_validator("item_seqs")
    @classmethod
    def unique(cls, values):
        if len(set(values)) != len(values) or any(
            not value.strip() for value in values
        ):
            raise ValueError("약품 식별자는 비어 있거나 중복될 수 없습니다.")
        return values
