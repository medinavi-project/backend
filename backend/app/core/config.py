from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore"
    )
    APP_NAME: str = "MediNavi"
    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    REDIS_URL: str = "redis://localhost:6379/0"
    SESSION_TTL_SECONDS: int = Field(default=1800, ge=1)
    MAX_CHAT_TURNS: int = Field(default=5, ge=1, le=5)
    AI_PROVIDER: str = "gemini"
    GEMINI_API: SecretStr = SecretStr("")
    GEMINI_MODEL: str = "gemini-2.5-flash-lite"
    AI_REQUEST_TIMEOUT_SECONDS: float = Field(default=30, gt=0, le=120)
    AI_MOCK_ENABLED: bool = False
    HOSPITAL_DATA_PROVIDER: str = "demo"
    MEDICATION_DATA_PROVIDER: str = "demo"
    MFDS_API_KEY: SecretStr = SecretStr("")
    CLOVA_API_KEY: SecretStr = SecretStr("")
    CLOVA_MODEL: str = "HCX-005"
    MEDICATION_AI_ENABLED: bool = True
    OCR_PROVIDER: str = "disabled"
    CLOVA_OCR_INVOKE_URL: str = ""
    CLOVA_OCR_SECRET: SecretStr = SecretStr("")
    OCR_REQUEST_TIMEOUT_SECONDS: float = Field(default=30, gt=0, le=120)
    OCR_API_URL: str = ""
    OCR_API_TOKEN: SecretStr = SecretStr("")
    TTS_PROVIDER: str = "disabled"
    CORS_ORIGINS: str = "http://localhost:8501"

    @model_validator(mode="after")
    def validate_modes(self):
        if self.AI_PROVIDER not in {"gemini", "mock"}:
            raise ValueError("Unsupported AI_PROVIDER")
        if self.AI_PROVIDER == "mock" and (
            not self.AI_MOCK_ENABLED or self.APP_ENV not in {"development", "test"}
        ):
            raise ValueError("Mock requires AI_MOCK_ENABLED=true and development/test")
        if (
            self.OCR_PROVIDER not in {"disabled", "http", "mock", "clova"}
            or self.TTS_PROVIDER != "disabled"
        ):
            raise ValueError("OCR and TTS are not implemented; use disabled")
        if self.OCR_PROVIDER == "http" and not self.OCR_API_URL.startswith(
            ("http://", "https://")
        ):
            raise ValueError("OCR_API_URL is required")
        if self.OCR_PROVIDER == "mock" and (
            self.APP_ENV not in {"development", "test"} or not self.AI_MOCK_ENABLED
        ):
            raise ValueError("OCR mock requires explicit development/test mock mode")
        if (
            self.HOSPITAL_DATA_PROVIDER != "demo"
            or self.MEDICATION_DATA_PROVIDER != "demo"
        ):
            raise ValueError("Only demo data providers are available")
        if self.APP_ENV == "production" and "*" in self.CORS_ORIGINS.split(","):
            raise ValueError("Wildcard CORS is not allowed in production")
        return self
