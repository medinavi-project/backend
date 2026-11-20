from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.chat import Disease, Message, Symptoms

SymptomField = Literal[
    "main_symptom",
    "body_location",
    "onset",
    "severity",
    "additional_symptoms",
    "region",
]


class ChatInput(BaseModel):
    messages: list[Message]
    symptoms: Symptoms


class ChatOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symptom_updates: Symptoms
    answered_fields: list[SymptomField]
    next_question: str = Field(min_length=1, max_length=1000)
    should_finish: bool = False
    red_flag_detected: bool = False


class AnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment_status: Literal[
        "PRELIMINARY", "INSUFFICIENT_INFORMATION", "INSUFFICIENT_EVIDENCE"
    ]
    symptom_summary: str = Field(min_length=1, max_length=2000)
    suspected_diseases: list[Disease] = Field(max_length=3)
    recommended_department: str | None = Field(max_length=100)


class SummaryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Extractive summaries must contain only supplied source sentences.
    summary: list[str] = Field(min_length=1, max_length=10)
