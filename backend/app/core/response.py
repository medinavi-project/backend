from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):  # noqa: UP046 - specified public DTO pattern
    code: str
    message: str
    data: T | None
