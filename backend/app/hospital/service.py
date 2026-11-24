import math
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Optional

from app.hospital.client import fetch_raw_hospitals
from app.hospital.dept_codes import resolve_department
from app.hospital.errors import HospitalApiError
from app.hospital.geo import haversine_km, reverse_geocode_region
from app.hospital.models import Hospital, HospitalListResponse
from app.hospital.open_hours import build_open_hours, ensure_seoul_now, is_open_now


# 진료과 하나당 외부 API를 따로 부르므로 상한을 둔다 (호출 한도 보호)
MAX_DEPARTMENTS = 5


def get_radius_max_km() -> float:
    raw = (os.getenv("HOSPITAL_RADIUS_MAX_KM") or "10").strip()
    try:
        val = float(raw)
        return val if val > 0 else 10.0
    except ValueError:
        return 10.0


def get_max_results() -> int:
    raw = (os.getenv("HOSPITAL_MAX_RESULTS") or "20").strip()
    try:
        val = int(raw)
        return val if val > 0 else 20
    except ValueError:
        return 20


def validate_search_inputs(
    department: str,
    latitude: float,
    longitude: float,
    radius_km: float,
) -> list[tuple[str, str]]:
    if not (-90.0 <= latitude <= 90.0) or not (-180.0 <= longitude <= 180.0):
        raise HospitalApiError(
            status_code=400,
            code="HOSPITAL_LOCATION_INVALID",
            message="위치 좌표가 유효하지 않습니다.",
        )

    max_radius = get_radius_max_km()
    if radius_km <= 0.0 or radius_km > max_radius:
        raise HospitalApiError(
            status_code=400,
            code="INVALID_PARAMETER",
            message="검색 반경이 허용 범위를 벗어났습니다.",
        )

    # "내과,외과"처럼 쉼표로 여러 진료과를 받는다. 별칭은 표준 이름으로 합치고 중복은 뺀다.
    names = [d for d in (department or "").split(",") if d.strip()]
    resolved: list[tuple[str, str]] = []
    for name in names:
        dept = resolve_department(name)
        if dept is None:
            raise _dept_error(f"지원하지 않는 진료과입니다: {name.strip()}")
        if dept not in resolved:
            resolved.append(dept)
    if not resolved:
        raise _dept_error("진료과를 하나 이상 입력해야 합니다.")
    if len(resolved) > MAX_DEPARTMENTS:
        raise _dept_error(f"진료과는 한 번에 {MAX_DEPARTMENTS}개까지 검색할 수 있습니다.")
    return resolved


def _dept_error(reason: str) -> HospitalApiError:
    return HospitalApiError(
        status_code=422,
        code="VALIDATION_ERROR",
        message="요청 데이터가 올바르지 않습니다.",
        data={"field_errors": [{"field": "department", "reason": reason}]},
    )


def _parse_float_or_none(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        num = float(str(val).strip())
        if math.isnan(num) or math.isinf(num):
            return None
        return num
    except (ValueError, TypeError):
        return None


def _clean_str_or_none(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _offset_point(lat: float, lon: float, distance_km: float, bearing_deg: float) -> tuple[float, float]:
    """(lat, lon)에서 bearing 방향으로 distance_km 떨어진 점 (짧은 거리용 근사)."""
    rad = math.radians(bearing_deg)
    dlat = (distance_km * math.cos(rad)) / 111.0
    cos_lat = max(0.01, math.cos(math.radians(lat)))
    dlon = (distance_km * math.sin(rad)) / (111.0 * cos_lat)
    return lat + dlat, lon + dlon


def collect_search_regions(latitude: float, longitude: float, radius_km: float) -> list[tuple[str, str]]:
    """검색 반경이 걸치는 시·도/시·군·구 목록.

    목록 API는 시·군·구(Q1) 단위로만 조회되므로, 중심 하나만 쓰면 반경이 옆 구로 넘어갈 때
    그쪽 병원이 빠진다. 중심과 반경 위 8방향 점을 역지오코딩해 걸치는 지역을 모은다.
    중심 지역 실패는 오류로 올리고, 바다·국경 등 주변 점 실패는 건너뛴다.
    """
    regions: list[tuple[str, str]] = [reverse_geocode_region(latitude, longitude)]

    def _lookup(bearing: int) -> Optional[tuple[str, str]]:
        p_lat, p_lon = _offset_point(latitude, longitude, radius_km, bearing)
        try:
            return reverse_geocode_region(p_lat, p_lon)
        except HospitalApiError:
            return None

    # 주변 8개 점은 동시에 조회해 지연을 줄인다
    with ThreadPoolExecutor(max_workers=8) as pool:
        for region in pool.map(_lookup, range(0, 360, 45)):
            if region is not None and region not in regions:
                regions.append(region)
    return regions


def search_open_hospitals(
    department: str,
    latitude: float,
    longitude: float,
    radius_km: float,
    open_now: bool,
    now: Optional[datetime] = None,
) -> HospitalListResponse:
    depts = validate_search_inputs(department, latitude, longitude, radius_km)
    seoul_now = ensure_seoul_now(now)
    regions = collect_search_regions(latitude, longitude, radius_km)
    jobs = [(name, qd, q0, q1) for name, qd in depts for q0, q1 in regions]
    # 진료과×지역 조회는 서로 독립이라 동시에 부른다 (각각 1시간 캐시)
    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        fetched = pool.map(lambda j: fetch_raw_hospitals(qd=j[1], q0=j[2], q1=j[3]), jobs)
        per_dept: dict[str, dict[str, dict[str, Any]]] = {name: {} for name, _ in depts}
        for (name, *_), rows in zip(jobs, fetched):
            for row in rows:
                hpid = _clean_str_or_none(row.get("hpid"))
                if hpid:
                    per_dept[name].setdefault(hpid, row)

    max_results = get_max_results()
    merged: dict[str, Hospital] = {}
    # 진료과마다 최대 max_results곳을 뽑아 합친다 (한 과가 결과를 독차지하지 않게)
    for name, rows in per_dept.items():
        for h in _rank(rows.values(), name, latitude, longitude, radius_km, open_now, seoul_now)[:max_results]:
            if h.hospital_id in merged:
                merged[h.hospital_id].departments.append(name)
            else:
                merged[h.hospital_id] = h

    hospitals = sorted(merged.values(), key=_sort_key)
    return HospitalListResponse(
        as_of=seoul_now.isoformat(timespec="seconds"),
        source_updated_at=None,
        total_count=len(hospitals),
        hospitals=hospitals,
    )


def _sort_key(h: Hospital) -> tuple:
    # 진료 중인 병원 먼저, 그다음 가까운 순 (open_now=false에서 진료 중 병원이 잘리지 않게)
    return (not h.is_open, h.distance_km, h.name)


def _rank(rows, standard_dept, latitude, longitude, radius_km, open_now, seoul_now) -> list[Hospital]:
    candidates: list[Hospital] = []
    for row in rows:
        h_lat = _parse_float_or_none(row.get("wgs84Lat"))
        h_lon = _parse_float_or_none(row.get("wgs84Lon"))
        if h_lat is None or h_lon is None:
            continue

        dist = haversine_km(latitude, longitude, h_lat, h_lon)
        if dist > radius_km:
            continue

        open_flag = is_open_now(row, now=seoul_now)
        if open_now and not open_flag:
            continue

        hospital_id = _clean_str_or_none(row.get("hpid")) or ""
        name = _clean_str_or_none(row.get("dutyName")) or ""
        address = _clean_str_or_none(row.get("dutyAddr")) or ""
        if not hospital_id or not name or not address:
            continue

        candidates.append(
            Hospital(
                hospital_id=hospital_id,
                name=name,
                address=address,
                latitude=h_lat,
                longitude=h_lon,
                department=standard_dept,
                departments=[standard_dept],
                phone=_clean_str_or_none(row.get("dutyTel1")),
                open_hours=build_open_hours(row),
                is_open=open_flag,
                distance_km=round(dist, 2),
            )
        )
    return sorted(candidates, key=_sort_key)
