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
    MAX_CHAT_TURNS: int = Field(default=7, ge=1, le=10)
    AI_PROVIDER: str = "clova_triage"
    AI_REQUEST_TIMEOUT_SECONDS: float = Field(default=30, gt=0, le=120)
    AI_MOCK_ENABLED: bool = False
    MFDS_API_KEY: SecretStr = SecretStr("")
    CLOVA_API_KEY: SecretStr = SecretStr("")
    CLOVA_MODEL: str = "HCX-005"
    # Read via os.getenv by the vendored hosAI modules (env_file supplies them);
    # declared here so they are documented and kept out of reprs.
    # 문진 엔진(hosAI) 전용 CLOVA Studio 키. CLOVA_API_KEY(약 설명)와 별개.
    CLOVASTUDIO_API_KEY: SecretStr = SecretStr("")
    DATA_GO_KR_KEY: SecretStr = SecretStr("")
    NCP_MAPS_CLIENT_ID: SecretStr = SecretStr("")
    NCP_MAPS_CLIENT_SECRET: SecretStr = SecretStr("")
    KDCA_API_KEY: SecretStr = SecretStr("")
    # CLOVA Voice(복약 안내 음성) 전용 NCP 키. 지도 키(NCP_MAPS_*)와 별개.
    CLOVA_VOICE_CLIENT_ID: SecretStr = SecretStr("")
    CLOVA_VOICE_CLIENT_SECRET: SecretStr = SecretStr("")
    CLOVA_VOICE_SPEAKER: str = "nara"
    HOSPITAL_RADIUS_MAX_KM: float = 10
    HOSPITAL_MAX_RESULTS: int = 20
    MEDICATION_AI_ENABLED: bool = True
    OCR_PROVIDER: str = "disabled"
    CLOVA_OCR_INVOKE_URL: str = ""
    CLOVA_OCR_SECRET: SecretStr = SecretStr("")
    OCR_REQUEST_TIMEOUT_SECONDS: float = Field(default=30, gt=0, le=120)
    CORS_ORIGINS: str = "http://localhost:8501"

    @model_validator(mode="after")
    def validate_modes(self):
        if self.AI_PROVIDER not in {"clova_triage", "mock"}:
            raise ValueError("Unsupported AI_PROVIDER")
        if self.AI_PROVIDER == "mock" and (
            not self.AI_MOCK_ENABLED or self.APP_ENV not in {"development", "test"}
        ):
            raise ValueError("Mock requires AI_MOCK_ENABLED=true and development/test")
        if self.OCR_PROVIDER not in {"disabled", "mock", "clova"}:
            raise ValueError("Unsupported OCR_PROVIDER")
        if self.OCR_PROVIDER == "mock" and (
            self.APP_ENV not in {"development", "test"} or not self.AI_MOCK_ENABLED
        ):
            raise ValueError("OCR mock requires explicit development/test mock mode")
        if self.APP_ENV == "production" and "*" in self.CORS_ORIGINS.split(","):
            raise ValueError("Wildcard CORS is not allowed in production")
        return self
