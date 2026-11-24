import logging
import math
import os
from typing import Optional
import httpx

from app.hospital.errors import HospitalApiError

logger = logging.getLogger("medinavi")

EARTH_RADIUS_KM = 6371.0
NCP_REVERSE_GEOCODE_URL = "https://maps.apigw.ntruss.com/map-reversegeocode/v2/gc"
# 좌표 → 행정구역은 거의 바뀌지 않으므로 소수점 4자리(약 10m) 단위로 캐시한다
_REGION_CACHE: dict[tuple[float, float], tuple[str, str]] = {}
_REGION_CACHE_MAX = 5000


def clear_region_cache() -> None:
    _REGION_CACHE.clear()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1_r = math.radians(lat1)
    lon1_r = math.radians(lon1)
    lat2_r = math.radians(lat2)
    lon2_r = math.radians(lon2)

    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r

    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * (math.sin(dlon / 2.0) ** 2)
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return EARTH_RADIUS_KM * c


def reverse_geocode_region(latitude: float, longitude: float) -> tuple[str, str]:
    key = (round(latitude, 4), round(longitude, 4))
    cached = _REGION_CACHE.get(key)
    if cached is not None:
        return cached
    region = _reverse_geocode_region_uncached(latitude, longitude)
    if len(_REGION_CACHE) >= _REGION_CACHE_MAX:
        _REGION_CACHE.clear()
    _REGION_CACHE[key] = region
    return region


def _reverse_geocode_region_uncached(latitude: float, longitude: float) -> tuple[str, str]:
    client_id = (os.getenv("NCP_MAPS_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("NCP_MAPS_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        raise HospitalApiError(
            status_code=503,
            code="HOSPITAL_DATA_UNAVAILABLE",
            message="병원 정보를 일시적으로 이용할 수 없습니다.",
        )

    params = {
        "coords": f"{longitude},{latitude}",
        "orders": "admcode,addr",
        "output": "json",
    }
    headers = {
        "X-NCP-APIGW-API-KEY-ID": client_id,
        "X-NCP-APIGW-API-KEY": client_secret,
    }

    last_exc: Optional[Exception] = None
    response: Optional[httpx.Response] = None
    for _ in range(2):
        try:
            response = httpx.get(
                NCP_REVERSE_GEOCODE_URL,
                params=params,
                headers=headers,
                timeout=5.0,
            )
            last_exc = None
            break
        except httpx.TimeoutException as exc:
            last_exc = exc
        except httpx.HTTPError as exc:
            last_exc = exc

    if last_exc is not None or response is None:
        logger.warning("NCP reverse geocode request failed: %r", last_exc)
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
        logger.warning("NCP reverse geocode HTTP %s body=%.200s", response.status_code, response.text)
        raise HospitalApiError(
            status_code=502,
            code="HOSPITAL_SEARCH_FAILED",
            message="병원 정보를 검색하지 못했습니다.",
        )

    try:
        payload = response.json()
        results = payload.get("results") or []
        for item in results:
            region = item.get("region") or {}
            area1 = (region.get("area1") or {}).get("name") or ""
            area2 = (region.get("area2") or {}).get("name") or ""
            if area1.strip():
                return area1.strip(), area2.strip()
    except Exception as exc:
        raise HospitalApiError(
            status_code=502,
            code="HOSPITAL_SEARCH_FAILED",
            message="병원 정보를 검색하지 못했습니다.",
        ) from exc

    raise HospitalApiError(
        status_code=502,
        code="HOSPITAL_SEARCH_FAILED",
        message="병원 정보를 검색하지 못했습니다.",
    )
