import json
from pathlib import Path
from typing import Protocol

from app.core.exceptions import BusinessException
from app.core.response_codes import Code


class DataProvider(Protocol):
    def hospitals(self) -> list[dict]: ...
    def medications(self) -> list[dict]: ...


class DemoDataProvider:
    def _read(self, name, code):
        try:
            return json.loads(
                (Path(__file__).resolve().parents[1] / "data" / name).read_text()
            )
        except (OSError, ValueError):
            raise BusinessException(code) from None

    def hospitals(self):
        return self._read("demo_hospitals.json", Code.HOSPITAL_DATA_UNAVAILABLE)

    def medications(self):
        return self._read("demo_medications.json", Code.MEDICATION_INFO_UNAVAILABLE)
