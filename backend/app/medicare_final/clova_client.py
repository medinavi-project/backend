"""CLOVA Studio v3 클라이언트. 네트워크 호출은 명시적으로 허용해야 한다."""

import os
import re
import time
import uuid
from pathlib import Path
import requests
from dotenv import load_dotenv

BASE_URL = "https://clovastudio.stream.ntruss.com"
LIMITS = {"HCX-005": {"context": 128000, "input": 128000, "output": 4096}}


class ClovaError(Exception):
    def __init__(self, category, code=None):
        super().__init__("CLOVA API 요청 실패: " + category)
        self.category, self.code = category, code


class ClovaClient:
    def __init__(
        self, *, api_key=None, model=None, session=None, timeout=45, retries=1
    ):
        self._key = api_key or os.getenv("CLOVA_API_KEY", "")
        self.model = model or os.getenv("CLOVA_MODEL") or "HCX-005"
        if not re.fullmatch(r"[A-Za-z0-9-]+", self.model):
            raise ValueError("모델명이 올바르지 않습니다.")
        if self.model not in LIMITS:
            raise ValueError("문서상 토큰 한도가 확인된 모델만 지원합니다: HCX-005")
        if timeout <= 0 or not 0 <= retries <= 2:
            raise ValueError("시간 제한 또는 재시도 설정 오류")
        self.timeout, self.retries = timeout, retries
        self.session = session or requests.Session()

    def _post(self, path, body, *, allow, retry_safe):
        if not allow:
            raise ClovaError("not_authorized")
        if not self._key:
            raise ClovaError("authentication")
        headers = {
            "Authorization": "Bearer " + self._key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
        }
        start = time.monotonic()
        for attempt in range((self.retries if retry_safe else 0) + 1):
            try:
                response = self.session.post(
                    BASE_URL + path, headers=headers, json=body, timeout=self.timeout
                )
            except requests.RequestException:
                if retry_safe and attempt < self.retries:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise ClovaError("network") from None
            if response.status_code >= 500 and retry_safe and attempt < self.retries:
                time.sleep(0.5 * (2**attempt))
                continue
            break
        latency = round(time.monotonic() - start, 3)
        if response.status_code in (401, 403):
            raise ClovaError("authentication", str(response.status_code))
        if response.status_code == 429:
            raise ClovaError("rate_limit", "429")
        try:
            data = response.json()
        except ValueError:
            raise ClovaError("api_error", str(response.status_code)) from None
        if not isinstance(data, dict):
            raise ClovaError("api_error")
        status = data.get("status") or {}
        code = str(status.get("code", "")) if isinstance(status, dict) else ""
        if response.status_code != 200 or code != "20000":
            category = (
                "token_limit"
                if code in ("40003", "64400")
                else (
                    "authentication"
                    if code.startswith(("401", "403"))
                    else ("rate_limit" if code.startswith("429") else "api_error")
                )
            )
            raise ClovaError(category, code or str(response.status_code))
        result = data.get("result")
        if not isinstance(result, dict):
            raise ClovaError("api_error", "invalid_result")
        return result, latency

    def tokenize(self, messages, *, allow=False):
        result, latency = self._post(
            "/v3/api-tools/chat-tokenize/" + self.model,
            {"messages": messages},
            allow=allow,
            retry_safe=True,
        )
        try:
            out = result["messages"]
            if len(out) != len(messages):
                raise ValueError()
            total = 0
            for requested, item in zip(messages, out):
                if item["role"] != requested["role"]:
                    raise ValueError()
                blocks = (
                    item["content"]
                    if isinstance(item["content"], list)
                    else [item["content"]]
                )
                if not blocks:
                    raise ValueError()
                for block in blocks:
                    count = block["count"]
                    if type(count) is not int or count < 0:
                        raise ValueError()
                    total += count
        except (KeyError, TypeError, ValueError):
            raise ClovaError("api_error", "invalid_token_response") from None
        # 공식 응답은 메시지 내용별 count를 제공하며 전체 프레이밍 토큰을 보장하지 않는다.
        return {
            "content_tokens": total,
            "latency": latency,
            "measurement": "calculator_content_counts",
        }

    def generate(self, messages, *, allow=False, max_tokens=2048, temperature=0.1):
        if (
            not 1 <= max_tokens <= LIMITS[self.model]["output"]
            or not 0 <= temperature <= 1
        ):
            raise ValueError("생성 설정이 모델 제한을 벗어났습니다.")
        result, latency = self._post(
            "/v3/chat-completions/" + self.model,
            {"messages": messages, "maxTokens": max_tokens, "temperature": temperature},
            allow=allow,
            retry_safe=False,
        )
        answer = (result.get("message") or {}).get("content")
        if not isinstance(answer, str) or not answer.strip():
            raise ClovaError("api_error", "empty_answer")
        usage = result.get("usage") or {}
        return {
            "answer": answer,
            "input_tokens": usage.get("promptTokens"),
            "output_tokens": usage.get("completionTokens"),
            "latency": latency,
            "finish_reason": result.get("finishReason"),
        }
