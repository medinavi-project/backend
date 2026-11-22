from typing import Any, Optional
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
# 라우팅 단계의 404/405는 Starlette의 HTTPException으로 발생한다.
# fastapi.HTTPException(하위 클래스)에 등록하면 이 둘을 잡지 못하므로 상위 클래스에 등록한다.
from starlette.exceptions import HTTPException


class HospitalApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        data: Optional[Any] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.data = data
        self.headers = headers


def _envelope(code: str, message: str, data: Optional[Any] = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "data": data,
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HospitalApiError)
    async def handle_hospital_api_error(_request: Request, exc: HospitalApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.data),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        field_errors = []
        for err in exc.errors():
            loc = err.get("loc", ())
            field_name = str(loc[-1]) if loc else "unknown"
            reason = str(err.get("msg", "입력값이 올바르지 않습니다."))
            field_errors.append({"field": field_name, "reason": reason})
        return JSONResponse(
            status_code=422,
            content=_envelope(
                "VALIDATION_ERROR",
                "요청 데이터가 올바르지 않습니다.",
                {"field_errors": field_errors},
            ),
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(_request: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code == 405:
            return JSONResponse(
                status_code=405,
                content=_envelope("METHOD_NOT_ALLOWED", "지원하지 않는 요청 방식입니다.", None),
            )
        if exc.status_code == 404:
            return JSONResponse(
                status_code=404,
                content=_envelope("NOT_FOUND", "요청한 리소스를 찾을 수 없습니다.", None),
            )
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope("HTTP_ERROR", str(exc.detail or "요청 처리에 실패했습니다."), None),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=_envelope("INTERNAL_SERVER_ERROR", "서버에서 오류가 발생했습니다.", None),
        )
