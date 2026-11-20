from datetime import datetime
from math import asin, cos, radians, sin, sqrt
from zoneinfo import ZoneInfo

from app.schemas.hospitals import Hospital, HospitalResponse


class HospitalService:
    def __init__(self, data):
        self.data = data

    def search(self, department, latitude, longitude, radius, now=None):
        now = (now or datetime.now(ZoneInfo("Asia/Seoul"))).astimezone(
            ZoneInfo("Asia/Seoul")
        )
        day = [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ][now.weekday()]
        clock = now.strftime("%H:%M")
        results = []
        for hospital in self.data.hospitals():
            if department not in hospital["departments"]:
                continue
            lat1, lat2 = radians(latitude), radians(hospital["latitude"])
            dlat, dlon = lat2 - lat1, radians(hospital["longitude"] - longitude)
            distance = (
                6371000
                * 2
                * asin(
                    sqrt(
                        min(
                            1,
                            sin(dlat / 2) ** 2
                            + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2,
                        )
                    )
                )
            )
            is_open = any(
                start <= clock < end
                for start, end in (
                    slot.split("-") for slot in hospital["open_hours"].get(day, [])
                )
            )
            if distance <= radius and is_open:
                results.append(
                    Hospital(**hospital, distance_meters=round(distance), is_open=True)
                )
        results.sort(key=lambda h: h.distance_meters)
        return HospitalResponse(total_count=len(results), hospitals=results)
