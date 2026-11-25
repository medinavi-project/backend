# MediNavi 백엔드

Python 3.12 · FastAPI · Pydantic v2 · Redis로 만든 MediNavi API 서버입니다. 다음 세 기능을 제공합니다.

- 🩺 **AI 문진**: 대화로 증상을 모아 의심 질환과 추천 진료과를 안내합니다. 근거 문서는 MedlinePlus RAG로 찾습니다.
- 🏥 **병원 찾기**: 국립중앙의료원 데이터로 진료과·거리·진료 중 여부를 따져 병원을 찾습니다.
- 💊 **약 설명**: 약봉투 사진을 OCR로 읽고, 식약처 공식 정보로 설명과 음성 안내를 만듭니다.

요청·응답 JSON과 에러 코드는 「MediNavi API 명세서 v1.1」과 Swagger(`/docs`)를 기준으로 합니다.

## API

| Method | 경로 | 하는 일 |
| --- | --- | --- |
| POST | `/api/v1/chat/sessions` | 첫 증상으로 문진 시작 (응급이면 바로 `EMERGENCY`) |
| POST | `/api/v1/chat/sessions/{session_id}/messages` | 답 처리 → 다음 질문 또는 완료 |
| GET | `/api/v1/chat/sessions/{session_id}/result` | 저장된 문진 결과 조회 |
| GET | `/api/v1/hospitals/open` | 진료과(쉼표로 최대 5개)·좌표·반경으로 병원 검색 |
| POST | `/api/v1/medications/scan` | 사진 한 장 → 약 설명 카드 + 음성 주소 |
| GET | `/api/v1/medications/audio/{audio_id}` | 약 안내 음성 (mp3) |
| GET | `/health` | API·Redis·AI 키·OCR·음성 설정 확인 |

모든 응답은 `{ "code", "message", "data" }` 형식입니다. 음성 API만 성공할 때 mp3 파일을 그대로 보냅니다.

## 동작 요약

**문진** (`app/triage`, `app/ai/providers/clova_triage_provider.py`, `app/services/chat_service.py`)
1. 응급 키워드 규칙을 먼저 검사합니다. 걸리면 LLM을 부르지 않고 119 안내를 보냅니다.
2. CLOVA Studio HCX-005가 증상 슬롯(주증상·부위·시작 시점·강도·동반 증상)을 채우고 다음 질문을 만듭니다.
3. 끝낼지는 서버가 정합니다.
   - 최대 7턴입니다.
   - 핵심 슬롯이 다 찰 때까지 계속 묻습니다.
   - 넓은 부위면 상세 위치를, 나이에 따라 진료과가 달라지는 증상이면 나이를 한 번 더 묻습니다.
   - AI가 만든 질문이 질문 문장이 아니면 서버의 기본 질문으로 바꿉니다.
4. 문진이 끝나면 MedlinePlus FAISS 인덱스(1,017건)에서 가장 가까운 문서 3개를 찾습니다. HCX-005가 그 문서를 근거로 의심 질환과 추천 진료과(표준 19종)를 만듭니다.

**병원** (`app/hospital`)
- 좌표를 역지오코딩해 반경이 걸치는 시·군·구를 모두 찾고, 진료과 × 지역 조합을 병렬로 조회합니다.
- 조회 결과는 1시간 캐시합니다.
- 결과는 진료 중인 병원이 먼저, 그다음 가까운 순입니다. 진료과마다 최대 20곳을 고릅니다.

**약** (`app/ocr`, `app/medicare_final`, `app/services`)
1. CLOVA OCR로 글자를 읽고 약 이름 후보를 뽑습니다.
2. 약 이름 사전(식약처 허가 품목 42,725개)과 문자 유사도·함량·제형으로 대조합니다. 애매하면 확정하지 않습니다.
3. 식약처 공식 정보를 5개 항목으로 나누고, HTML 태그를 지운 뒤 HCX-005로 쉽게 다시 씁니다.
4. 음성은 재생할 때 CLOVA Voice로 만듭니다. 약 이름과 효능만 읽고, 주소는 1시간 유효합니다.

## 실행 (Docker, 권장)

저장소 루트에서 실행합니다. 먼저 `backend/.env`를 준비합니다([환경변수](#환경변수) 참고).

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d
```

- Swagger: http://localhost:8000/docs
- 상태 확인: http://localhost:8000/health

`backend/app`이 컨테이너에 마운트되어 있어서, 코드를 고치면 자동으로 다시 시작합니다. `.env`를 바꿨을 때는 아래 명령으로 API 컨테이너를 다시 만듭니다.

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --force-recreate api
```

`requirements.txt`가 바뀌면 `--build`를 붙여 다시 빌드합니다. Redis는 호스트에 열리지 않고, 재시작하면 세션이 사라집니다.

## 실행 (로컬 Python)

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # 이미 있으면 덮어쓰지 말 것
```

Redis를 띄운 뒤 서버를 실행합니다.

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d redis
```

```bash
./backend/dev.sh
```

Docker API와 로컬 API를 같은 8000 포트로 동시에 띄우지 마세요.

> macOS Homebrew Python 3.12는 시스템 libexpat과 충돌해 XML을 쓰는 코드(병원·식약처·문진 엔진)가 import 단계에서 실패할 수 있습니다. 이때는 Docker로 실행하거나 테스트하세요.

## 환경변수

`backend/.env.example`을 복사해서 채웁니다. 키는 기능마다 따로 씁니다. 한 기능이 호출 한도에 걸려도 다른 기능은 멈추지 않게 하려는 것입니다.

| 변수 | 기능 | 없으면 |
| --- | --- | --- |
| `CLOVASTUDIO_API_KEY` | 문진 LLM(HCX-005), RAG 임베딩(bge-m3) | 문진 503 `AI_SERVICE_UNAVAILABLE` |
| `CLOVA_API_KEY`, `CLOVA_MODEL` | 약 설명 쉬운 말 다시 쓰기 (문진 키와 별개) | 원문 그대로 |
| `CLOVA_OCR_INVOKE_URL`, `CLOVA_OCR_SECRET`, `OCR_PROVIDER=clova` | 약봉투 OCR | 약 사진 분석 불가 |
| `MFDS_API_KEY` | 식약처 의약품 정보 | 503 `MEDICATION_INFO_UNAVAILABLE` |
| `DATA_GO_KR_KEY` | 국립중앙의료원 병·의원 | 503 `HOSPITAL_DATA_UNAVAILABLE` |
| `NCP_MAPS_CLIENT_ID`, `NCP_MAPS_CLIENT_SECRET` | 역지오코딩 (좌표 → 시·군·구) | 503 `HOSPITAL_DATA_UNAVAILABLE` |
| `CLOVA_VOICE_CLIENT_ID`, `CLOVA_VOICE_CLIENT_SECRET`, `CLOVA_VOICE_SPEAKER` | 약 안내 음성 (지도 키와 별개) | `audio_status=NOT_GENERATED` |
| `KDCA_API_KEY` | 질병관리청 건강정보 | 아직 미사용 (승인 대기) |
| `MAX_CHAT_TURNS` | 문진 최대 턴 (기본 7, 최대 10) | 기본값 |
| `SESSION_TTL_SECONDS` | 문진 세션 만료 (기본 1800초) | 기본값 |
| `HOSPITAL_RADIUS_MAX_KM`, `HOSPITAL_MAX_RESULTS` | 병원 최대 반경(10), 진료과당 최대 결과(20) | 기본값 |
| `AI_PROVIDER`, `AI_MOCK_ENABLED` | `clova_triage` 또는 개발용 `mock` | - |
| `CORS_ORIGINS` | 프론트 주소 (쉼표 구분) | - |

`.env`는 git에 올리지 않습니다. 키 값은 로그·문서·커밋에 남기지 않습니다.

## 데이터 갱신

| 데이터 | 위치 | 갱신 방법 |
| --- | --- | --- |
| MedlinePlus FAISS 인덱스 | `app/triage/medlineplus_index/` | 월 1회 아래 명령 실행 → 폴더 커밋 |
| 약 이름 사전 | `app/medicare_final/data/mfds_collection/` | 식약처 수집기 (`app/medicare_final/mfds_collector.py`) |
| 질환 → 진료과 표 | `app/triage/dept_mapping/*.csv` | 직접 편집 |

FAISS 인덱스는 로컬에서 만들어 커밋합니다. Docker 이미지에 같이 들어가서 배포 서버에서 따로 할 일이 없습니다. 생성은 약 20분 걸립니다. CLOVA 임베딩 분당 한도 때문에 1초 간격으로 호출하기 때문입니다. 중간에 멈추면 같은 명령을 다시 실행하면 이어서 합니다. 저장소 루트에서 실행하세요.

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm --no-deps -v "$PWD/backend/app/triage/medlineplus_index:/app/app/triage/medlineplus_index" api /app/.venv/bin/python -m app.triage.medlineplus_index
```

인덱스가 없어도 서버는 동작합니다. 이때 문진 결과는 MedlinePlus 실시간 검색으로 근거를 찾습니다.

## 테스트

`backend/tests`는 git에 올리지 않고 로컬에만 둡니다. CI는 ruff 검사만 합니다. XML을 쓰는 테스트가 있으니 컨테이너에서 실행하세요. 저장소 루트에서 실행합니다.

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm --no-deps -v "$PWD/backend/tests:/app/tests:ro" -v "$PWD/backend/pyproject.toml:/app/pyproject.toml:ro" api /app/.venv/bin/python -m pytest -q -p no:cacheprovider --deselect tests/test_backend.py
```

`tests/test_backend.py`는 `redis-server`를 직접 띄우는 통합 테스트라 로컬 Python으로 실행합니다.

```bash
cd backend && .venv/bin/pytest -q tests/test_backend.py
```

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
```

외부 API는 테스트에서 호출하지 않습니다. 실제 연결은 로컬 서버로 직접 확인합니다.

## 배포

main으로 PR이 머지되면 GitHub Actions가 다음을 실행합니다.

1. ruff 검사
2. Docker 이미지 빌드 → GHCR
3. SSH로 NCP 서버에 배포
4. `/health` 확인

설정과 GitHub Secrets는 [deploy/README.md](../deploy/README.md)를 보세요. `.env` 내용(`BACKEND_ENV`)을 바꾸면 수동 배포해야 반영됩니다.

- 서버: `http://101.79.27.212:8000` (Uvicorn이 직접 응답, NGINX·HTTPS 없음)
- `nginx/nginx.conf`는 HTTPS가 필요해질 때 쓸 예시이고, 지금은 사용하지 않습니다.

## 폴더 구조

```
backend/app
├── main.py              앱 생성, /health
├── core/                설정·공통 응답·에러 코드·예외 처리
├── routers/             chat, hospitals, medications
├── schemas/             API·내부 DTO
├── services/            문진 흐름, 약 스캔·식별·설명, 음성
├── repositories/        Redis 세션 (잠금·중복 전송 처리)
├── ai/providers/        문진 엔진 어댑터 (clova_triage), 개발용 mock
├── triage/              hosAI 문진 엔진, RAG, FAISS 인덱스, 진료과 표
├── hospital/            병원 검색 (공공데이터·역지오코딩·진료시간)
├── ocr/                 CLOVA OCR
└── medicare_final/      식약처 조회·약 이름 사전·DUR
```

`app/triage`와 `app/hospital`은 팀원의 hosAI 코드를 거의 그대로 가져온 것입니다. 우리 API 계약과는 `clova_triage_provider.py` 어댑터로 연결해서, 엔진이 바뀌면 파일을 교체하면 됩니다. 두 폴더는 ruff 검사에서 제외되어 있습니다.

## 로그와 개인정보

- AI·공공데이터·음성 호출이 실패하면 세션 ID, 에러 종류, 외부 응답 코드만 남깁니다.
- 사용자가 입력한 증상 문장, 사진, API 키는 로그에 남기지 않습니다.
- 사진은 처리한 뒤 저장하지 않습니다. 문진 세션은 Redis에 30분, 음성 문장은 1시간만 보관합니다.
- 이 서비스의 결과는 진단이 아닌 진료 안내입니다.
