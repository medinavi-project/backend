import json
import re
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Summary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1000)
    source_ids: list[str] = Field(min_length=1, max_length=5)


class DrugSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_seq: str
    summary: list[Summary] = Field(min_length=1, max_length=5)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    medications: list[DrugSummary] = Field(min_length=1, max_length=10)


def validate_structured(answer, context):
    result = {
        "status": "review_required",
        "method": "structured_source_check",
        "medical_expert_review": "not_performed",
        "reasons": [],
    }
    try:
        context = json.loads(context) if isinstance(context, str) else context
        raw = answer.strip()
        match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, re.S | re.I)
        parsed = Answer.model_validate_json(match.group(1) if match else raw)
        products = {str(p["item_seq"]): p for p in context["products"]}
        if len({p.item_seq for p in parsed.medications}) != len(parsed.medications) or {
            p.item_seq for p in parsed.medications
        } != set(products):
            raise ValueError("product mismatch")
        for medication in parsed.medications:
            allowed = {
                d["source_id"]
                for d in products[medication.item_seq]["documents"]
                if d["field"] == "effects" and d["source_id"] in context["source_index"]
            }
            for summary in medication.summary:
                if not summary.text.strip() or not set(summary.source_ids) <= allowed:
                    raise ValueError("invalid source")
                if re.search(
                    r"\d|복용|투여|용량|금기|임신|수유|안전|괜찮|상호작용", summary.text
                ):
                    raise ValueError("protected content in summary")
        result["status"] = "structured_source_linked"
        result["structured_summary"] = parsed.model_dump()
        result["display_answer"] = "\n".join(
            f"{item.text} [{', '.join(item.source_ids)}]"
            for med in parsed.medications
            for item in med.summary
        )
    except (ValidationError, ValueError, KeyError, TypeError, AttributeError):
        result["reasons"].append(
            "구조·품목·효능 출처 또는 보호 항목 규칙을 확인하지 못했습니다."
        )
    return result
