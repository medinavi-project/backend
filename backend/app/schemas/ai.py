from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    model_validator,
)

from app.schemas.chat import Message, Symptoms

SymptomField = Literal[
    "main_symptom",
    "body_location",
    "onset",
    "severity",
    "additional_symptoms",
    "region",
]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ChatInput(BaseModel):
    messages: list[Message]
    symptoms: Symptoms
    answered_fields: list[SymptomField]
    turn_count: int = Field(ge=1)
    max_turns: int = Field(ge=1, le=10)
    # Opaque per-session state of an engine that keeps its own memory.
    engine_state: dict = Field(default_factory=dict)


class SymptomUpdates(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    main_symptom: Text | None = None
    body_location: Text | None = None
    onset: Text | None = None
    severity: int | None = Field(default=None, ge=0, le=10)
    additional_symptoms: list[Text] = Field(default_factory=list)
    region: Text | None = None


class AnalysisDisease(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    disease_name: Text
    reason: Text


class AnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    assessment_status: Literal[
        "PRELIMINARY", "INSUFFICIENT_INFORMATION", "INSUFFICIENT_EVIDENCE"
    ]
    symptom_summary: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    suspected_diseases: list[AnalysisDisease] = Field(max_length=3)
    recommended_department: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
        ]
        | None
    )


class ChatOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["ASK_FOLLOW_UP", "COMPLETE", "EMERGENCY"]
    symptom_updates: SymptomUpdates
    answered_fields: list[SymptomField]
    next_question: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
        ]
        | None
    )
    analysis: AnalysisOutput | None
    emergency_guidance: str | None = None
    engine_state: dict | None = None
    references: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_contract(self, info: ValidationInfo):
        if len(set(self.answered_fields)) != len(self.answered_fields):
            raise ValueError("answered_fields must be unique")
        updates = self.symptom_updates.model_dump(exclude_unset=True)
        if any(
            field not in updates or updates[field] is None
            for field in self.answered_fields
        ):
            raise ValueError(
                "answered_fields must reference explicitly supplied non-null updates"
            )
        if self.action == "EMERGENCY":
            if not self.emergency_guidance or self.next_question or self.analysis:
                raise ValueError("EMERGENCY requires guidance only")
            return self
        if self.emergency_guidance is not None:
            raise ValueError("emergency_guidance is only for EMERGENCY")
        if self.action == "ASK_FOLLOW_UP":
            if self.next_question is None or self.analysis is not None:
                raise ValueError("ASK_FOLLOW_UP requires a question and null analysis")
            context = info.context or {}
            if context.get("turn_count", 0) >= context.get("max_turns", 6):
                raise ValueError(
                    "Maximum turn requires COMPLETE with an analysis result"
                )
        elif self.next_question is not None or self.analysis is None:
            raise ValueError("COMPLETE requires null question and an analysis result")
        return self
