import logging
import os
import time
from typing import Any, Optional
import xml.etree.ElementTree as ET
import httpx

from app.hospital.errors import HospitalApiError

logger = logging.getLogger("medinavi")

NMC_LIST_URL = "https://apis.data.go.kr/B552657/HsptlAsembySearchService/getHsptlMdcncListInfoInqire"
# 요일 조건(QT) 없이 받으므로 진료시간 원본만 캐시한다. 진료 중 여부는 매 요청 계산.
CACHE_TTL_SECONDS = 3600.0
# 한 지역·진료과 결과가 100건을 넘는 경우가 실제로 있다 (서울 중구 내과 127건, 강남구 내과 498건).
# 목록 API는 거리순이 아니므로 첫 페이지만 받으면 가까운 병원이 빠질 수 있어 전체 페이지를 받는다.
MAX_PAGES = 10

# 캐시에는 공공데이터 원본 행(dict)만 저장하고 (만료시각, 행목록) 형태로 보관
_RAW_ROW_CACHE: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}


def clear_raw_row_cache() -> None:
    _RAW_ROW_CACHE.clear()


def parse_nmc_list_xml(xml_text: str) -> list[dict[str, Any]]:
    rows, _total = parse_nmc_list_page(xml_text)
    return rows


def parse_nmc_list_page(xml_text: str) -> tuple[list[dict[str, Any]], Optional[int]]:
    """행 목록과 totalCount(없으면 None)를 함께 돌려준다."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise HospitalApiError(
            status_code=502,
            code="HOSPITAL_SEARCH_FAILED",
            message="병원 정보를 검색하지 못했습니다.",
        ) from exc

    # 정상 응답은 resultCode, 게이트웨이 오류는 returnReasonCode (22 = 일일 호출 한도 초과)
    result_code = (root.findtext(".//resultCode") or root.findtext(".//returnReasonCode") or "").strip()
    if result_code and result_code not in ("00", "0"):
        logger.warning("NMC list API error resultCode=%s", result_code)
        if result_code == "22":
            raise _rate_limited()
        raise HospitalApiError(
            status_code=502,
            code="HOSPITAL_SEARCH_FAILED",
            message="병원 정보를 검색하지 못했습니다.",
        )

    rows: list[dict[str, Any]] = []
    for item_el in root.findall(".//item"):
        row: dict[str, Any] = {}
        for child in item_el:
            row[child.tag] = child.text.strip() if child.text is not None else None
        rows.append(row)

    total: Optional[int] = None
    total_text = (root.findtext(".//totalCount") or "").strip()
    if total_text.isdigit():
        total = int(total_text)
    return rows, total


def fetch_raw_hospitals(
    qd: str,
    q0: str,
    q1: str,
    num_of_rows: int = 500,  # 2026-11-21 실측: 500건 한 번에 응답(강남구 내과 490건, 1.3초)
) -> list[dict[str, Any]]:
    cache_key = (qd, f"{q0}|{q1}")
    now_ts = time.monotonic()
    cached = _RAW_ROW_CACHE.get(cache_key)
    if cached is not None:
        expires_at, cached_rows = cached
        if now_ts < expires_at:
            return [dict(r) for r in cached_rows]
        _RAW_ROW_CACHE.pop(cache_key, None)

    service_key = (os.getenv("DATA_GO_KR_KEY") or os.getenv("NMC_API_KEY") or "").strip()
    if not service_key:
        raise HospitalApiError(
            status_code=503,
            code="HOSPITAL_DATA_UNAVAILABLE",
            message="병원 정보를 일시적으로 이용할 수 없습니다.",
        )

    collected: list[dict[str, Any]] = []
    page_no = 1
    while True:
        params: dict[str, Any] = {
            "ServiceKey": service_key,
            "QD": qd,
            "pageNo": str(page_no),
            "numOfRows": str(num_of_rows),
        }
        if q0:
            params["Q0"] = q0
        if q1:
            params["Q1"] = q1

        rows, total = parse_nmc_list_page(_get_page(params))
        collected.extend(rows)

        # totalCount가 없거나, 다 받았거나, 빈 페이지거나, 상한에 닿으면 멈춘다
        if total is None or not rows or len(collected) >= total or page_no >= MAX_PAGES:
            break
        page_no += 1

    _RAW_ROW_CACHE[cache_key] = (now_ts + CACHE_TTL_SECONDS, [dict(r) for r in collected])
    return [dict(r) for r in collected]


def _get_page(params: dict[str, Any]) -> str:
    last_exc: Optional[Exception] = None
    response: Optional[httpx.Response] = None
    for _ in range(2):
        try:
            response = httpx.get(NMC_LIST_URL, params=params, timeout=5.0)
            last_exc = None
            break
        except httpx.TimeoutException as exc:
            last_exc = exc
        except httpx.HTTPError as exc:
            last_exc = exc

    if last_exc is not None or response is None:
        logger.warning("NMC list API request failed: %r", last_exc)
        if isinstance(last_exc, httpx.TimeoutException):
            raise HospitalApiError(
                status_code=504,
                code="EXTERNAL_SERVICE_TIMEOUT",
                message="외부 서비스 응답이 지연되고 있습니다.",
            )
        raise HospitalApiError(
            status_code=502,
            code="HOSPITAL_SEARCH_FAILED",
            message="병원 정보를 검색하지 못했습니다.",
        )

    if response.status_code != 200:
        logger.warning("NMC list API HTTP %s body=%.200s", response.status_code, response.text)
        if response.status_code == 429:
            raise _rate_limited()
        raise HospitalApiError(
            status_code=502,
            code="HOSPITAL_SEARCH_FAILED",
            message="병원 정보를 검색하지 못했습니다.",
        )
    return response.text


def _rate_limited() -> HospitalApiError:
    return HospitalApiError(
        status_code=429,
        code="RATE_LIMIT_EXCEEDED",
        message="외부 서비스 호출 한도를 초과했습니다.",
    )
