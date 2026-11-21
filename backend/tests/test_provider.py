from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import httpx2
import pytest
from google.genai import errors

from app.ai.factory import create_provider
from app.ai.providers.gemini_provider import GeminiAIProvider
from app.core.config import Settings
from app.core.exceptions import BusinessException
from app.core.response_codes import Code
from app.schemas.ai import ChatInput
from app.schemas.chat import Message, Symptoms


@pytest.fixture
def gemini():
    provider = GeminiAIProvider(Settings(_env_file=None, GEMINI_API=""))
    provider.client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=AsyncMock()))
    )
    return provider


def request():
    return ChatInput(
        messages=[
            Message(role="user", content="첫 입력"),
            Message(role="assistant", content="질문"),
            Message(role="user", content="추가 입력"),
        ],
        symptoms=Symptoms(onset="어제"),
        answered_fields=["onset"],
        turn_count=2,
        max_turns=5,
    )


async def test_gemini_structured_context(gemini):
    import json

    raw = {
        "action": "ASK_FOLLOW_UP",
        "analysis": None,
        "symptom_updates": {},
        "answered_fields": [],
        "next_question": "추가 설명 부탁드립니다.",
    }
    gemini.client.aio.models.generate_content.return_value = SimpleNamespace(
        text=json.dumps(raw)
    )
    output = await gemini.process_chat(request())
    assert output.next_question
    kwargs = gemini.client.aio.models.generate_content.call_args.kwargs
    assert kwargs["model"] == "gemini-2.5-flash-lite"
    payload = json.loads(kwargs["contents"])
    assert len(payload["messages"]) == 3
    assert payload["symptoms"]["onset"] == "어제"
    assert kwargs["config"].response_mime_type == "application/json"


async def test_invalid_json_bounded_retry(gemini):
    gemini.client.aio.models.generate_content.return_value = SimpleNamespace(
        text="invalid"
    )
    with pytest.raises(BusinessException) as error:
        await gemini.process_chat(request())
    assert error.value.code == Code.AI_RESPONSE_INVALID
    assert gemini.client.aio.models.generate_content.await_count == 2


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (TimeoutError(), Code.AI_REQUEST_TIMEOUT),
        (httpx2.ReadTimeout("timeout"), Code.AI_REQUEST_TIMEOUT),
        (
            errors.APIError(403, {"error": {"message": "private"}}),
            Code.AI_SERVICE_UNAVAILABLE,
        ),
        (
            errors.APIError(404, {"error": {"message": "unknown model"}}),
            Code.AI_SERVICE_UNAVAILABLE,
        ),
        (
            errors.APIError(500, {"error": {"message": "private"}}),
            Code.AI_REQUEST_FAILED,
        ),
        (
            errors.APIError(
                429,
                {"error": {"message": "limit"}},
                httpx.Response(429, headers={"Retry-After": "15"}),
            ),
            Code.RATE_LIMIT_EXCEEDED,
        ),
    ],
)
async def test_error_mapping_no_retry(gemini, failure, expected):
    gemini.client.aio.models.generate_content.side_effect = failure
    with pytest.raises(BusinessException) as error:
        await gemini.process_chat(request())
    assert error.value.code == expected
    assert gemini.client.aio.models.generate_content.await_count == 1
    if expected == Code.RATE_LIMIT_EXCEEDED:
        assert error.value.headers == {"Retry-After": "15"}


async def test_missing_key_is_not_mock():
    provider = create_provider(Settings(_env_file=None, GEMINI_API=""))
    with pytest.raises(BusinessException) as error:
        await provider.process_chat(request())
    assert error.value.code == Code.AI_SERVICE_UNAVAILABLE


def test_client_uses_explicit_key_and_timeout(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.ai.providers.gemini_provider.genai.Client",
        lambda **kwargs: calls.append(kwargs),
    )
    GeminiAIProvider(
        Settings(_env_file=None, GEMINI_API="test-only", AI_REQUEST_TIMEOUT_SECONDS=7)
    )
    assert calls[0]["api_key"] == "test-only"
    assert calls[0]["http_options"].timeout == 7000
    assert calls[0]["http_options"].retry_options.attempts == 1


async def test_wire_schema_omits_unsupported_extra_properties(gemini):
    import json

    from app.ai.providers.gemini_provider import provider_schema
    from app.schemas.ai import ChatOutput

    schema = provider_schema(ChatOutput)
    assert "additionalProperties" not in json.dumps(schema)
    assert ChatOutput.model_config["extra"] == "forbid"
    gemini.client.aio.models.generate_content.return_value = SimpleNamespace(
        text=json.dumps(
            {
                "action": "ASK_FOLLOW_UP",
                "symptom_updates": {},
                "answered_fields": [],
                "next_question": "질문",
                "analysis": None,
            }
        )
    )
    await gemini.process_chat(request())
    config = gemini.client.aio.models.generate_content.call_args.kwargs["config"]
    assert config.automatic_function_calling.disable is True


async def test_complete_response_uses_same_call_and_schema(gemini):
    import json

    raw = {
        "action": "COMPLETE",
        "symptom_updates": {"severity": 2, "additional_symptoms": []},
        "answered_fields": ["severity", "additional_symptoms"],
        "next_question": None,
        "analysis": {
            "assessment_status": "PRELIMINARY",
            "symptom_summary": "최종 답변을 반영한 테스트 요약",
            "suspected_diseases": [
                {"disease_name": "테스트 질환", "reason": "테스트 근거"}
            ],
            "recommended_department": "테스트 진료과",
        },
    }
    gemini.client.aio.models.generate_content.return_value = SimpleNamespace(
        text=json.dumps(raw)
    )
    output = await gemini.process_chat(request())
    assert output.action == "COMPLETE"
    assert output.analysis.recommended_department == "테스트 진료과"
    assert gemini.client.aio.models.generate_content.await_count == 1
    payload = json.loads(
        gemini.client.aio.models.generate_content.call_args.kwargs["contents"]
    )
    assert payload["answered_fields"] == ["onset"]
    assert payload["turn_count"] == 2 and payload["max_turns"] == 5


async def test_wrong_last_turn_action_gets_bounded_retry(gemini):
    import json

    gemini.client.aio.models.generate_content.return_value = SimpleNamespace(
        text=json.dumps(
            {
                "action": "ASK_FOLLOW_UP",
                "symptom_updates": {},
                "answered_fields": [],
                "next_question": "계속 질문",
                "analysis": None,
            }
        )
    )
    last = request().model_copy(update={"turn_count": 5})
    with pytest.raises(BusinessException) as error:
        await gemini.process_chat(last)
    assert error.value.code == Code.AI_RESPONSE_INVALID
    assert gemini.client.aio.models.generate_content.await_count == 2
