from pydantic import BaseModel


class Hospital(BaseModel):
    hospital_id: str
    name: str
    address: str
    latitude: float
    longitude: float
    departments: list[str]
    phone: str | None
    distance_meters: int
    is_open: bool
    open_hours: dict[str, list[str]]


class HospitalResponse(BaseModel):
    total_count: int
    data_source: str = "DEMO"
    hospitals: list[Hospital]
