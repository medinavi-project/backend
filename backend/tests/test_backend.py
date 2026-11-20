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
from app.schemas.ai import ChatOutput
from app.schemas.chat import Symptoms
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
    assert len(schema["paths"]) == 7
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
    assert (
        client.post(
            "/api/v1/medications/guide", json={"item_seqs": ["missing"]}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/medications/guide", json={"item_seqs": ["DEMO-M001"] * 2}
        ).status_code
        == 422
    )
    data = client.post(
        "/api/v1/medications/guide", json={"item_seqs": ["DEMO-M001"]}
    ).json()["data"]
    assert data["audio_status"] == "DISABLED" and data["audio_url"] is None
    assert data["data_source"] == "DEMO"


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
        async def analyze_symptoms(self, symptoms):
            raise BusinessException(Code.AI_REQUEST_TIMEOUT)

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
            n = len(request.messages)
            if n == 1:
                return ChatOutput(
                    symptom_updates=Symptoms(main_symptom="복통", body_location="배"),
                    answered_fields=["main_symptom", "body_location"],
                    next_question="언제부터인가요?",
                )
            if n == 3:
                return ChatOutput(
                    symptom_updates=Symptoms(body_location="오른쪽", onset="어제"),
                    answered_fields=["body_location", "onset"],
                    next_question="강도와 동반 증상은요?",
                )
            return ChatOutput(
                symptom_updates=Symptoms(severity=0),
                answered_fields=["severity", "additional_symptoms"],
                next_question="확인했습니다.",
            )

    with TestClient(create_app(settings, Extractor())) as client:
        sid = create(client)
        url = f"/api/v1/chat/sessions/{sid}/messages"
        response = client.post(
            url, json={"client_message_id": str(uuid4()), "message": "정정"}
        )
        assert response.json()["data"]["collected_symptoms"]["main_symptom"] == "복통"
        assert (
            response.json()["data"]["collected_symptoms"]["body_location"] == "오른쪽"
        )
        response = client.post(
            url, json={"client_message_id": str(uuid4()), "message": "동반 증상 없음"}
        )
        assert response.json()["data"]["status"] == "COMPLETED"
        assert response.json()["data"]["turn_count"] == 3


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
        async def analyze_symptoms(self, symptoms):
            started.set()
            while not finish.is_set():
                await asyncio.sleep(0.01)
            return await super().analyze_symptoms(symptoms)

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


def test_analysis_result_is_stored_and_get_does_not_reanalyze(settings):
    from app.schemas.ai import AnalysisOutput
    from app.schemas.chat import Disease

    class Analyzer(MockAIProvider):
        analysis_calls = 0

        async def analyze_symptoms(self, symptoms):
            self.analysis_calls += 1
            return AnalysisOutput(
                assessment_status="PRELIMINARY",
                symptom_summary="외부 AI 테스트 요약",
                suspected_diseases=[
                    Disease(
                        disease_name="가상 테스트 질환", reason="계약 검증용 가상 내용"
                    )
                ],
                recommended_department="가상 테스트 진료과",
            )

    settings.MAX_CHAT_TURNS = 2
    provider = Analyzer()
    with TestClient(create_app(settings, provider)) as client:
        sid = create(client)
        response = client.post(
            f"/api/v1/chat/sessions/{sid}/messages",
            json={"client_message_id": str(uuid4()), "message": "추가 테스트"},
        )
        assert response.json()["data"]["status"] == "COMPLETED"
        path = f"/api/v1/chat/sessions/{sid}/result"
        data = client.get(path).json()["data"]
        assert data["assessment_status"] == "PRELIMINARY"
        assert data["suspected_diseases"][0]["disease_name"] == "가상 테스트 질환"
        assert data["recommended_department"] == "가상 테스트 진료과"
        assert data["references"] == []
        assert client.get(path).json()["data"] == data
        assert provider.analysis_calls == 1
