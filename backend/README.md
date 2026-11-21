# MediNavi 백엔드

Python 3.12 / FastAPI / Pydantic v2 / redis.asyncio 기반 발표용 백엔드입니다.
최신 추가 지시에 따라 AI는 외부 시스템으로 분리하고 Gemini는 기본 JSON 통신과 멀티턴 데이터 흐름 검증에만 사용합니다.

## 구현 범위

| API | 동작 |
| --- | --- |
| `POST /api/v1/chat/sessions` | `initial_message`로 최초 턴 처리, UUID v4 세션 생성, HTTP 201 |
| `POST /api/v1/chat/sessions/{session_id}/messages` | `client_message_id`와 `message`로 멀티턴 처리 및 멱등 응답 |
| `GET /api/v1/chat/sessions/{session_id}/result` | 저장된 상태·결과 조회, AI 재호출 및 TTL 갱신 없음 |
| `GET /api/v1/hospitals/open` | 가상 병원 JSON에서 진료과·거리·서울 현재 운영시간 필터 |
| `POST /api/v1/medications/scan` | 이미지 검증 후 OCR 텍스트 반환 (AI 미호출) |
| `GET /health` | API·Redis 가용성, AI 설정 유무, OCR/TTS 비활성 상태 |

모든 업무 API의 성공·오류는 `code/message/data` 구조입니다. Swagger와 OpenAPI 자체 응답은 문서 형식입니다.

각 턴에서 Gemini를 한 번의 논리 요청으로 호출하며, **통합 응답(action, symptom_updates, answered_fields, next_question, analysis)**을 받습니다. 전체 대화·누적 증상·확인된 항목·현재/최대 턴을 입력으로 전달합니다. ASK_FOLLOW_UP이면 누적 증상을 갱신하고 질문을 전달하며, COMPLETE이면 마지막 답변의 갱신값까지 반영한 뒤 최종 분석을 저장합니다. 별도의 분석 LLM 재호출은 없습니다. 필수 필드가 채워져도 AI가 추가 질문을 선택하면 계속 문진합니다. 최대 5턴에서는 COMPLETE가 필수이고, 부족한 정보가 있으면 INSUFFICIENT_INFORMATION 결과로 종료할 수 있습니다. 한도를 어기거나 계약이 잘못된 응답은 거부합니다.

최종 결과는 증상 요약·최대 3개의 의심 질환과 이유·추천 진료과이며, 정보나 근거가 부족하면 빈 질환 배열·null 진료과를 허용합니다. references=[]는 RAG 미연결을 뜻합니다. 별도 질병 판단 알고리즘과 응급 판단 알고리즘은 없습니다. EMERGENCY Enum은 향후 연결용으로 유지합니다. 이 서버를 실제 의료 판단에 사용하지 않습니다.

OCR·Embedding·RAG·TTS·파인튜닝·CLOVA·공식 병원/의약품 API는 미구현입니다. Gemini 이미지 인식으로 OCR을 대신하지 않습니다. `audio_status=DISABLED`, `audio_url=null`입니다. 병원과 약품은 실재하지 않는 가상 데이터이며 `data_source=DEMO`입니다.

## 로컬 실행

Python 3.12와 Redis가 필요합니다. 저장소 루트에서:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

이미 생성된 `.env`가 있으면 덮어쓰지 말고 편집합니다. `backend/.env`에서:

```dotenv
AI_PROVIDER=gemini
GEMINI_API=실제_키
GEMINI_MODEL=gemini-2.5-flash-lite
AI_MOCK_ENABLED=false
OCR_PROVIDER=disabled
TTS_PROVIDER=disabled
```

키가 없으면 Gemini 의존 API는 `503 AI_SERVICE_UNAVAILABLE`을 반환하며 자동 Mock 전환은 없습니다. `/health`의 `ai_configured`는 설정 존재만 표시하고 실제 인증 성공을 뜻하지 않습니다.
모델을 사용할 수 없으면 오류를 반환하며 다른 모델로 자동 변경하지 않습니다. API 지원·계정별 한도는 실제 통합 테스트로 확인합니다.

Redis를 별도 터미널에서 실행합니다:

```bash
redis-server --bind 127.0.0.1 --port 6379 --save '' --appendonly no
```

또는 저장소 루트에서 `docker compose -f docker-compose.yml -f docker-compose.local.yml up -d redis`를 실행합니다. 이미 6379 포트를 사용 중이면 기존 Redis와 중복 실행하지 않습니다.

```bash
# backend 디렉터리
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

- Swagger: http://localhost:8000/docs
- OpenAPI: http://localhost:8000/openapi.json
- 상태 확인: http://localhost:8000/health

이 작업 환경의 Homebrew Python 3.12는 시스템 libexpat과 충돌했습니다. 동일한 `XML_SetAllocTrackerActivationThreshold` 오류가 발생하면 올바른 Python 설치를 사용하거나 이 환경에서 다음과 같이 실행할 수 있습니다. 시스템 라이브러리 자체는 수정하지 않았습니다.

```bash
DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib .venv/bin/python -m pip install -r requirements.txt
```

## Mock 모드

`backend/.env`에서 세 값을 명시적으로 설정합니다:

```dotenv
APP_ENV=development
AI_PROVIDER=mock
AI_MOCK_ENABLED=true
```

Mock은 실제 AI 통신 없이 질문과 요약 테스트 결과를 반환합니다. 자동 증상 추출은 하지 않으며 5턴에 완료됩니다. 운영 환경에서는 Mock 설정이 거부됩니다. 병원·약품의 DEMO 데이터와 AI Mock 모드는 서로 다른 개념입니다.

## 요청 예시

```bash
curl -s http://localhost:8000/api/v1/chat/sessions \
  -H 'Content-Type: application/json' \
  -d '{"initial_message":"배가 아파요."}'

# 응답의 session_id를 아래 UUID 대신 입력합니다.
curl -s http://localhost:8000/api/v1/chat/sessions/550e8400-e29b-41d4-a716-446655440000/messages \
  -H 'Content-Type: application/json' \
  -d '{"client_message_id":"ae94c17d-744c-4a5d-b73e-c35c07b14e10","message":"어제부터 아파요."}'

curl -s http://localhost:8000/api/v1/chat/sessions/550e8400-e29b-41d4-a716-446655440000/result

curl -s -G http://localhost:8000/api/v1/hospitals/open \
  --data-urlencode 'department=내과' \
  --data-urlencode 'latitude=37.3943' \
  --data-urlencode 'longitude=126.9568' \
  --data-urlencode 'radius=3000'



# CLOVA OCR 설정 후 사진의 텍스트를 추출합니다.
curl -s http://localhost:8000/api/v1/medications/scan -F 'file=@example.png'
```

메시지 ID는 매번 새 UUID로 생성하고, 네트워크 오류로 같은 메시지를 재전송할 때만 동일 ID를 사용합니다.

## 세션과 동시성

- 키: `medinavi:chat:session:{session_id}`, 기본 TTL 1800초.
- 최초 메시지도 1턴으로 계산합니다. 정상 처리 시 TTL을 갱신합니다. 질문을 계속하는 응답에서도 동일한 메시지 ID의 재전송은 저장된 응답을 반환합니다.
- 대화·증상·분석 결과·확인된 필드·멱등 응답 기록을 동일 세션 JSON에 보관합니다.
- 결과 조회 및 이미 처리된 메시지 재전송은 TTL을 연장하지 않습니다.
- 빈 동반 증상 배열은 `answered_fields`로 미응답과 구분합니다. null은 기존 값을 지우지 않습니다.
- Redis `SET NX EX`로 세션별 잠금을 얻고, Lua 스크립트가 소유권과 세션 존재를 확인한 후 상태와 멱등 기록을 원자적으로 저장합니다.
- 처리 중 동시 요청은 409를 받습니다. 완료 후 동일 ID·동일 본문 재전송은 기존 응답을 받으며 동일 ID·다른 본문은 409입니다.
- 잠금 수명은 통합 요청의 파싱 재시도까지 고려해 여유 있게 설정합니다. 잠금이 만료된 처리의 저장은 거부됩니다.
- 완료/실패 상태는 새 메시지를 받지 않습니다. `ANALYZING`은 메시지의 통합 LLM 처리 중 임시 상태로 기록됩니다. GET은 이 상태를 조회할 수 있습니다.
- AI 분석 예외는 `FAILED`와 안전한 `failure_code`를 기록합니다. GET은 HTTP 200으로 상태를 반환합니다. 생성 요청에서 AI가 실패하면 정상 세션을 생성하지 않습니다.
- 프로세스 강제 종료 후 `ANALYZING` 세션의 자동 복구는 현재 없습니다. TTL 만료 또는 새 세션이 필요합니다. Redis 장애 시 503입니다.
- 분석 실패로 종료된 요청은 정상 응답 멱등 기록이 없으며, 재전송 시 종료 세션 오류를 받습니다.

## Gemini 오류 처리

`GEMINI_API`를 설정 객체에서 명시적으로 전달합니다. SDK 기본 키 환경변수에 의존하지 않습니다.
`google-genai` import와 호출은 `app/ai/providers/gemini_provider.py`에만 있습니다.
구조화 JSON을 Pydantic으로 검증하고 파싱 실패에 한해 1회 재시도합니다. 429·인증 실패·시간 초과에는 자동 재시도하지 않습니다. 제공된 `Retry-After`는 429 응답에 전달합니다.
인증·모델 사용 불가 503, 호출 실패 502, 잘못된 JSON 502, 시간 초과 504로 구분합니다.
API 키·대화 원문·이미지를 로그로 남기지 않으며 이미지 업로드는 영구 저장하지 않습니다.

## 테스트

```bash
cd backend
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

일반 테스트는 실제 Gemini API를 호출하지 않습니다. Redis 통합 테스트는 설치된 `redis-server`를 임시 포트에서 실행하고 종료합니다. 서버가 없으면 테스트가 실패하며 메모리 저장소로 대체하지 않습니다.

실제 연결 테스트는 `backend/.env`에 키와 모델을 설정한 뒤 명시적으로 실행합니다. 실제 요청이므로 계정의 사용량을 소비할 수 있습니다.

```bash
cd backend
RUN_GEMINI_INTEGRATION=1 .venv/bin/python -m pytest -m integration -q
```

## Docker Compose

저장소 루트에서 실행합니다. 먼저 `backend/.env`를 준비합니다.

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f api
# 종료
docker compose down
```

API는 `127.0.0.1:8000`에 공개하며 Redis는 기본적으로 호스트에 공개하지 않습니다. 로컬 Python 개발용 Redis만 실행할 때 `docker-compose.local.yml`을 추가하면 `127.0.0.1:6379`에 공개됩니다. 필요하면 `REDIS_LOCAL_PORT`로 변경하고 로컬 `REDIS_URL`도 맞춥니다. 컨테이너 내부 Redis 주소는 `redis://redis:6379/0`으로 덮어씁니다. Redis 디스크 영구 저장은 비활성화되어 재시작 시 세션이 사라질 수 있습니다.
Docker 빌드 컨텍스트에서 `.env`와 `.venv`는 제외됩니다. 키는 실행 시 `env_file`로 주입됩니다. 컨테이너도 전용 가상환경에서 실행합니다.
Nginx는 필수 서비스가 아니며 `../nginx/nginx.conf`는 호스트에서 실행하는 Nginx용 예시입니다. Nginx를 컨테이너로 옮길 때 upstream을 해당 네트워크의 `api:8000`으로 변경합니다. 도메인·HTTPS는 미설정입니다.

## 교체할 파일

- AI 교체: `app/ai/base.py`의 Protocol을 구현한 Provider를 추가하고 `app/ai/factory.py`, `app/core/config.py`의 허용 값을 수정합니다. 외부 DTO·Router·세션 Repository는 유지합니다.
- 외부 문진·분석 AI: `app/ai/base.py`의 process_chat을 구현합니다. `app/services/analysis_service.py`는 통합 응답의 analysis를 기존 결과 DTO로 변환하며 별도 LLM을 호출하지 않습니다. 현재 결과를 RAG로 검증된 분석으로 표시하지 않습니다.
- OCR: `app/services/ocr_service.py`의 비활성 구현에 외부 Client를 주입합니다. `/scan` 계약은 유지합니다.
- 공공데이터: `app/services/data_providers.py`의 인터페이스를 구현한 Provider를 주입합니다.
- TTS: 복약 서비스에 별도 Client를 추가하고 실제 생성 결과에 따라 `audio_status`와 `audio_url`을 설정합니다.

## 파일과 의존성

`app/core`는 설정·응답·코드·예외, `app/schemas`는 API/내부 DTO, `app/routers`는 HTTP,
`app/services`는 흐름 조정, `app/repositories`는 Redis, `app/ai`는 교체 가능한 AI 통신,
`app/data`는 가상 데이터, `tests`는 자동 검증을 담당합니다.
직접 사용하는 라이브러리는 FastAPI, Uvicorn, Pydantic, pydantic-settings, redis, httpx/httpx2,
google-genai, python-multipart, pytest, pytest-asyncio, Ruff이며, 전이 의존성까지 `requirements.txt`에 freeze했습니다.

## 개발용 핫 리로드

저장소 루트에서 Docker 개발 모드를 실행합니다:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d
docker compose logs -f api
```

`backend/app`을 컨테이너에 읽기 전용 마운트하며 Python 코드 수정 시 Uvicorn이 자동 재시작됩니다. Redis는 재시작하지 않으므로 세션이 유지됩니다. `.env`를 변경하면 다음 명령으로 API를 다시 생성합니다. 의존성 변경은 위 `--build` 명령으로 다시 빌드합니다.

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --force-recreate api
```

로컬 Python 개발은 Redis 실행 후 저장소 루트에서:

```bash
./backend/dev.sh
```

또는 `backend`에서 직접 실행합니다:

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir app
```

Docker API와 로컬 API를 같은 8000 포트에 동시에 실행하지 않습니다. 로컬 실행으로 전환하려면 `docker compose stop api`로 Docker API만 중지합니다. 기본 리로더는 `.py` 파일을 감시하므로 JSON 데이터나 `.env` 변경 시에는 수동 재시작이 필요합니다.


### 외부 LLM 통합 계약

입출력 규격과 예시는 [docs/llm-contract.md](docs/llm-contract.md), 기계 판독 JSON Schema는 [docs/llm-response.schema.json](docs/llm-response.schema.json)에 있습니다. 프론트엔드 엔드포인트와 응답 DTO는 유지합니다. LLM의 action과 analysis를 그대로 프론트에 노출하지 않고 기존 status, next_question, collected_symptoms, result_available 및 결과 조회 응답으로 변환합니다. 기존 완료 세션 결과는 재실행하지 않으므로 새 세션에서 확인합니다.

## 사진 업로드: scan 단일 진입점

`POST /api/v1/medications/scan`에 multipart `file`로 JPEG/PNG를 전송합니다. 현재는 CLOVA OCR만 호출하고 `data.recognized_text`를 반환합니다. Gemini는 호출하지 않습니다. 별도 `/ocr` 엔드포인트는 제거했습니다.

```dotenv
OCR_PROVIDER=clova
CLOVA_OCR_INVOKE_URL=발급받은_Invoke_URL
CLOVA_OCR_SECRET=OCR_도메인의_Secret_Key
OCR_REQUEST_TIMEOUT_SECONDS=30
```

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --force-recreate api
curl -s http://localhost:8000/api/v1/medications/scan -F 'file=@/절대경로/사진.png'
```

응답 예시:

```json
{"code":"SUCCESS","message":"텍스트 인식이 완료되었습니다.","data":{"recognized_text":"읽힌 글자"}}
```

학습된 AI가 준비되면 OCRService.scan에서 extract 결과를 AI에 전달하도록 연결합니다. 사진 요청 주소와 multipart 형식은 그대로 사용하고, AI 연결 시 최종 응답 DTO는 별도로 확정합니다. 의약품 엔드포인트는 /scan 하나이며 /guide와 /ocr는 제거했습니다.

공식 문서: https://api.ncloud-docs.com/docs/en/ai-application-service-ocr-ocr

## 제공된 Medicare 의약품 AI 통합 (현재 scan 동작)

`/scan`은 OCR 추출 후 CLOVA로 약품명을 추출하고, 식약처 품목 검색과 공식 자료·DUR 확인을 거쳐 제공된 Medicare 모듈로 답변을 생성합니다. 통합 코드는 `app/medicare/`에 있으며 원본 다운로드 폴더는 수정하지 않았습니다. 동기 외부 호출은 스레드풀에서 실행합니다.

추가 환경변수:

```dotenv
MEDICATION_AI_ENABLED=true
MFDS_API_KEY=식약처_공공데이터_서비스키
CLOVA_API_KEY=CLOVA_Studio_API키
CLOVA_MODEL=HCX-005
```

CLOVA OCR Secret과 CLOVA Studio API 키는 서로 다릅니다. OCR만 시험하려면 MEDICATION_AI_ENABLED=false로 설정합니다. 키를 제공하지 않은 실제 의약품 조회·생성은 503으로 실패합니다.

공통 응답 data는 recognized_text, status, medications, answer, official_drug_info, official_precautions, sources, warnings, validation_status, processing_status, data_source, analysis_source, identification_method, audio_url, audio_status, medical_disclaimer를 포함합니다. status는 COMPLETED / NEEDS_CONFIRMATION / INCOMPLETE / OCR_ONLY입니다. 네트워크·인증 오류는 공통 오류 응답으로 변환합니다.

자동 식별은 OCR 원문에 있는 이름과 공식 제품명이 정규화 후 유일하게 완전 일치하고 허가 상태가 정상이며 검색이 잘리지 않은 경우에만 허용합니다. 이것은 사용자 확인과 동일하지 않습니다. 함량·제형이 불명확하거나 후보가 여러 개면 NEEDS_CONFIRMATION과 후보를 반환하며 선택용 추가 API는 만들지 않았습니다. 생성문은 원본 모듈의 출처·원문 일치 검증을 통과해야 answer에 표시됩니다. 자료가 부족하면 INCOMPLETE로 공식 자료와 부족 사유를 반환합니다. 모든 약물 상호작용의 안전성을 보장하지 않습니다.

OCR 1회 이후 이름 추출·식약처 조회·DUR·토큰 계산·생성 등 여러 외부 호출이 이어집니다. 공개 API 요청은 한 번이지만 내부 호출은 여러 번이며 시간이 오래 걸릴 수 있습니다. 키를 설정한 실제 서비스 전체 호출은 별도 검증이 필요합니다.

### 약품 식별 조정 및 OCR 없는 실연동 검증

CLOVA 약품명 추출은 순수 JSON뿐 아니라 JSON 코드 블록 및 단일 `약품명` 배열 키를 정규화해 처리합니다. 추출 이름이 실제 입력에 존재하는지 검증은 유지합니다.
공식 제품명 뒤 괄호 성분명, 공백 및 단위 표기 차이는 동일 제품 비교에서 허용합니다. 마이크로그램 단위 정규화 순서도 수정했습니다. 함량·제형이 맞는 유사 후보를 우선 정렬하되, 오타 유사도만으로 제품을 확정하지 않습니다. 이름·함량이 다른 후보, 복수 동일 후보, 잘린 검색은 자동 확정하지 않습니다.
OCR 없이 `타이레놀정500밀리그람`으로 실제 CLOVA·식약처 흐름을 검증했고 품목코드 202106092와 매칭됐습니다. 최종 생성은 원본 인용 검증에서 review_required / UNVERIFIED_OUTPUT으로 보류됐습니다. 이는 연결 실패가 아닌 생성 답변 검증 결과이며 공식 정보는 별도로 반환됩니다.

### 생성 답변 검증 조정

일반 효능·보관 설명의 바꿔 쓰기를 허용하고, 실제 출처 ID 연결을 요구합니다. 용법·용량·수치·금기 등 보호 항목은 인용 출처의 연속 원문과 일치해야 합니다. 공식 주의사항 전체는 official_precautions로 제공하므로 생성 답변에 전체 문서를 반복하도록 요구하지 않습니다. 통과 상태는 source_linked_summary이며 verified_extractive로 표시하지 않습니다. 이 검사는 일반 요약의 의미적 정확성을 보증하지 않으며 의료 전문가 검수 상태는 not_performed입니다. 알 수 없는 출처나 출처 없는 문장, 보호 항목 불일치는 계속 보류합니다.

### 최종 AI 응답 규격 변경

현재 의약품 생성은 자유 문장 인용 대신 `medications[{item_seq,summary[{text,source_ids}]}]` JSON을 요구합니다. summary는 해당 품목의 effects 출처만 사용하며 용법·용량·수치·금기 설명은 허용하지 않습니다. 공식 용법·용량은 official_drug_info, 주의사항은 official_precautions로 제공합니다. 공개 응답 ai_summary에 구조화 요약을 추가하고 기존 answer에는 이를 표시용 문자열로 변환합니다. validation_status=structured_source_linked는 구조와 출처 연결만 확인했다는 의미이며 의학적 의미 검증은 아닙니다.
OCR 없는 실제 CLOVA·식약처 재검증에서 타이레놀정500밀리그람(품목 202106092)이 COMPLETED / processing_status=OK / structured_source_linked로 반환됐습니다.

### OCR 문자 유사도 후보 검색

약품 검색 결과가 없으면 이름의 앞 4글자·3글자로 최대 두 번 추가 검색하고, 공식 결과 중 문자 유사도 70점 이상을 후보로 제공합니다. 후보에는 similarity_score(0~100, 확률 아님), strength_match, form_match, permit_status, match_reason을 포함합니다. 유사 후보는 함량·제형 호환성을 우선하여 정렬하며, 자동 식별은 기존 유일한 공식 이름 일치 규칙을 유지합니다. 후보 유사도만으로 제품을 확정하지 않습니다. 검색 결과가 잘리면 자동 확정하지 않습니다. 일반 접두 검색으로 찾지 못하는 첫 글자 오인식 등은 여전히 누락될 수 있습니다.

### 다중 OCR 표기·부분 결과

LLM 추출 결과에 OCR 원문 내 약품명 형태의 독립 행을 합쳐 검색합니다. 같은 표기는 중복 제거하되 비슷하다는 이유로 서로 다른 표기를 합치지 않습니다. 약별 review_reason은 SEARCH_FAILED, INACTIVE_PRODUCT, SEARCH_TRUNCATED, IDENTITY_UNCERTAIN 등을 반환합니다. 식별 가능한 품목만 공식 정보·안내 생성에 전달하고 미식별 품목은 후보로 남깁니다. 일부만 안내가 생성되면 status=PARTIAL입니다. 공식 자료·DUR 검증 자체가 부족하면 INCOMPLETE를 유지합니다. 누락된 약을 포함한 전체 처방의 상호작용 안전성을 확인한 것으로 표시하지 않습니다.

벡터 검색 교체 지점은 services/drug_search_provider.py의 DrugSearchProvider입니다. 현재 OfficialTextSearch만 구현되어 있고 실제 벡터DB·임베딩은 설치하거나 실행하지 않았습니다. 벡터 Provider도 공식 품목 코드, 검색 오류·잘림 정보를 보존해야 하며 점수만으로 약을 확정할 수 없습니다.

### 공식 정보와 DUR 상태 분리

공식 품목 정보가 있으면 생성 보류·DUR 불확실 시에도 PARTIAL로 반환합니다. information_status=AVAILABLE, dur_status=NOT_VERIFIED와 dur_details를 제공합니다. AI answer는 검증 통과 시에만 반환하며, 없는 부작용 자료는 생성하지 않습니다. 실제 샘플 진단에서는 화록소정 부작용 자료 없음과 화록소정/부스코판당의정 병용 관계 unknown이 기존 중단 원인이었습니다. 부분 응답이 전체 처방의 안전성을 확인했다는 의미는 아닙니다.

### DUR와 효능 생성 경로 분리

생성 컨텍스트는 공식 품목 확인을 통과하고 effects 문서가 존재하는 약의 품목 코드·이름·효능 문서만 포함합니다. DUR 미확인이나 별도 부작용 필드 부재는 효능 생성 자체를 차단하지 않습니다. DUR 결과는 별도 반환하며 용법·주의사항은 공식 자료에 유지됩니다. 생성 JSON의 구조·출처·보호 항목 검증은 계속 적용됩니다. 실제 제공된 OCR 입력으로 DUR 단계를 넘어 생성까지 도달했으나, 해당 실행의 생성 응답은 review_required / UNVERIFIED_OUTPUT으로 보류됐습니다. 공식 정보는 PARTIAL로 제공됩니다.

## Medicare_AI_Backend_Final 인계 버전 연결

현재 /scan은 app/medicare_final의 로컬 extract_candidates와 get_medication_info를 사용합니다. OCR 원문을 CLOVA 약품명 추출에 보내던 경로는 사용하지 않습니다. 외부 검색에는 독립 행으로 확인된 약품 형태 후보만 보내며, 문장에 섞인 후보는 PRIVACY_REVIEW_REQUIRED로 보류합니다. 이는 휴리스틱 필터이며 완전한 개인정보 탐지 또는 사람의 검토를 의미하지 않습니다.

기존 단일 사진 요청 흐름을 유지하기 위해 공식 이름이 유일하게 일치하고 정상 허가인 경우의 자동 식별 정책은 유지합니다. 인계 문서의 사람 확인 단계와 동일하지 않으며 identification_method=EXACT_OFFICIAL_NAME으로 표시합니다. 모호한 후보는 자동 확정하지 않습니다.

새 모듈의 반환 status가 ready이고 validation_result.status가 verified_extractive 또는 supported_with_checks일 때만 ai_summary를 공개합니다. partial 상태나 검증 실패면 생성 문장을 숨기고 공식 정보·원문 주의사항·DUR 상세를 반환합니다. 기존 공통 code/message/data 및 /scan 진입점은 유지합니다. 증상 문진용 모듈은 변경하지 않았습니다. 기존 app/medicare는 회귀 비교용으로 남아 있으나 /scan의 생성·공식 정보 조회는 새 패키지를 사용합니다.

실제 기존 OCR 샘플 재검증: 전체 PARTIAL, 식별된 품목 정보 조회 READY, 생성 검증 verified_extractive 확인. 미식별/보류 품목은 후보와 사유로 반환합니다. 회귀 테스트 74개 통과, 실제 원본 새 모듈의 모든 오프라인 시험을 그대로 실행한 것은 아닙니다.

### 최종 모듈 연결 후 약품 회귀 수정

로컬 OCR 후보 어댑터는 독립 행의 약품 형태만 추출하여 금액·일반 서술 오탐과 함량 중간 잘림을 줄입니다. 범위 밖의 문장형/여러 약이 붙은 행은 누락될 수 있으며 자동 교정하지 않습니다. 넓은 유사 검색의 잘림은 전체 이름으로 최대 2페이지 재조회하여 구분합니다. 그 재조회도 불완전하거나 정확한 후보가 복수이면 자동 식별하지 않습니다.
실제 기존 OCR 원문 재검증: 후보 9개→5개 표기, 화록소정과 부스코판당의정 식별 성공. 세프로바이·씨프로바이 표기는 별개로 보존되며 후보/비활성 사유 반환. PARTIAL / information_status=AVAILABLE, 새 모듈은 partial 또는 review_required일 때 생성 요약을 숨깁니다. 약물 관련 선별 회귀 테스트 17개 통과. 이번 수정에서는 증상 문진 흐름은 변경하지 않았습니다.

### 판별/불확실 DTO 리스트와 부분 요약

응답에 identified_count, uncertain_count, summarized_count, identified_medications, uncertain_medications를 추가했습니다. 판별 수는 공식 품목코드별 고유 개수이며 불확실 수는 OCR 후보 표기 개수로 동일 약의 오인식이 중복될 수 있습니다. identified_medications는 품목별 recognized_names, official_info, precautions, summary, summary_status를 포함합니다. 판별된 약만 요약하며 품목별 verified_extractive/supported_with_checks 문장만 공개합니다. 다른 약의 실패 또는 전체 partial 때문에 통과 문장까지 숨기지 않습니다. 전체 JSON이 rejected이면 모든 생성문을 보류합니다. 검증 실패한 약은 summary=[]와 WITHHELD 상태, 조회된 공식 정보만 반환합니다.

### 현재 프론트 scan 응답: 5항목 설명

공개 data는 status, identified_count, uncertain_count, medications, uncertain_medications입니다. 약 카드에는 item_seq, name, description, guidance_status가 있으며 description은 purpose/how_to_take/precautions/side_effects/storage의 문자열 또는 null입니다. notice, sources, medical_disclaimer, 내부 검증/DUR 원문은 공개 DTO에서 제외합니다. 기존 내부 상세 DTO는 검증·매핑에만 사용합니다. 목적 설명은 검증된 요약 또는 공식 효능, 나머지는 공식 용법·주의사항·부작용·보관 정보에서 매핑하며 없는 항목은 null입니다. 이 변경은 프론트 응답 계약 변경이므로 해당 5개 description 키를 사용해야 합니다.
