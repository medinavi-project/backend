import os

import pytest

from app.ai.factory import create_provider
from app.core.config import Settings
from app.schemas.ai import ChatInput
from app.schemas.chat import Message, Symptoms


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_GEMINI_INTEGRATION") != "1",
    reason="Explicit opt-in required; this calls the actual Gemini API",
)
async def test_live_gemini_connection():
    settings = Settings()
    if settings.AI_PROVIDER != "gemini" or not settings.GEMINI_API.get_secret_value():
        pytest.fail("Set AI_PROVIDER=gemini and GEMINI_API in backend/.env")
    provider = create_provider(settings)
    try:
        output = await provider.process_chat(
            ChatInput(
                messages=[
                    Message(
                        role="user",
                        content="통신 테스트입니다. 가벼운 두통이 있습니다.",
                    )
                ],
                symptoms=Symptoms(),
            )
        )
        assert output.next_question
    finally:
        await provider.close()
