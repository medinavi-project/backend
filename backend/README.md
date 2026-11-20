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
| `POST /api/v1/medications/scan` | 업로드 계약 유지, 형식·크기·빈 파일 검사 후 HTTP 503 `OCR_SERVICE_UNAVAILABLE` |
| `POST /api/v1/medications/guide` | 가상 약품 조회 후 제공된 원문 문장만 선택하는 최소 Gemini 요약 |
| `GET /health` | API·Redis 가용성, AI 설정 유무, OCR/TTS 비활성 상태 |

모든 업무 API의 성공·오류는 `code/message/data` 구조입니다. Swagger와 OpenAPI 자체 응답은 문서 형식입니다.

문진 필수 항목 수집 또는 최대 5턴에서 분석 요청을 수행합니다. 분석은 외부 Gemini에서 **증상 요약·최대 3개의 의심 질환과 이유·추천 진료과**를 받아 검증하고 저장합니다. 별도 질병 판단 알고리즘은 없습니다. 정보와 근거가 충분한 경우 `PRELIMINARY`, 부족한 경우 `INSUFFICIENT_INFORMATION` 또는 `INSUFFICIENT_EVIDENCE`를 반환하며 질환 배열이나 진료과를 비워둘 수 있습니다. `references=[]`는 RAG 미연결을 뜻합니다. 응급 판단 알고리즘과 정교한 문진 정책은 구현하지 않았습니다. `EMERGENCY` Enum은 향후 외부 AI 연동을 위해 유지하며 현재 자체적으로 응급 판정을 하지 않습니다. 이 서버를 실제 의료 판단에 사용하지 않습니다.

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

curl -s http://localhost:8000/api/v1/medications/guide \
  -H 'Content-Type: application/json' \
  -d '{"item_seqs":["DEMO-M001"]}'

# OCR은 현재 503을 반환합니다.
curl -s http://localhost:8000/api/v1/medications/scan -F 'file=@example.png'
```

메시지 ID는 매번 새 UUID로 생성하고, 네트워크 오류로 같은 메시지를 재전송할 때만 동일 ID를 사용합니다.

## 세션과 동시성

- 키: `medinavi:chat:session:{session_id}`, 기본 TTL 1800초.
- 최초 메시지도 1턴으로 계산합니다. 정상 처리 시 TTL을 갱신합니다.
- 대화·증상·분석 결과·확인된 필드·멱등 응답 기록을 동일 세션 JSON에 보관합니다.
- 결과 조회 및 이미 처리된 메시지 재전송은 TTL을 연장하지 않습니다.
- 빈 동반 증상 배열은 `answered_fields`로 미응답과 구분합니다. null은 기존 값을 지우지 않습니다.
- Redis `SET NX EX`로 세션별 잠금을 얻고, Lua 스크립트가 소유권과 세션 존재를 확인한 후 상태와 멱등 기록을 원자적으로 저장합니다.
- 처리 중 동시 요청은 409를 받습니다. 완료 후 동일 ID·동일 본문 재전송은 기존 응답을 받으며 동일 ID·다른 본문은 409입니다.
- 잠금 수명은 문진·요약의 최대 4회 호출 제한 시간 합계에 30초를 더합니다. 잠금이 만료된 처리의 저장은 거부됩니다.
- 완료/실패 상태는 새 메시지를 받지 않습니다. `ANALYZING`은 동기 분석 호출 직전에 Redis에 기록됩니다. GET은 이 상태를 조회할 수 있습니다.
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
- RAG/질병 분석: `app/services/analysis_service.py`가 교체 지점입니다. 현재 Gemini의 잠정 분석 결과를 근거 검증된 분석으로 표시하지 않습니다.
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
