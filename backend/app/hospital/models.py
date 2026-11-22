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
    department: str
    phone: Optional[str] = None
    open_hours: list[OpenHours]
    is_open: bool
    distance_km: float


class HospitalListResponse(BaseModel):
    as_of: str
    source_updated_at: Optional[str] = None
    total_count: int
    hospitals: list[Hospital]
