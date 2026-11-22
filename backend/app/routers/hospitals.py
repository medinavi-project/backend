from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from starlette.concurrency import run_in_threadpool

from app.core.response import ApiResponse
from app.hospital.models import HospitalListResponse
from app.hospital.service import search_open_hospitals

router = APIRouter(prefix="/api/v1/hospitals", tags=["병원"])


# Contract follows the hosAI hospital module (NMC public API).
@router.get("/open", response_model=ApiResponse[HospitalListResponse])
async def hospitals(
    department: Annotated[str, Query()],
    latitude: Annotated[float, Query()],
    longitude: Annotated[float, Query()],
    radius_km: Annotated[float, Query()],
    open_now: Annotated[bool, Query()],
    now: Annotated[datetime | None, Query(include_in_schema=False)] = None,
):
    data = await run_in_threadpool(
        search_open_hospitals,
        department=department,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        open_now=open_now,
        now=now,
    )
    return ApiResponse(
        code="SUCCESS",
        message="병원 검색이 완료되었습니다."
        if data.total_count
        else "검색 조건에 해당하는 병원이 없습니다.",
        data=data,
    )
