# 외부 LLM 통합 응답 계약

이 문서는 현재 백엔드가 검증하고 처리하는 규격입니다. 문진과 최종 분석은 동일한 `process_chat` 응답을 사용하며, 별도의 분석 LLM 호출은 없습니다. 정상 응답은 턴당 한 번 호출합니다. Gemini에서 형식이 잘못된 응답에 한해 한 번 재시도합니다.

## 입력

```json
{
  "messages": [
    {"role": "user", "content": "머리가 아파요."},
    {"role": "assistant", "content": "언제부터인가요?"},
    {"role": "user", "content": "어제부터 이마가 묵직하게 아파요."}
  ],
  "symptoms": {
    "main_symptom": "두통",
    "body_location": "머리",
    "onset": null,
    "severity": null,
    "additional_symptoms": [],
    "region": null
  },
  "answered_fields": ["main_symptom", "body_location"],
  "turn_count": 2,
  "max_turns": 5
}
```

`messages`는 이번 사용자 답변까지 포함합니다. `symptoms`와 `answered_fields`는 이번 답변을 반영하기 전의 누적값입니다. `turn_count`는 이번 사용자 답변까지 포함한 턴 수입니다. 초기 생성도 1턴입니다.

## 출력: 공통 5개 필드

다음 필드는 매번 모두 필수이며, 추가 필드는 허용하지 않습니다.

| 필드 | 타입 | 규칙 |
| --- | --- | --- |
| action | enum | ASK_FOLLOW_UP 또는 COMPLETE |
| symptom_updates | object | 이번에 확인·정정한 필드만 포함. 없으면 {} |
| answered_fields | string[] | 이번 응답의 확인 항목. 중복 금지. 갱신 객체에 명시된 null 아닌 값만 참조 |
| next_question | string/null | ASK_FOLLOW_UP에서 질문 하나 필수, 공백 제외 1~1000자. COMPLETE에서는 null |
| analysis | object/null | ASK_FOLLOW_UP에서는 null. COMPLETE에서는 아래 최종 결과 필수 |

### 중간 응답

```json
{
  "action": "ASK_FOLLOW_UP",
  "symptom_updates": {"body_location": "이마", "onset": "어제부터"},
  "answered_fields": ["body_location", "onset"],
  "next_question": "통증 강도는 0에서 10 중 어느 정도인가요?",
  "analysis": null
}
```

### 마지막 응답

```json
{
  "action": "COMPLETE",
  "symptom_updates": {"severity": 2},
  "answered_fields": ["severity"],
  "next_question": null,
  "analysis": {
    "assessment_status": "INSUFFICIENT_INFORMATION",
    "symptom_summary": "강도는 2점으로 확인됐으나 발생 시점과 동반 증상은 확인되지 않았습니다.",
    "suspected_diseases": [],
    "recommended_department": null
  }
}
```

## 갱신 객체

허용 필드는 main_symptom, body_location, onset, severity, additional_symptoms, region입니다.
문자열 필드는 string 또는 null이고, severity는 0~10 정수 또는 null입니다. additional_symptoms는 문자열 배열이며 null을 허용하지 않습니다.

- 생략한 필드는 기존 값을 유지합니다. null도 기존 값을 삭제하지 않습니다.
- 0점은 정상 값입니다. 거짓 값으로 간주해 누락하지 않습니다.
- additional_symptoms는 전체 목록을 교체합니다. 새로 발견한 일부만 반환하지 않습니다.
- 명시적으로 동반 증상 없음이면 []와 answered_fields의 additional_symptoms를 반환합니다.
- 동반 증상이 미확인이면 필드를 생략하고 answered_fields에도 넣지 않습니다.
- 기존 정보를 정정할 때는 새로운 값을 반환합니다. 사실 검증 책임은 외부 AI에 있으며 백엔드는 구조를 검증합니다.
- JSON에 전용 필드가 없는 양상 등은 전체 대화로 전달됩니다.

## 최종 분석 객체

네 필드 모두 필수이며 추가 필드는 허용하지 않습니다.

| 필드 | 규격 |
| --- | --- |
| assessment_status | PRELIMINARY / INSUFFICIENT_INFORMATION / INSUFFICIENT_EVIDENCE |
| symptom_summary | 공백 제외 1~2000자. 정보 부족 시 부족한 내용을 포함 |
| suspected_diseases | 0~3개. 각 항목은 비어 있지 않은 disease_name, reason 문자열 |
| recommended_department | 공백 제외 1~100자 문자열 또는 null |

이번 symptom_updates까지 반영해 최종 분석을 작성합니다. 최종 턴에서도 질환을 억지로 채우지 않습니다.

## 백엔드 처리

ASK_FOLLOW_UP은 갱신값 저장 후 기존 API의 next_question으로 전달합니다. COMPLETE는 마지막 갱신과 결과를 같은 세션에 원자적으로 저장하고 status=COMPLETED, result_available=true를 반환합니다. 초기 세션 생성 응답에는 기존 계약대로 result_available 필드가 없습니다. 최종 분석은 기존 GET 결과 API로 조회합니다.

최대 턴에는 COMPLETE가 필수입니다. 최대 턴에서 ASK_FOLLOW_UP을 반환하면 계약 위반으로 거부합니다. 정상 형식을 지키되 부족한 경우 COMPLETE와 INSUFFICIENT_INFORMATION을 반환해야 합니다. JSON Schema의 기본 구조 제약 외에도 백엔드는 이 교차 필드·턴 규칙을 검증합니다.

세션 ID, 프론트 응답 래퍼(code/message/data), TTL, status, references, medical_disclaimer는 백엔드가 관리합니다. LLM 응답에 생성하지 않습니다. 프론트 API와 DTO는 변경하지 않았습니다.

## 교체 지점

`app/ai/base.py`의 AIProvider.process_chat(ChatInput) -> ChatOutput을 구현한 외부 Provider를 factory에 등록하면 됩니다. 외부 HTTP 응답을 ChatOutput으로 검증해 반환해야 하며 원본 SDK 객체를 Service에 전달하지 않습니다. `llm-response.schema.json`은 Pydantic에서 생성한 기계 판독 스키마입니다. Gemini 호환용 일부 키워드 제거는 해당 Provider 내부에서만 수행합니다.
