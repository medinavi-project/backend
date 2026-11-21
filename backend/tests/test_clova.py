from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import Settings
from app.core.exceptions import BusinessException
from app.ocr.clova import ClovaOCRClient


@pytest.mark.parametrize("text", ["약품 이름", ""])
async def test_clova_wire_and_text(monkeypatch, text):
    fields = (
        [{"inferText": text, "inferConfidence": 0.99, "lineBreak": True}]
        if text
        else []
    )
    response = httpx.Response(
        200,
        json={"images": [{"inferResult": "SUCCESS", "fields": fields}]},
        request=httpx.Request("POST", "https://example.test"),
    )
    post = AsyncMock(return_value=response)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    client = ClovaOCRClient(
        Settings(
            _env_file=None,
            CLOVA_OCR_INVOKE_URL="https://example.test",
            CLOVA_OCR_SECRET="test-secret",
        )
    )
    if text:
        assert await client.recognize(b"fixture", "image/png") == text
    else:
        with pytest.raises(BusinessException) as error:
            await client.recognize(b"fixture", "image/png")
        assert error.value.code == "OCR_TEXT_NOT_FOUND"
    kwargs = post.call_args.kwargs
    assert kwargs["headers"]["X-OCR-SECRET"] == "test-secret"
    assert kwargs["json"]["version"] == "V2"
    assert kwargs["json"]["images"][0]["data"] == "Zml4dHVyZQ=="
