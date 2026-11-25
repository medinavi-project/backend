# 문진 LLM 계약 (ChatInput / ChatOutput)

문진 엔진(`app/ai/providers/clova_triage_provider.py`, CLOVA Studio HCX-005)과 API 사이의 입출력 규격입니다. 코드 기준은 `app/schemas/ai.py`이고, JSON Schema는 [llm-response.schema.json](llm-response.schema.json)입니다(`ChatOutput.model_json_schema()`로 생성).

## ChatInput

| 필드 | 설명 |
| --- | --- |
| `messages` | 지금까지의 대화 (user / assistant) |
| `symptoms` | 지금까지 채운 증상 슬롯 |
| `answered_fields` | 이미 답한 슬롯 이름 |
| `turn_count` | 지금까지 질문 수 |
| `max_turns` | 최대 질문 수 (기본 `MAX_CHAT_TURNS=7`, 최대 10) |
| `engine_state` | 엔진이 다음 턴에 이어 쓸 내부 상태 (세션에 저장) |

## ChatOutput

| 필드 | 설명 |
| --- | --- |
| `action` | `ASK_FOLLOW_UP` · `COMPLETE` · `EMERGENCY` |
| `symptom_updates` | 이번 답변에서 새로 채운 슬롯 |
| `answered_fields` | 갱신된 답변 슬롯 목록 |
| `next_question` | `ASK_FOLLOW_UP`일 때만. 항상 질문 문장 |
| `analysis` | `COMPLETE`일 때만. 의심 질환·이유·추천 진료과(표준 이름) |
| `emergency_guidance` | `EMERGENCY`일 때만. 119·응급실 안내 |
| `engine_state` | 다음 턴에 넘길 상태 |
| `references` | RAG로 참고한 MedlinePlus 문서 (최대 3개) |

검증 규칙은 `ChatOutput`의 validator가 강제합니다: action에 맞는 필드만 채워져야 하고, 나머지는 비어 있어야 합니다.

## 서버가 결정하는 것 (LLM에 맡기지 않음)

- **응급**: LLM 호출 전 키워드 규칙으로 판단해 바로 `EMERGENCY`를 돌려줍니다.
- **종료 시점**: 핵심 슬롯이 다 찼거나 `max_turns`에 닿으면 서버가 `COMPLETE`로 바꿉니다.
- **추가 질문**: 넓은 부위(배·머리 등)면 상세 위치, 나이에 따라 달라지는 증상이면 나이를 서버가 묻습니다.
- **질문 형식**: LLM이 준 `next_question`이 질문 문장(`?`·`요`·`까`로 끝남)이 아니면 버리고 서버 질문으로 대체합니다.
- **진료과 이름**: `recommended_department`는 표준 진료과 이름으로 정규화합니다.
