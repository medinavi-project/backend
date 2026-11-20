from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import StringConstraints

from app.core.response import ApiResponse
from app.schemas.hospitals import HospitalResponse

router = APIRouter(prefix="/api/v1/hospitals", tags=["병원"])


@router.get("/open", response_model=ApiResponse[HospitalResponse])
async def hospitals(
    request: Request,
    department: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ],
    latitude: Annotated[float, Query(ge=-90, le=90, allow_inf_nan=False)],
    longitude: Annotated[float, Query(ge=-180, le=180, allow_inf_nan=False)],
    radius: Annotated[int, Query(ge=100, le=20000)] = 3000,
):
    data = request.app.state.hospitals.search(department, latitude, longitude, radius)
    return ApiResponse(
        code="SUCCESS",
        message="병원 검색이 완료되었습니다."
        if data.total_count
        else "검색 조건에 해당하는 병원이 없습니다.",
        data=data,
    )
