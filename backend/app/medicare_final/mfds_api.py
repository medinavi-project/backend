"""식약처 공공데이터 API. 예외나 로그에 요청 URL과 인증키를 포함하지 않는다."""

import json
import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import requests
from dotenv import load_dotenv

BASE = "https://apis.data.go.kr/1471000/"
EASY = "DrbEasyDrugInfoService/getDrbEasyDrugList"
PERMIT = "DrugPrdtPrmsnInfoService08/"
DUR = "DURPrdlstInfoService03/"
DUR_OPS = {
    "interaction": "getUsjntTabooInfoList03",
    "elderly": "getOdsnAtentInfoList03",
    "age": "getSpcifyAgrdeTabooInfoList03",
    "pregnancy": "getPwnmTabooInfoList03",
    "dosage": "getCpctyAtentInfoList03",
    "duration": "getMdctnPdAtentInfoList03",
    "duplicate": "getEfcyDplctInfoList03",
    "split": "getSeobangjeongPartitnAtentInfoList03",
    "all": "getDurPrdlstInfoList03",
}
AUTH_CODES = {"10", "11", "12", "20", "21", "22", "30", "31", "32", "33"}


class MFDSError(Exception):
    def __init__(self, category, message, code=None):
        super().__init__(message)
        self.category, self.code = category, code


@dataclass
class Page:
    items: list
    total_count: int
    page: int
    page_size: int
    code: str

    @property
    def status(self):
        return "ok" if self.items else "no_results"


class MFDSClient:
    def __init__(
        self,
        key=None,
        *,
        max_calls=100,
        timeout=20,
        retries=1,
        interval=0.15,
        session=None,
    ):
        self.key = unquote(key or os.getenv("MFDS_API_KEY", ""))
        if not self.key:
            raise MFDSError("authentication", "MFDS_API_KEY가 없습니다.")
        if max_calls < 1 or retries < 0 or retries > 2 or timeout <= 0 or interval < 0:
            raise ValueError("호출 예산, 재시도, 시간 설정이 올바르지 않습니다.")
        self.max_calls, self.timeout, self.retries, self.interval = (
            max_calls,
            timeout,
            retries,
            interval,
        )
        self.calls = 0
        self.session = session or requests.Session()
        self.last_call = 0.0

    def _request(self, path, params, page, page_size):
        if not 1 <= page or not 1 <= page_size <= 100:
            raise ValueError("page는 1 이상, page_size는 1~100이어야 합니다.")
        query = {
            "serviceKey": self.key,
            "pageNo": page,
            "numOfRows": page_size,
            "type": "json",
            **params,
        }
        for attempt in range(self.retries + 1):
            if self.calls >= self.max_calls:
                raise MFDSError(
                    "budget_exceeded", "이 클라이언트의 호출 예산을 소진했습니다."
                )
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_call)))
            self.calls += 1
            self.last_call = time.monotonic()
            try:
                response = self.session.get(
                    BASE + path, params=query, timeout=self.timeout
                )
            except requests.RequestException:
                if attempt < self.retries:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise MFDSError(
                    "network", "식약처 API 네트워크 요청에 실패했습니다."
                ) from None
            if response.status_code in (401, 403):
                raise MFDSError(
                    "authentication",
                    "API 인증 또는 이용 권한을 확인하세요.",
                    str(response.status_code),
                )
            if response.status_code == 429:
                raise MFDSError("quota", "API 호출 제한 응답을 받았습니다.", "429")
            if response.status_code >= 500 and attempt < self.retries:
                time.sleep(0.5 * (2**attempt))
                continue
            if response.status_code != 200:
                raise MFDSError(
                    "api", "API HTTP 오류가 발생했습니다.", str(response.status_code)
                )
            break
        raw = response.content
        data = None
        # requests의 기본 ISO-8859-1 추측을 사용하지 않는다.
        for encoding in ("utf-8-sig", "cp949"):
            try:
                data = json.loads(raw.decode(encoding))
                break
            except (UnicodeError, ValueError):
                pass
        if data is None:
            try:
                root = ET.fromstring(raw)
                code = root.findtext(".//returnReasonCode") or root.findtext(
                    ".//resultCode"
                )
                if code:
                    category = (
                        "quota"
                        if code == "22"
                        else ("authentication" if code in AUTH_CODES else "api")
                    )
                    raise MFDSError(category, "공공데이터 API 오류 응답입니다.", code)
            except ET.ParseError:
                pass
            raise MFDSError("api", "API 응답을 JSON으로 해석할 수 없습니다.")
        if not isinstance(data, dict):
            raise MFDSError("api", "API 응답 형식이 올바르지 않습니다.")
        data = data.get("response", data)
        if not isinstance(data, dict):
            raise MFDSError("api", "API 응답 형식이 올바르지 않습니다.")
        header = data.get("header") or {}
        code = str(header.get("resultCode", ""))
        if code not in ("00", "0"):
            category = (
                "quota"
                if code == "22"
                else ("authentication" if code in AUTH_CODES else "api")
            )
            raise MFDSError(category, "공공데이터 API 오류 응답입니다.", code or None)
        body = data.get("body") or {}
        if not isinstance(body, dict):
            raise MFDSError("api", "API 본문 형식이 올바르지 않습니다.")
        items = body.get("items") or []
        if isinstance(items, dict):
            items = items.get("item", items)
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list) or any(not isinstance(x, dict) for x in items):
            raise MFDSError("api", "API 품목 목록 형식이 올바르지 않습니다.")
        try:
            total = int(body.get("totalCount", len(items)))
        except (TypeError, ValueError):
            raise MFDSError("api", "API 전체 건수가 올바르지 않습니다.") from None
        if total > 0 and not items and page == 1:
            raise MFDSError("api", "API 전체 건수와 품목 목록이 일치하지 않습니다.")
        return Page(items, total, page, page_size, code)

    @staticmethod
    def _verify_seq(result, item_seq, field):
        if item_seq and any(
            str(item.get(field, "")).strip() != str(item_seq).strip()
            for item in result.items
        ):
            raise MFDSError(
                "api", "품목코드 조회 결과가 요청 품목과 일치하지 않습니다."
            )
        return result

    def easy(self, *, name=None, item_seq=None, page=1, page_size=30):
        if not (name or item_seq):
            raise ValueError("제품명 또는 품목기준코드가 필요합니다.")
        return self._verify_seq(
            self._request(
                EASY,
                {k: v for k, v in {"itemName": name, "itemSeq": item_seq}.items() if v},
                page,
                page_size,
            ),
            item_seq,
            "itemSeq",
        )

    def permit_list(self, *, name=None, item_seq=None, page=1, page_size=30):
        if not (name or item_seq):
            raise ValueError("제품명 또는 품목기준코드가 필요합니다.")
        return self._verify_seq(
            self._request(
                PERMIT + "getDrugPrdtPrmsnInq08",
                {
                    k: v
                    for k, v in {
                        "item_name": name,
                        "prdlst_Stdr_code": item_seq,
                    }.items()
                    if v
                },
                page,
                page_size,
            ),
            item_seq,
            "ITEM_SEQ",
        )

    def permit_detail(self, item_seq, *, page=1, page_size=30):
        if not item_seq:
            raise ValueError("품목기준코드가 필요합니다.")
        return self._verify_seq(
            self._request(
                PERMIT + "getDrugPrdtPrmsnDtlInq08",
                {"item_seq": str(item_seq)},
                page,
                page_size,
            ),
            item_seq,
            "ITEM_SEQ",
        )

    def ingredients(self, item_seq, *, page=1, page_size=30):
        if not item_seq:
            raise ValueError("품목기준코드가 필요합니다.")
        return self._verify_seq(
            self._request(
                PERMIT + "getDrugPrdtMcpnDtlInq08",
                {"Item_seq": str(item_seq)},
                page,
                page_size,
            ),
            item_seq,
            "ITEM_SEQ",
        )

    def dur(self, item_seq, *, kind="interaction", page=1, page_size=30):
        if not item_seq or kind not in DUR_OPS:
            raise ValueError("품목기준코드와 지원 DUR 유형이 필요합니다.")
        return self._verify_seq(
            self._request(
                DUR + DUR_OPS[kind], {"itemSeq": str(item_seq)}, page, page_size
            ),
            item_seq,
            "ITEM_SEQ",
        )

    def pages(self, method, *, max_pages=3, **kwargs):
        if max_pages < 1:
            raise ValueError("max_pages는 1 이상이어야 합니다.")
        for number in range(1, max_pages + 1):
            result = method(page=number, **kwargs)
            yield result
            if not result.items or number * result.page_size >= result.total_count:
                break
