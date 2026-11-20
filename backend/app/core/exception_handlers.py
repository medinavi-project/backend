import logging

from fastapi.exceptions import RequestValidationError
from redis.exceptions import RedisError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.core.exceptions import BusinessException
from app.core.response import ApiResponse
from app.core.response_codes import Code

logger = logging.getLogger("medinavi")


def error_response(exc, data=None):
    return JSONResponse(
        status_code=exc.status,
        headers=exc.headers,
        content=ApiResponse(code=exc.code, message=exc.message, data=data).model_dump(
            mode="json"
        ),
    )


def register_handlers(app):
    @app.exception_handler(BusinessException)
    async def business(request, exc):
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def validation(request, exc):
        fields = [
            {"field": ".".join(map(str, e["loc"][1:])), "reason": e["msg"]}
            for e in exc.errors()
        ]
        return error_response(
            BusinessException(Code.VALIDATION_ERROR), {"field_errors": fields}
        )

    @app.exception_handler(HTTPException)
    async def http(request, exc):
        code = {
            400: Code.INVALID_REQUEST,
            404: Code.RESOURCE_NOT_FOUND,
            405: Code.METHOD_NOT_ALLOWED,
            413: Code.PAYLOAD_TOO_LARGE,
            415: Code.UNSUPPORTED_MEDIA_TYPE,
            422: Code.VALIDATION_ERROR,
            429: Code.RATE_LIMIT_EXCEEDED,
        }.get(exc.status_code, Code.INTERNAL_SERVER_ERROR)
        return error_response(BusinessException(code, headers=exc.headers))

    @app.exception_handler(RedisError)
    async def redis(request, exc):
        logger.warning("Redis failure type=%s", type(exc).__name__)
        return error_response(BusinessException(Code.SESSION_STORE_UNAVAILABLE))

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        logger.error("Unhandled failure type=%s", type(exc).__name__)
        return error_response(BusinessException(Code.INTERNAL_SERVER_ERROR))
