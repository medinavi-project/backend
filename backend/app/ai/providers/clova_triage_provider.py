"""Adapter: backend chat contract <-> hosAI triage engine (app/triage).

The engine is vendored nearly as-is so a teammate's update can be dropped in.
Only this file knows both shapes.
"""

import copy
import re

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.core.exceptions import BusinessException
from app.core.response_codes import Code
from app.schemas.ai import ChatOutput

# (words the model may write, words that count as the user having said it).
# ponytail: fixed cue list, no negation handling ("열은 없어요" counts as said);
# extend when a new invented-fact pattern shows up.
CUES = [
    (("공복", "빈속"), ("공복", "빈속", "배고플", "굶")),
    (("야간", "밤에", "새벽"), ("야간", "밤", "새벽", "잘때", "자다")),
    (("식후", "식사 후", "식사후"), ("식후", "먹고", "먹은", "식사후")),
    (
        ("출혈", "혈변", "흑색변", "토혈", "피를"),
        ("출혈", "피가", "피를", "피나", "혈변", "검은변"),
    ),
    (("발열", "고열"), ("열", "발열")),
    (("구토",), ("구토", "토했", "토해", "토할")),
    (("설사",), ("설사", "묽은")),
    (("체중 감소", "체중감소"), ("체중", "살이빠", "살빠")),
    (("황달",), ("황달", "노랗")),
]
CHECK_WORDS = ("확인", "해당", "있는지", "여부")
FALLBACK_REASON = (
    "말씀하신 증상과 관련이 있을 수 있어 참고 자료를 함께 안내합니다. "
    "정확한 판단은 진료가 필요합니다."
)


def _drop_unstated(analysis, said):
    """Remove reason sentences that state a symptom the user never mentioned
    (e.g. "공복·야간 통증" copied from a reference document)."""
    said = re.sub(r"\s+", "", said)
    for disease in (analysis or {}).get("suspected_diseases") or []:
        kept = []
        for sentence in re.split(r"(?<=[.!?])\s+", disease["reason"]):
            flat = re.sub(r"\s+", "", sentence)
            invented = any(
                any(re.sub(r"\s+", "", w) in flat for w in written)
                and not any(w in said for w in heard)
                for written, heard in CUES
            )
            if not invented or any(w in sentence for w in CHECK_WORDS):
                kept.append(sentence)
        disease["reason"] = " ".join(kept).strip() or FALLBACK_REASON
    return analysis


def _turn(message, state, turn_count, max_turns, said=""):
    """Same steps as rag_chatbot.process_turn_for_backend, but keeps the RAG
    documents so the result can cite them (the engine discards them)."""
    from app.triage.chatbot import (
        generate_final_analysis,
        has_sufficient_info,
        run_triage_turn,
        validate_backend_response,
    )
    from app.triage.rag_chatbot import retrieve_medlineplus_rag

    payload = run_triage_turn(
        user_message=message,
        current_state=state,
        turn_count=turn_count,
        max_turns=max_turns,
    )
    references = []
    if payload["action"] == "COMPLETE" and has_sufficient_info(state):
        rag = retrieve_medlineplus_rag(state)
        analysis = generate_final_analysis(state, rag)
        # The user's own words plus symptoms the engine extracted from them.
        facts = [state.get(k) for k in ("main_symptom", "body_location", "onset")]
        facts += state.get("additional_symptoms") or []
        said = " ".join([said, *(f for f in facts if isinstance(f, str))])
        payload = {**payload, "analysis": _drop_unstated(analysis, said)}
        references = [
            f"{d.get('title_ko') or d['title']} | MedlinePlus.gov | {d['url']}"
            for d in rag.get("medlineplus_docs", [])
            if d.get("url")
        ]
    validate_backend_response(payload)
    return payload, references


class ClovaTriageProvider:
    source = "CLOVA_TRIAGE"

    def __init__(self, settings):
        self.configured = bool(settings.CLOVASTUDIO_API_KEY.get_secret_value())

    async def process_chat(self, request):
        if not self.configured:
            raise BusinessException(Code.AI_SERVICE_UNAVAILABLE)
        from app.triage.chatbot import init_state

        # The engine keeps _last_question and _condition inside its state dict
        # (HANDOFF 1-3 ①), so the whole dict round-trips through the session.
        state = copy.deepcopy(request.engine_state) or init_state()
        try:
            payload, references = await run_in_threadpool(
                _turn,
                request.messages[-1].content,
                state,
                request.turn_count,
                request.max_turns,
                " ".join(m.content for m in request.messages if m.role == "user"),
            )
        except ValueError:  # engine's own contract check failed
            raise BusinessException(Code.AI_RESPONSE_INVALID) from None
        try:
            return ChatOutput.model_validate(
                {
                    "action": payload["action"],
                    "symptom_updates": payload["symptom_updates"],
                    "answered_fields": payload["answered_fields"],
                    "next_question": payload["next_question"],
                    "analysis": payload["analysis"],
                    "emergency_guidance": payload["emergency_guidance"],
                    "engine_state": state,
                    "references": references,
                },
                strict=False,
            )
        except ValidationError:
            raise BusinessException(Code.AI_RESPONSE_INVALID) from None

    async def close(self):
        return None
