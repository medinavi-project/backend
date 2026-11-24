"""CLOVA Voice (NCP Premium TTS) for medication guidance, generated on play.

Scan registers the text to read under a random id (Redis, 1h) and returns
/api/v1/medications/audio/{id}; the mp3 is synthesized only when that URL is
fetched. The id indirection keeps the endpoint from being a free TTS proxy.
"""

import hashlib
import logging
import secrets

import httpx

from app.core.exceptions import BusinessException
from app.core.response_codes import Code

logger = logging.getLogger("medinavi")

TTS_URL = "https://naveropenapi.apigw.ntruss.com/tts-premium/v1/tts"
AUDIO_TTL_SECONDS = 3600
MAX_CHARS = 1900  # API limit is 2000
_MP3_CACHE: dict[str, bytes] = {}
_MP3_CACHE_MAX = 200  # ponytail: in-process, cleared when full; Redis if multi-server


def speech_text(name, purpose):
    """Name and purpose only: approved dosage may differ from the prescription."""
    text = f"{name}. {purpose}" if purpose else name
    return " ".join(text.split())[:MAX_CHARS]


class VoiceService:
    def __init__(self, settings, redis):
        self.client_id = settings.CLOVA_VOICE_CLIENT_ID.get_secret_value()
        self.client_secret = settings.CLOVA_VOICE_CLIENT_SECRET.get_secret_value()
        self.speaker = settings.CLOVA_VOICE_SPEAKER
        self.redis = redis

    @property
    def configured(self):
        return bool(self.client_id and self.client_secret)

    async def register(self, text):
        audio_id = secrets.token_urlsafe(16)
        await self.redis.set(f"medinavi:tts:{audio_id}", text, ex=AUDIO_TTL_SECONDS)
        return f"/api/v1/medications/audio/{audio_id}"

    async def audio(self, audio_id):
        if not self.configured:
            raise BusinessException(Code.TTS_SERVICE_UNAVAILABLE)
        text = await self.redis.get(f"medinavi:tts:{audio_id}")
        if not text:
            raise BusinessException(Code.RESOURCE_NOT_FOUND)
        key = hashlib.sha256(f"{self.speaker}|{text}".encode()).hexdigest()
        if key not in _MP3_CACHE:
            if len(_MP3_CACHE) >= _MP3_CACHE_MAX:
                _MP3_CACHE.clear()
            _MP3_CACHE[key] = await self._synthesize(text)
        return _MP3_CACHE[key]

    async def _synthesize(self, text):
        headers = {
            "X-NCP-APIGW-API-KEY-ID": self.client_id,
            "X-NCP-APIGW-API-KEY": self.client_secret,
        }
        data = {"speaker": self.speaker, "text": text, "format": "mp3", "speed": "0"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                res = await client.post(TTS_URL, headers=headers, data=data)
        except httpx.TimeoutException:
            raise BusinessException(Code.TTS_REQUEST_TIMEOUT) from None
        except httpx.HTTPError as exc:
            logger.warning("CLOVA Voice request failed: %r", exc)
            raise BusinessException(Code.TTS_GENERATION_FAILED) from None
        if res.status_code != 200:
            logger.warning("CLOVA Voice HTTP %s body=%.200s", res.status_code, res.text)
            if res.status_code == 429:
                raise BusinessException(Code.RATE_LIMIT_EXCEEDED)
            raise BusinessException(Code.TTS_GENERATION_FAILED)
        return res.content
