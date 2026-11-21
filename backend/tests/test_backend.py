import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from app.ai.providers.mock_provider import MockAIProvider
from app.core.config import Settings
from app.core.exceptions import BusinessException
from app.core.response_codes import HTTP_STATUS, Code
from app.main import create_app
from app.repositories.redis_session_repository import SessionRepository
from app.schemas.ai import AnalysisOutput, ChatOutput, SymptomUpdates
from app.services.data_providers import DemoDataProvider
from app.services.hospital_service import HospitalService


def create(client):
    response = client.post(
        "/api/v1/chat/sessions", json={"initial_message": "배가 아파요"}
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["session_id"]


def test_flow_idempotency_ttl(client, redis_url):
    sid = create(client)
    redis = Redis.from_url(redis_url, decode_responses=True)
    key = f"medinavi:chat:session:{sid}"
    assert 1790 <= redis.ttl(key) <= 1800
    payload = {"client_message_id": str(uuid4()), "message": "어제부터"}
    url = f"/api/v1/chat/sessions/{sid}/messages"
    response = client.post(url, json=payload)
    assert response.json()["data"]["turn_count"] == 2
    redis.expire(key, 100)
    assert client.post(url, json=payload).json() == response.json()
    assert redis.ttl(key) <= 100
    assert client.post(url, json={**payload, "message": "다른 내용"}).status_code == 409
    for _ in range(3):
        response = client.post(
            url, json={"client_message_id": str(uuid4()), "message": "추가 설명"}
        )
    assert response.json()["data"]["status"] == "COMPLETED"
    assert redis.ttl(key) > 1700
    result = client.get(f"/api/v1/chat/sessions/{sid}/result").json()["data"]
    assert result["analysis_source"] == "MOCK"
    assert result["references"] == [] and result["suspected_diseases"] == []
    assert result["assessment_status"] == "INSUFFICIENT_EVIDENCE"
    assert (
        client.post(
            url, json={"client_message_id": str(uuid4()), "message": "추가"}
        ).json()["code"]
        == "SESSION_ALREADY_COMPLETED"
    )
    redis.expire(key, 100)
    client.get(f"/api/v1/chat/sessions/{sid}/result")
    assert redis.ttl(key) <= 100
    redis.delete(key)
    assert (
        client.get(f"/api/v1/chat/sessions/{sid}/result").json()["code"]
        == "SESSION_NOT_FOUND"
    )
    redis.close()


def test_validation_errors_openapi(client):
    assert client.get("/health").status_code == 200
    invalid = client.post("/api/v1/chat/sessions", json={"initial_message": "   "})
    assert invalid.status_code == 422
    assert invalid.json()["data"]["field_errors"][0]["field"] == "initial_message"
    assert set(invalid.json()) == {"code", "message", "data"}
    assert client.get("/unknown").json()["code"] == "RESOURCE_NOT_FOUND"
    assert client.get("/api/v1/chat/sessions").json()["code"] == "METHOD_NOT_ALLOWED"
    schema = client.get("/openapi.json").json()
    assert len(schema["paths"]) == 6
    assert "201" in schema["paths"]["/api/v1/chat/sessions"]["post"]["responses"]
    bad = client.get(
        "/api/v1/hospitals/open",
        params={"department": "내과", "latitude": 91, "longitude": 127},
    )
    assert bad.status_code == 422
    assert (
        client.get(
            "/api/v1/hospitals/open",
            params={"department": "   ", "latitude": 37, "longitude": 127},
        ).status_code
        == 422
    )


def test_medications_disabled_ocr(client):
    assert (
        client.post(
            "/api/v1/medications/scan",
            files={"file": ("a.png", b"example", "image/png")},
        ).json()["code"]
        == "OCR_SERVICE_UNAVAILABLE"
    )
    assert (
        client.post(
            "/api/v1/medications/scan", files={"file": ("a.txt", b"a", "text/plain")}
        ).status_code
        == 415
    )
    assert (
        client.post(
            "/api/v1/medications/scan", files={"file": ("a.png", b"", "image/png")}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/medications/scan",
            files={"file": ("a.png", b"x" * (5 * 1024 * 1024 + 1), "image/png")},
        ).status_code
        == 413
    )


def test_hospital_filter():
    service = HospitalService(DemoDataProvider())
    args = ("내과", 37.3943, 126.9568, 3000)
    now = datetime(2026, 11, 20, 11, tzinfo=ZoneInfo("Asia/Seoul"))
    assert service.search(*args, now=now).total_count == 2
    assert service.search("피부과", 0, 0, 100, now=now).total_count == 0
    assert service.search(*args, now=now.replace(hour=23)).total_count == 0


def test_redis_outage(settings):
    settings.REDIS_URL = "redis://127.0.0.1:1/0"
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        assert client.get("/health").json()["code"] == "SESSION_STORE_UNAVAILABLE"


def test_failed_analysis_and_unexpected(settings):
    class Failing(MockAIProvider):
        async def process_chat(self, request):
            if request.turn_count == 2:
                raise BusinessException(Code.AI_REQUEST_TIMEOUT)
            return await super().process_chat(request)

    settings.MAX_CHAT_TURNS = 2
    app = create_app(settings, Failing())

    @app.get("/crash")
    def crash():
        raise RuntimeError("secret")

    with TestClient(app, raise_server_exceptions=False) as client:
        sid = create(client)
        response = client.post(
            f"/api/v1/chat/sessions/{sid}/messages",
            json={"client_message_id": str(uuid4()), "message": "추가"},
        )
        assert response.status_code == 504
        data = client.get(f"/api/v1/chat/sessions/{sid}/result").json()["data"]
        assert (
            data["status"] == "FAILED" and data["failure_code"] == "AI_REQUEST_TIMEOUT"
        )
        assert client.get("/crash").json() == {
            "code": "INTERNAL_SERVER_ERROR",
            "message": "서버 내부 오류가 발생했습니다.",
            "data": None,
        }


def test_merge_correction_and_early_finish(settings):
    class Extractor(MockAIProvider):
        async def process_chat(self, request):
            if request.turn_count == 1:
                return ask(
                    {"main_symptom": "복통", "body_location": "배"}, "언제부터인가요?"
                )
            if request.turn_count == 2:
                return ask({"body_location": "오른쪽", "onset": "어제"}, "강도는요?")
            return complete({"severity": 0, "additional_symptoms": []})

    with TestClient(create_app(settings, Extractor())) as client:
        sid = create(client)
        url = f"/api/v1/chat/sessions/{sid}/messages"
        data = client.post(
            url, json={"client_message_id": str(uuid4()), "message": "정정"}
        ).json()["data"]
        assert data["collected_symptoms"]["main_symptom"] == "복통"
        assert data["collected_symptoms"]["body_location"] == "오른쪽"
        data = client.post(
            url, json={"client_message_id": str(uuid4()), "message": "동반 증상 없음"}
        ).json()["data"]
        assert data["status"] == "COMPLETED" and data["turn_count"] == 3
        assert data["collected_symptoms"]["severity"] == 0
        assert data["collected_symptoms"]["additional_symptoms"] == []


def test_concurrent_requests(settings):
    class Slow(MockAIProvider):
        async def process_chat(self, request):
            await asyncio.sleep(0.1)
            return await super().process_chat(request)

    with TestClient(create_app(settings, Slow())) as client:
        sid = create(client)
        payload = {"client_message_id": str(uuid4()), "message": "동시 요청"}
        url = f"/api/v1/chat/sessions/{sid}/messages"
        with ThreadPoolExecutor(2) as pool:
            responses = list(
                pool.map(lambda _: client.post(url, json=payload), range(2))
            )
        assert sorted(r.status_code for r in responses) == [200, 409]
        assert client.post(url, json=payload).json()["data"]["turn_count"] == 2


async def test_real_expiry_and_lock(redis_url):
    redis = AsyncRedis.from_url(redis_url, decode_responses=True)
    from app.schemas.chat import Session

    repo = SessionRepository(redis, ttl=1)
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    session = Session(session_id=uuid4(), created_at=now, updated_at=now)
    try:
        await repo.create(session)
        await asyncio.sleep(1.1)
        with pytest.raises(BusinessException) as error:
            await repo.get(session.session_id)
        assert error.value.code == Code.SESSION_NOT_FOUND
    finally:
        await redis.aclose()


def test_mock_requires_explicit_permission():
    with pytest.raises(ValueError):
        Settings(_env_file=None, AI_PROVIDER="mock", AI_MOCK_ENABLED=False)
    assert HTTP_STATUS[Code.AI_REQUEST_TIMEOUT] == 504


def test_analysis_visible_without_blocking_get(settings):
    import threading

    started = threading.Event()
    finish = threading.Event()

    class Delayed(MockAIProvider):
        async def process_chat(self, request):
            if request.turn_count == 2:
                started.set()
                while not finish.is_set():
                    await asyncio.sleep(0.01)
            return await super().process_chat(request)

    settings.MAX_CHAT_TURNS = 2
    with TestClient(create_app(settings, Delayed())) as client:
        sid = create(client)
        url = f"/api/v1/chat/sessions/{sid}/messages"
        payload = {"client_message_id": str(uuid4()), "message": "두번째"}
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(client.post, url, json=payload)
            try:
                assert started.wait(timeout=5)
                data = client.get(f"/api/v1/chat/sessions/{sid}/result").json()["data"]
                assert data["status"] == "ANALYZING"
                assert data["assessment_status"] is None
                assert (
                    client.post(url, json=payload).json()["code"]
                    == "SESSION_ANALYSIS_IN_PROGRESS"
                )
            finally:
                finish.set()
            assert pending.result(timeout=5).json()["data"]["status"] == "COMPLETED"


async def test_stale_lock_cannot_commit(redis_url):
    from app.schemas.chat import Session

    redis = AsyncRedis.from_url(redis_url, decode_responses=True)
    repo = SessionRepository(redis)
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    session = Session(session_id=uuid4(), created_at=now, updated_at=now)
    try:
        await repo.create(session)
        async with repo.locked(session.session_id) as token:
            await redis.set(repo.key(session.session_id) + ":lock", "new-owner", ex=30)
            session.turn_count = 99
            with pytest.raises(BusinessException) as error:
                await repo.save(session, token)
            assert error.value.code == Code.RESOURCE_CONFLICT
        assert (await repo.get(session.session_id)).turn_count == 0
        assert await redis.get(repo.key(session.session_id) + ":lock") == "new-owner"
    finally:
        await redis.aclose()


def ask(updates=None, question="추가 질문입니다."):
    updates = updates or {}
    return ChatOutput(
        action="ASK_FOLLOW_UP",
        symptom_updates=SymptomUpdates(**updates),
        answered_fields=[key for key, value in updates.items() if value is not None],
        next_question=question,
        analysis=None,
    )


def complete(updates=None, assessment="PRELIMINARY"):
    updates = updates or {}
    return ChatOutput(
        action="COMPLETE",
        symptom_updates=SymptomUpdates(**updates),
        answered_fields=[key for key, value in updates.items() if value is not None],
        next_question=None,
        analysis=AnalysisOutput(
            assessment_status=assessment,
            symptom_summary="통합 AI 응답 테스트 요약",
            suspected_diseases=[
                {"disease_name": "가상 테스트 질환", "reason": "계약 검증용 가상 내용"}
            ]
            if assessment == "PRELIMINARY"
            else [],
            recommended_department="가상 테스트 진료과"
            if assessment == "PRELIMINARY"
            else None,
        ),
    )


def test_unified_single_call_per_turn_and_public_contract(settings):
    class Unified(MockAIProvider):
        def __init__(self):
            self.requests = []

        async def process_chat(self, request):
            self.requests.append(request.model_copy(deep=True))
            if request.turn_count == 1:
                return ask({"main_symptom": "두통"}, "언제부터인가요?")
            return complete({"onset": "어제", "additional_symptoms": []})

    provider = Unified()
    with TestClient(create_app(settings, provider)) as client:
        sid = create(client)
        payload = {
            "client_message_id": str(uuid4()),
            "message": "어제부터 묵직하고 동반 증상은 없습니다.",
        }
        url = f"/api/v1/chat/sessions/{sid}/messages"
        response = client.post(url, json=payload)
        data = response.json()["data"]
        assert set(data) == {
            "session_id",
            "status",
            "next_question",
            "turn_count",
            "collected_symptoms",
            "result_available",
            "emergency_message",
        }
        assert data["status"] == "COMPLETED" and data["result_available"]
        assert data["next_question"] is None
        assert data["collected_symptoms"]["main_symptom"] == "두통"
        assert data["collected_symptoms"]["onset"] == "어제"
        assert "action" not in data and "analysis" not in data
        path = f"/api/v1/chat/sessions/{sid}/result"
        result = client.get(path).json()["data"]
        assert result["suspected_diseases"][0]["disease_name"] == "가상 테스트 질환"
        assert result["recommended_department"] == "가상 테스트 진료과"
        assert result["references"] == []
        assert client.get(path).json()["data"] == result
        assert client.post(url, json=payload).json() == response.json()
        assert len(provider.requests) == 2
        assert provider.requests[1].turn_count == 2
        assert provider.requests[1].symptoms.main_symptom == "두통"
        assert provider.requests[1].answered_fields == ["main_symptom"]
        assert [m.role for m in provider.requests[1].messages] == [
            "user",
            "assistant",
            "user",
        ]
        assert "묵직" in provider.requests[1].messages[-1].content


@pytest.mark.parametrize("end_turn", [1, 3, 5])
def test_action_controls_finish_not_field_completeness(settings, redis_url, end_turn):
    import json

    class Unified(MockAIProvider):
        def __init__(self):
            self.calls = 0

        async def process_chat(self, request):
            self.calls += 1
            if request.turn_count >= end_turn:
                return complete(
                    {"severity": 2},
                    "INSUFFICIENT_INFORMATION" if end_turn == 5 else "PRELIMINARY",
                )
            return ask(
                {
                    "main_symptom": "두통",
                    "body_location": "이마",
                    "onset": "어제",
                    "severity": 2,
                    "additional_symptoms": [],
                },
                "하루 중 언제 더 심한가요?",
            )

    provider = Unified()
    with TestClient(create_app(settings, provider)) as client:
        response = client.post(
            "/api/v1/chat/sessions", json={"initial_message": "묵직한 두통"}
        )
        assert response.status_code == 201
        data = response.json()["data"]
        sid = data["session_id"]
        assert data["status"] == ("COMPLETED" if end_turn == 1 else "IN_PROGRESS")
        for turn in range(2, end_turn + 1):
            response = client.post(
                f"/api/v1/chat/sessions/{sid}/messages",
                json={"client_message_id": str(uuid4()), "message": "아침에 심합니다."},
            )
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert data["turn_count"] == turn
            assert data["status"] == (
                "COMPLETED" if turn == end_turn else "IN_PROGRESS"
            )
        result = client.get(f"/api/v1/chat/sessions/{sid}/result").json()["data"]
        assert result["status"] == "COMPLETED" and provider.calls == end_turn
        redis = Redis.from_url(redis_url, decode_responses=True)
        try:
            saved = json.loads(redis.get(f"medinavi:chat:session:{sid}"))
            assert len(saved["messages"]) == 2 * end_turn - 1
            assert saved["symptoms"]["severity"] == 2
            if end_turn > 1:
                assert "additional_symptoms" in saved["answered_fields"]
        finally:
            redis.close()


def test_invalid_final_turn_cannot_advance_or_overwrite(settings, redis_url):
    import json

    class InvalidLast(MockAIProvider):
        async def process_chat(self, request):
            return ask({"body_location": "이마"})

    settings.MAX_CHAT_TURNS = 2
    with TestClient(create_app(settings, InvalidLast())) as client:
        sid = create(client)
        response = client.post(
            f"/api/v1/chat/sessions/{sid}/messages",
            json={"client_message_id": str(uuid4()), "message": "어제부터"},
        )
        assert (
            response.status_code == 502
            and response.json()["code"] == "AI_RESPONSE_INVALID"
        )
        redis = Redis.from_url(redis_url, decode_responses=True)
        try:
            saved = json.loads(redis.get(f"medinavi:chat:session:{sid}"))
            assert saved["turn_count"] == 1 and len(saved["messages"]) == 2
            assert saved["status"] == "FAILED"
        finally:
            redis.close()


def test_sparse_patch_preserves_existing_values_and_explicit_none_does_not_clear(
    settings,
):
    class Patcher(MockAIProvider):
        async def process_chat(self, request):
            if request.turn_count == 1:
                return ask({"onset": "어제", "additional_symptoms": ["발열"]})
            if request.turn_count == 2:
                return ask({"onset": None})
            return complete({"severity": 0, "additional_symptoms": []})

    with TestClient(create_app(settings, Patcher())) as client:
        sid = create(client)
        url = f"/api/v1/chat/sessions/{sid}/messages"
        data = client.post(
            url, json={"client_message_id": str(uuid4()), "message": "추가 내용"}
        ).json()["data"]
        assert data["collected_symptoms"]["onset"] == "어제"
        assert data["collected_symptoms"]["additional_symptoms"] == ["발열"]
        data = client.post(
            url,
            json={"client_message_id": str(uuid4()), "message": "발열은 없다고 정정"},
        ).json()["data"]
        assert data["collected_symptoms"]["onset"] == "어제"
        assert data["collected_symptoms"]["additional_symptoms"] == []
        assert data["collected_symptoms"]["severity"] == 0


def test_photo_to_guidance_single_request(settings):
    settings.OCR_PROVIDER = "mock"

    class Counting(MockAIProvider):
        calls = 0

        async def explain_medication_text(self, text):
            self.calls += 1
            assert text.startswith("[MOCK OCR]")
            return await super().explain_medication_text(text)

    provider = Counting()
    with TestClient(create_app(settings, provider)) as client:
        response = client.post(
            "/api/v1/medications/scan",
            files={"file": ("demo.png", b"test fixture", "image/png")},
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["status"] == "OCR_ONLY"
        assert data["identified_count"] == 0
        assert provider.calls == 0


def test_ocr_only_never_calls_ai(settings):
    settings.OCR_PROVIDER = "mock"

    class ForbiddenAI(MockAIProvider):
        async def explain_medication_text(self, text):
            raise AssertionError("OCR-only endpoint must not invoke AI")

    with TestClient(create_app(settings, ForbiddenAI())) as client:
        response = client.post(
            "/api/v1/medications/scan",
            files={"file": ("a.png", b"fixture", "image/png")},
        )
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "OCR_ONLY"


def test_separate_ocr_endpoint_removed(client):
    assert "/api/v1/medications/ocr" not in client.get("/openapi.json").json()["paths"]
    assert client.post("/api/v1/medications/ocr").status_code == 404


def test_medication_has_only_scan_endpoint(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert [path for path in paths if path.startswith("/api/v1/medications/")] == [
        "/api/v1/medications/scan"
    ]
    assert (
        client.post(
            "/api/v1/medications/guide", json={"item_seqs": ["DEMO-M001"]}
        ).status_code
        == 404
    )
