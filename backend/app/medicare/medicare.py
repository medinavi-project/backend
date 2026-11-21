"""팀 연동 진입점. 네트워크 토큰 계산 및 유료 생성은 별도 명시적 동의가 필요하다."""

import json

from .rag import search_drugs, build_drug_context
from .prompts import SYSTEM_PROMPT, USER_TEMPLATE
from .clova_client import ClovaClient, ClovaError, LIMITS
from .response_validator import official_data
from .structured_answer import validate_structured


def generate_medication_answer(
    confirmed_item_seqs,
    question,
    prescription_info=None,
    *,
    mfds_client=None,
    clova_client=None,
    allow_tokenizer=False,
    allow_generation=False,
    max_input_tokens=32000,
    max_output_tokens=2048,
    token_safety_margin=256,
    max_context_chars=200000,
):
    result = {
        "status": "incomplete",
        "answer": None,
        "confirmed_drugs": [],
        "sources": {},
        "dur_result": {},
        "warnings": [],
        "input_tokens": None,
        "output_tokens": None,
        "latency": None,
        "tokenizer_content_tokens": None,
        "generated_answer": None,
        "official_drug_info": [],
        "official_precautions": [],
        "validation_result": {"status": "not_run"},
    }
    if max_input_tokens < 1 or token_safety_margin < 0 or max_output_tokens < 1:
        raise ValueError("토큰 예산 설정이 올바르지 않습니다.")
    context = build_drug_context(
        confirmed_item_seqs,
        question,
        prescription_info,
        client=mfds_client,
        max_context_chars=max_context_chars,
    )
    result["confirmed_drugs"] = [
        {"item_seq": p["item_seq"], "drug_name": p["drug_name"]}
        for p in context.get("products", [])
    ]
    result["sources"] = context.get("sources", {})
    result["official_drug_info"], result["official_precautions"] = official_data(
        context.get("products", []), confirmed_item_seqs or []
    )
    # 공식 정보는 LLM 전달 여부와 별도로 출처를 제공한다.
    result["sources"] = dict(result["sources"])
    for item in result["official_precautions"]:
        if item.get("source_id") and item.get("url"):
            result["sources"].setdefault(
                item["source_id"],
                {
                    "url": item["url"],
                    "item_seq": item["item_seq"],
                    "field": item["field"],
                    "retrieved_at": item["retrieved_at"],
                },
            )
    result["dur_result"] = {
        "by_drug": context.get("dur_selected", {}),
        "pairs": context.get("pairs_selected", []),
    }
    for p in context.get("products", []):
        for f in ("warnings", "precautions", "interactions"):
            result["warnings"].extend(
                {
                    "item_seq": p["item_seq"],
                    "source_id": d["source_id"],
                    "field": f,
                    "text": d["text"],
                }
                for d in p[f]
            )
    for seq, types in context.get("dur_selected", {}).items():
        for kind, item in types.items():
            if kind != "interaction" and item.get("records"):
                result["warnings"].append(
                    {
                        "item_seq": seq,
                        "field": "dur_" + kind,
                        "status": item["status"],
                        "records": item["records"],
                    }
                )
    for pair in context.get("pairs_selected", []):
        if pair.get("records"):
            result["warnings"].append(
                {
                    "field": "dur_pair",
                    "item_seqs": pair["item_seqs"],
                    "status": pair["status"],
                    "records": pair["records"],
                }
            )
    result["warnings"].extend({"message": x} for x in context.get("reasons", []))
    # Build a separate effects-only generation context. DUR and missing unrelated
    # fields remain public warnings and never become generation evidence.
    verified = {str(item["item_seq"]) for item in result["official_drug_info"]}
    products = []
    source_index = {}
    for product in context.get("products", []):
        if str(product["item_seq"]) not in verified:
            continue
        docs = [
            d
            for d in product.get("documents", [])
            if d.get("field") == "effects"
            and d.get("text")
            and d.get("source_id") in result["sources"]
        ]
        if not docs:
            continue
        products.append(
            {
                "item_seq": product["item_seq"],
                "drug_name": product["drug_name"],
                "documents": [
                    {"source_id": d["source_id"], "field": "effects", "text": d["text"]}
                    for d in docs
                ],
            }
        )
        for doc in docs:
            source_index[doc["source_id"]] = result["sources"][doc["source_id"]]
    if not products:
        result["status"] = "evidence_incomplete"
        return result
    context["context"] = json.dumps(
        {"products": products, "source_index": source_index}, ensure_ascii=False
    )
    if len(products) < len(confirmed_item_seqs):
        result["warnings"].append(
            {"message": "공식 효능 근거가 확보된 품목만 AI 요약에 포함했습니다."}
        )
    if not allow_tokenizer:
        result["status"] = "tokenizer_not_authorized"
        return result
    model_client = clova_client or ClovaClient()
    limits = LIMITS[model_client.model]
    if max_output_tokens > limits["output"]:
        raise ValueError("출력 토큰 예산이 모델 제한을 초과합니다.")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(context=context["context"])},
    ]
    try:
        measured = model_client.tokenize(messages, allow=True)
        count = measured["content_tokens"]
        result["tokenizer_content_tokens"] = count
        result["tokenizer_latency"] = measured["latency"]
        result["token_count_note"] = (
            "메시지 내용별 계산기 수치 합계; 메시지 프레이밍 토큰은 미확인"
        )
        if (
            count + token_safety_margin > min(max_input_tokens, limits["input"])
            or count + token_safety_margin + max_output_tokens > limits["context"]
        ):
            result["status"] = "token_limit"
            return result
        if not allow_generation:
            result["status"] = "generation_not_authorized"
            return result
        output = model_client.generate(
            messages, allow=True, max_tokens=max_output_tokens, temperature=0.1
        )
        result.update(
            {k: output.get(k) for k in ("input_tokens", "output_tokens", "latency")}
        )
        if output.get("finish_reason") == "length":
            result["status"] = "incomplete_output"
            return result
        result["validation_result"] = validate_structured(
            output["answer"], context.get("context")
        )
        if result["validation_result"]["status"] not in {
            "verified_extractive",
            "source_linked_summary",
            "structured_source_linked",
        }:
            result["warnings"].append(
                {
                    "message": "생성 답변의 출처 또는 원문·주의사항 대응을 확인하지 못해 제공을 보류했습니다."
                }
            )
            result["status"] = "unverified_output"
            return result
        result["status"], result["answer"], result["generated_answer"] = (
            "ok",
            result["validation_result"]["display_answer"],
            result["validation_result"]["display_answer"],
        )
        result["warnings"].append(
            {
                "message": "출처 연결과 보호 항목의 원문 일치를 검사했습니다. 일반 설명의 의미적 정확성과 의료 전문가 검수는 확인되지 않았으며 공식 주의사항 원문을 함께 확인해야 합니다."
            }
        )
        return result
    except ClovaError as exc:
        result["status"] = exc.category
        return result
