import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.ai.factory import create_provider
from app.core.config import Settings
from app.core.exception_handlers import register_handlers
from app.core.response import ApiResponse
from app.core.response_codes import HTTP_STATUS, MESSAGES
from app.repositories.redis_session_repository import SessionRepository
from app.routers import chat, hospitals, medications
from app.services.analysis_service import AnalysisService
from app.services.chat_service import ChatService
from app.services.ocr_service import OCRService


def create_app(settings=None, provider=None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        logging.basicConfig(
            level=logging.INFO, format="%(levelname)s %(name)s %(message)s"
        )
        # SDK/HTTP request logs can contain sensitive URLs or content.
        for name in ("httpx", "httpx2", "google.genai", "google_genai"):
            logging.getLogger(name).setLevel(logging.CRITICAL)
        redis = Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        ai = provider or create_provider(settings)
        app.state.redis = redis
        app.state.chat = ChatService(
            SessionRepository(
                redis,
                settings.SESSION_TTL_SECONDS,
                int(settings.AI_REQUEST_TIMEOUT_SECONDS * 4 + 30),
            ),
            ai,
            AnalysisService(ai),
            settings,
        )
        app.state.ocr = OCRService(settings, ai)
        try:
            yield
        finally:
            await redis.aclose()
            await ai.close()

    errors = {}
    for code, status in HTTP_STATUS.items():
        if status >= 400:
            errors.setdefault(
                status,
                {
                    "model": ApiResponse[dict],
                    "description": "공통 오류",
                    "content": {"application/json": {"examples": {}}},
                },
            )
            errors[status]["content"]["application/json"]["examples"][code] = {
                "value": {
                    "code": code,
                    "message": MESSAGES.get(code, "요청을 처리할 수 없습니다."),
                    "data": None,
                }
            }
    app = FastAPI(
        title=settings.APP_NAME, version="0.1.0", lifespan=lifespan, responses=errors
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip()
            for origin in settings.CORS_ORIGINS.split(",")
            if origin.strip()
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    register_handlers(app)
    for router in (chat.router, hospitals.router, medications.router):
        app.include_router(router)

    @app.get("/health", response_model=ApiResponse[dict])
    async def health():
        await app.state.redis.ping()
        return ApiResponse(
            code="SUCCESS",
            message="서버와 Redis가 정상입니다.",
            data={
                "api": "UP",
                "redis": "UP",
                "ai_provider": settings.AI_PROVIDER,
                "ai_configured": settings.AI_PROVIDER == "mock"
                or bool(
                    (
                        settings.CLOVASTUDIO_API_KEY
                        if settings.AI_PROVIDER == "clova_triage"
                        else settings.GEMINI_API
                    ).get_secret_value()
                ),
                "ocr": "DISABLED",
                "tts": "DISABLED",
            },
        )

    return app


app = create_app()
