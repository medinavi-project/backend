"""출처별 원문 인용 검증. 자유 서술의 의료적 정확성은 자동 확정하지 않는다."""

import json
import re

ID_RE = re.compile(r"\b(?:MFDS|DUR)-[0-9a-f]{20}\b")
SAFETY_FIELDS = ("warnings", "precautions", "interactions")
NUMBER_RE = re.compile(
    r"\d+(?:[.,/]\d+)*(?:\s*(?:개월|시간|밀리그램|밀리그람|mL|mg|세|일|회|병))?", re.I
)


def norm(value):
    return re.sub(r"\s+", " ", value).strip()


def official_data(products, confirmed_item_seqs):
    """품목 목록 API의 코드 확인이 성공한 자료만 공식 영역에 포함한다."""
    wanted = {str(x) for x in confirmed_item_seqs}
    info = []
    precautions = []
    for p in products:
        seq = str(p.get("item_seq", ""))
        identity = p.get("source", {}).get("permit_list", {})
        if (
            seq not in wanted
            or identity.get("status") != "ok"
            or identity.get("total_count") != 1
        ):
            continue
        info.append(
            {
                k: p.get(k)
                for k in (
                    "item_seq",
                    "drug_name",
                    "manufacturer",
                    "drug_type",
                    "ingredients",
                    "strength",
                    "dosage_form",
                    "appearance",
                    "effects",
                    "dosage",
                    "side_effects",
                    "storage",
                    "source",
                    "retrieved_at",
                )
            }
        )
        raw = p.get("raw_documents", [])
        for field in SAFETY_FIELDS:
            for d in p.get(field, []):
                original = next(
                    (
                        x.get("text")
                        for x in raw
                        if x.get("source") == d.get("source")
                        and x.get("source_field") == d.get("source_field")
                    ),
                    None,
                )
                text = d.get("text", "")
                precautions.append(
                    {
                        "item_seq": seq,
                        "field": field,
                        "source_id": d.get("source_id"),
                        "official_raw": original,
                        "official_text": text,
                        "url": d.get("url"),
                        "retrieved_at": p.get("retrieved_at"),
                        "numbers_and_units": NUMBER_RE.findall(text),
                        "condition_sentences": [
                            line.strip()
                            for line in text.splitlines()
                            if line.strip()
                            and re.search(
                                r"금기|복용하지|투여하지|이상|미만|경우|제외|개월|시간|상의|지킬|준수",
                                line,
                            )
                        ],
                    }
                )
    return info, precautions


def validate_response(answer, context_json):
    """전달된 Context의 원문 문구와 안전 문서 전체를 인용한 답변만 허용한다.

    각 근거 인용은 '원문 [source_id]' 형식이다. 패러프레이즈 및 미인용 문장은 검토 대상으로 둔다.
    """
    result = {
        "status": "review_required",
        "reasons": [],
        "unknown_source_ids": [],
        "unsupported_claims": [],
        "missing_precautions": [],
        "method": "source_linked_summary_with_protected_quotes",
        "medical_expert_review": "not_performed",
    }
    try:
        payload = (
            json.loads(context_json) if isinstance(context_json, str) else context_json
        )
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("products"), list)
            or not isinstance(payload.get("source_index"), dict)
        ):
            raise ValueError()
        docs = {
            d["source_id"]: d
            for p in payload["products"]
            for d in p.get("documents", [])
            if isinstance(d, dict)
            and d.get("source_id") in payload["source_index"]
            and isinstance(d.get("text"), str)
        }
    except (ValueError, TypeError, KeyError):
        result["reasons"].append("실제 모델에 전달한 근거 문서를 확인할 수 없습니다.")
        return result
    if not isinstance(answer, str) or not answer.strip():
        result["reasons"].append("답변이 비어 있습니다.")
        return result
    refs = list(ID_RE.finditer(answer))
    used = {m.group() for m in refs}
    if not refs:
        result["reasons"].append("출처 ID가 없습니다.")
    result["unknown_source_ids"] = sorted(used - set(docs))
    if result["unknown_source_ids"]:
        result["reasons"].append("전달된 공식 근거에 없는 출처 ID가 있습니다.")
    previous = 0
    for match in refs:
        # 인용 앞 텍스트 전체가 해당 출처의 연속 원문이어야 한다.
        claim = answer[previous : match.start()].strip().strip("[]() \n\t")
        claim = re.sub(r"(?m)^\s*[-*•]\s+", "", claim).replace("**", "").strip()
        doc = docs.get(match.group())
        protected = doc is not None and (
            doc.get("field") in (*SAFETY_FIELDS, "dosage")
            or bool(NUMBER_RE.search(claim))
            or bool(
                re.search(
                    r"금기|복용|투여|용량|임신|수유|미만|이상|안전|가능|괜찮", claim
                )
            )
        )
        if not claim or not doc or (protected and norm(claim) not in norm(doc["text"])):
            result["unsupported_claims"].append(
                {
                    "source_id": match.group(),
                    "reason": "출처 원문과 불일치 또는 빈 인용",
                }
            )
        previous = match.end()
        if previous < len(answer) and answer[previous] == "]":
            previous += 1
    if answer[previous:].strip("[]() \n\t"):
        result["unsupported_claims"].append(
            {"source_id": None, "reason": "출처가 없는 문장"}
        )
    if result["unsupported_claims"]:
        result["reasons"].append(
            "일부 문장이 연결된 출처의 원문과 일치하지 않거나 출처가 없습니다."
        )
    # Official precautions are delivered separately in full. Do not require
    # repeating every warning document in the user-facing summary.
    if not any(d.get("field") in SAFETY_FIELDS for d in docs.values()):
        result["reasons"].append("전달된 공식 주의사항 근거가 없습니다.")
    if not result["reasons"]:
        result["status"] = "source_linked_summary"
    return result
