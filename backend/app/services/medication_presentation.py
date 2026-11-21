from app.schemas.medications import (
    MedicationCard,
    MedicationDescription,
    MedicationScanResponse,
    UncertainMedicationCard,
)


def text_of(value):
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        texts = [text_of(row) for row in value]
        return "\n\n".join(dict.fromkeys(t for t in texts if t)) or None
    if isinstance(value, dict):
        return text_of(value.get("official_text") or value.get("text"))
    return None


def present_scan(result):
    cards = []
    for item in result.identified_medications:
        info = item.official_info or {}
        description = MedicationDescription(
            purpose=text_of(item.summary) or text_of(info.get("effects")),
            how_to_take=text_of(info.get("dosage")),
            precautions=item.display_precautions or text_of(item.precautions),
            side_effects=text_of(info.get("side_effects")),
            storage=text_of(info.get("storage")),
        )
        values = list(description.model_dump().values())
        cards.append(
            MedicationCard(
                item_seq=item.item_seq,
                name=item.item_name,
                description=description,
                guidance_status="AVAILABLE"
                if all(values)
                else ("PARTIAL" if any(values) else "UNAVAILABLE"),
            )
        )
    reasons = {
        "PRIVACY_REVIEW_REQUIRED": "문장에 섞인 내용을 약품명으로 확인하기 어렵습니다.",
        "SEARCH_FAILED": "약품 정보를 조회하지 못했습니다.",
        "INACTIVE_OR_UNCERTAIN_PRODUCT": "정상 허가 품목으로 확정하기 어렵습니다.",
        "IDENTITY_UNCERTAIN": "정확한 제품명이나 함량을 확인하기 어렵습니다.",
    }
    uncertain = [
        UncertainMedicationCard(
            recognized_name=item.recognized_name,
            reason=reasons.get(
                item.review_reason, "정확한 약품을 확인하지 못했습니다."
            ),
            action_message="약 이름과 함량을 약사에게 확인하거나 선명하게 다시 촬영해 주세요.",
        )
        for item in result.uncertain_medications
    ]
    return MedicationScanResponse(
        status=result.status,
        identified_count=len(cards),
        uncertain_count=len(uncertain),
        medications=cards,
        uncertain_medications=uncertain,
    )
