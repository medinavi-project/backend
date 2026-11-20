from app.core.response_codes import HTTP_STATUS, MESSAGES, Code


class BusinessException(Exception):
    def __init__(
        self, code: Code, message: str | None = None, headers: dict | None = None
    ):
        self.code = code
        self.status = HTTP_STATUS[code]
        self.message = message or MESSAGES.get(code, "요청을 처리할 수 없습니다.")
        self.headers = headers
