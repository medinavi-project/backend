from typing import Generic, Optional, TypeVar
from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    code: str
    message: str
    data: Optional[T] = None


class OpenHours(BaseModel):
    day_of_week: str
    open_time: Optional[str] = None
    close_time: Optional[str] = None
    is_closed: bool


class Hospital(BaseModel):
    hospital_id: str
    name: str
    address: str
    latitude: float
    longitude: float
    department: str  # 요청한 진료과 중 이 병원이 해당하는 첫 번째
    departments: list[str] = []  # 이 병원이 해당하는 요청 진료과 전부
    phone: Optional[str] = None
    open_hours: list[OpenHours]
    is_open: bool
    distance_km: float


class HospitalListResponse(BaseModel):
    as_of: str
    source_updated_at: Optional[str] = None
    total_count: int
    hospitals: list[Hospital]
