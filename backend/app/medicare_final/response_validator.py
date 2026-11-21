"""출처별 원문 인용 검증. 자유 서술의 의료적 정확성은 자동 확정하지 않는다."""

import json
import re

ID_RE = re.compile(r"\b(?:MFDS|DUR)-[0-9a-f]{20}\b")
SAFETY_FIELDS = ("warnings", "precautions", "interactions")
NUMBER_RE = re.compile(
    r"\d+(?:[.,/]\d+)*(?:\s*(?:개월|시간|밀리그램|밀리그람|mL|mg|세|일|회|병))?", re.I
)

# 수동으로 대응 관계를 한정한 예시 규칙이다. 미등록 표현의 의미를 추론하지 않는다.
CONTROLLED_REPHRASES = (
    {
        "item_seq": "195700020",
        "field": "effects",
        "claim": "이 약은 소화가 잘 안 되거나 입맛이 떨어질 때 사용합니다.",
        "required": ("식욕감퇴(식욕부진)", "소화불량"),
    },
    {
        "item_seq": "195700020",
        "field": "effects",
        "claim": "이 약은 소화불량에 사용합니다.",
        "required": ("소화불량",),
    },
    {
        "item_seq": "195700020",
        "field": "dosage",
        "claim": "만 15세 이상 및 성인은 한 번에 1병(75 mL)을 하루 3회 식후에 복용하며, 복용 간격은 4시간 이상입니다.",
        "required": (
            "만 15세 이상 및 성인은 1회 1병(75 mL)",
            "1일 3회 식후에 복용합니다.",
            "복용간격은 4시간 이상으로 합니다.",
        ),
    },
)
CLAIM_FIELDS = {
    "effect": "effects",
    "dosage": "dosage",
    "precaution": "precautions",
    "warning": "warnings",
    "interaction": "interactions",
    "side_effect": "side_effects",
    "storage": "storage",
}

# 대응은 해당 공식 문서가 괄호 안에 직접 제공한 표현에 한정한다. 일반 동의어 사전이 아니다.
MEDICAL_TERM_CORRESPONDENCES = (
    {
        "official_term": "동통",
        "plain_candidate": "통증",
        "source": "식약처 효능 원문 자체의 괄호 병기",
        "context": "공식 원문에 동통(통증)이 그대로 존재",
        "review_status": "source_explicit_developer_review",
        "reviewer_type": "developer",
        "medication_scope": "해당 괄호 병기를 포함하는 식약처 효능 문서",
        "medical_expert_review": "not_performed",
    },
    {
        "official_term": "염좌통",
        "plain_candidate": "삔 통증",
        "source": "식약처 효능 원문 자체의 괄호 병기",
        "context": "공식 원문에 염좌통(삔 통증)이 그대로 존재",
        "review_status": "source_explicit_developer_review",
        "reviewer_type": "developer",
        "medication_scope": "해당 괄호 병기를 포함하는 식약처 효능 문서",
        "medical_expert_review": "not_performed",
    },
)


def validate_source_defined_terms(text, source_text):
    """원문의 명시적 괄호 병기만 치환하고 나머지 문서 전체가 그대로인 경우만 확인."""
    transformed = source_text
    applied = []
    for entry in MEDICAL_TERM_CORRESPONDENCES:
        literal = entry["official_term"] + "(" + entry["plain_candidate"] + ")"
        if literal in transformed:
            transformed = transformed.replace(literal, entry["plain_candidate"])
            applied.append(entry)
    if applied and text.strip() == transformed.strip():
        return {
            "method": "source_defined_parentheticals",
            "applied_terms": applied,
            "medical_expert_review": "not_performed",
            "scope": "공식 문서의 명시적 괄호만 치환하며 그 외 내용은 전부 동일",
        }
    return None


def effect_claim_diagnostics(text, source_texts):
    """불확실성 단서를 표시한다. 단어 탐지는 완전한 의학적 의미 판별이 아니다."""
    codes = [
        "wording_differs_from_source",
        "medical_equivalence_unverified",
        "support_or_omission_unverified",
        "additional_effect_unverified",
    ]
    source = " ".join(source_texts)
    nums = set(re.findall(r"\d+(?:[.,/]\d+)*", text))
    source_nums = set(re.findall(r"\d+(?:[.,/]\d+)*", source))
    if nums - source_nums:
        codes.append("number_or_condition_not_in_source")
    if re.search(r"복용|투여|\d\s*(?:회|세|일|개월|시간|mg|mcg|밀리그램)", text, re.I):
        codes.append("possible_dose_or_condition_claim")
    if re.search(r"완치|치료|예방", text):
        codes.append("disease_treatment_or_cure_unverified")
    return {
        "reason_codes": codes,
        "evidence": {
            "cited_sources_checked": len(source_texts),
            "unmatched_numbers": sorted(nums - source_nums),
            "note": "표현 차이 및 일부 위험 단서만 확인했으며 추가 효능·누락·의학적 동등성은 자동 판정하지 못함",
        },
    }


def controlled_effect_rephrase(item_seq, text, source_text):
    """기존 V2 규칙 중 공식 문서 전체와 동일하게 확인된 직접적 효능 표현만 허용한다."""
    original = "이 약은 식욕감퇴(식욕부진), 위부팽만감, 소화불량, 과식, 체함, 구역, 구토에 사용합니다."
    for rule in CONTROLLED_REPHRASES:
        if (
            rule["field"] == "effects"
            and rule["item_seq"] == item_seq
            and rule["claim"] == "이 약은 소화불량에 사용합니다."
            and text == rule["claim"]
            and source_text == original
        ):
            return {
                "rule_id": "v2_effect_indigestion_01",
                "source": "식약처 e약은요 공식 효능 원문",
                "review_status": "developer_review_only",
                "medical_expert_review": "not_performed",
                "scope": "지정 품목·원문·문장에만 적용; 임상적 정확성 인증 아님",
            }
    return None


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


def validate_claims(value, payload):
    """명시적 근거와 제한된 대응 규칙만 확인한다. 일반 의미 추론은 하지 않는다."""
    result = {
        "status": "review_required",
        "method": "claim_level_controlled_rules",
        "claims": [],
        "reasons": [],
        "medical_expert_review": "not_performed",
    }
    if (
        not isinstance(value, dict)
        or set(value) != {"claims"}
        or not isinstance(value["claims"], list)
        or not value["claims"]
        or len(value["claims"]) > 50
    ):
        result["reasons"].append("claims 객체 형식이 올바르지 않습니다.")
        return result
    source_index = payload.get("source_index") or {}
    products = payload.get("products") or []
    docs = {}
    for p in products:
        for doc in p.get("documents", []):
            sid = doc.get("source_id")
            meta = source_index.get(sid, {})
            if (
                sid
                and meta.get("item_seq") == p.get("item_seq")
                and isinstance(doc.get("text"), str)
            ):
                docs[sid] = {"item_seq": p.get("item_seq"), **doc}
    if not any(
        d.get("field") in SAFETY_FIELDS and d.get("text") for d in docs.values()
    ):
        result["reasons"].append("모델에 전달한 공식 안전성 근거가 없습니다.")
    for i, claim in enumerate(value["claims"]):
        row = {"index": i, "status": "review_required", "reasons": []}
        if not isinstance(claim, dict) or set(claim) - {
            "text",
            "source_ids",
            "claim_type",
            "item_seq",
        }:
            row["reasons"].append("주장 객체 형식 오류")
            result["claims"].append(row)
            continue
        text = claim.get("text")
        ids = claim.get("source_ids")
        kind = claim.get("claim_type")
        if (
            not isinstance(text, str)
            or not text.strip()
            or not isinstance(ids, list)
            or not ids
            or len(ids) > 10
            or any(not isinstance(x, str) for x in ids)
            or kind not in CLAIM_FIELDS
        ):
            row["reasons"].append("주장 내용·유형 또는 출처 형식 오류")
            result["claims"].append(row)
            continue
        row["source_ids"] = ids
        row["claim_type"] = kind
        if any(s not in docs for s in ids):
            row["status"] = "rejected"
            row["reasons"].append("Context에 없는 출처 또는 품목 매핑 오류")
            result["claims"].append(row)
            continue
        refs = [docs[s] for s in ids]
        seqs = {str(x["item_seq"]) for x in refs}
        if len(seqs) != 1 or (
            claim.get("item_seq") is not None and str(claim["item_seq"]) not in seqs
        ):
            row["status"] = "rejected"
            row["reasons"].append("주장과 출처의 품목이 다릅니다.")
            result["claims"].append(row)
            continue
        if len(products) > 1 and claim.get("item_seq") is None:
            row["reasons"].append("복수 품목에서는 주장별 item_seq가 필요합니다.")
            result["claims"].append(row)
            continue
        if any(d.get("field") != CLAIM_FIELDS[kind] for d in refs):
            row["status"] = "rejected"
            row["reasons"].append("주장 유형과 출처 필드가 다릅니다.")
            result["claims"].append(row)
            continue
        numbers = set(re.findall(r"\d+(?:[.,/]\d+)*", text))
        source_numbers = set(
            re.findall(r"\d+(?:[.,/]\d+)*", " ".join(x["text"] for x in refs))
        )
        if not numbers.issubset(source_numbers):
            row["status"] = "rejected"
            row["reasons"].append("출처에 없는 수치가 있습니다.")
            result["claims"].append(row)
            continue
        disease = re.search(r"(암|당뇨병|고혈압)(?:을|를)?\s*(?:치료|완치)", text)
        if (
            kind == "effect"
            and disease
            and all(disease.group(1) not in d["text"] for d in refs)
        ):
            row["status"] = "rejected"
            row["reasons"].append("인용한 효능 근거에 없는 질환 치료 주장입니다.")
            result["claims"].append(row)
            continue
        if (
            kind in ("precaution", "warning")
            and re.search(r"3\s*개월\s*미만", text)
            and re.search(r"복용(?:해도|할\s*수|\s*가능)", text)
            and any(
                re.search(
                    r"3\s*개월\s*미만.{0,40}(?:복용하지|투여하지)", d["text"], re.S
                )
                for d in refs
            )
        ):
            row["status"] = "rejected"
            row["reasons"].append("공식 영아 금기 조건과 반대입니다.")
            result["claims"].append(row)
            continue
        exact = [norm(text) == norm(d["text"]) for d in refs]
        mapped = [
            any(
                norm(text) == norm(rule["claim"])
                and rule["item_seq"] in seqs
                and d.get("field") == rule["field"]
                and all(norm(anchor) in norm(d["text"]) for anchor in rule["required"])
                for rule in CONTROLLED_REPHRASES
            )
            for d in refs
        ]
        if all(a or b for a, b in zip(exact, mapped)):
            row["status"] = "supported_with_checks"
            row["method"] = "exact_document" if all(exact) else "controlled_rephrase"
        else:
            row["reasons"].append(
                "문장 의미 또는 의료 조건을 결정적으로 확인할 수 없습니다."
            )
        result["claims"].append(row)
    statuses = {x["status"] for x in result["claims"]}
    if "rejected" in statuses:
        result["status"] = "rejected"
    elif statuses == {"supported_with_checks"} and not result["reasons"]:
        result["status"] = "supported_with_checks"
    return result


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
        "method": "exact_official_extract",
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
    try:
        candidate = json.loads(answer)
    except (TypeError, ValueError):
        candidate = None
    if isinstance(candidate, dict) and "claims" in candidate:
        return validate_claims(candidate, payload)
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
        doc = docs.get(match.group())
        if not claim or not doc or norm(claim) not in norm(doc["text"]):
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
    # 원문 전체를 요구해 연령·기간·예외의 부분 누락을 회피한다.
    for sid, d in docs.items():
        if d.get("field") in SAFETY_FIELDS and (
            sid not in used or norm(d["text"]) not in norm(answer)
        ):
            result["missing_precautions"].append(sid)
    if result["missing_precautions"]:
        result["reasons"].append("필수 공식 주의사항 원문 전체가 포함되지 않았습니다.")
    if not any(d.get("field") in SAFETY_FIELDS for d in docs.values()):
        result["reasons"].append("전달된 공식 주의사항 근거가 없습니다.")
    if not result["reasons"]:
        result["status"] = "verified_extractive"
    return result
