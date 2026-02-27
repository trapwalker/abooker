from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATA_DIR: Path = Path("/data")

    QB_HOST: str = "qbittorrent"
    QB_PORT: int = 8080
    QB_USER: str = "admin"
    QB_PASS: str = "adminadmin"

    JACKETT_URL: str = "http://jackett:9117"
    JACKETT_KEY: str = ""

    WARMUP_COUNT: int = 2
    WARMUP_DEPTH: int = 3
    CLIENT_TTL_DAYS: int = 14

    BOT_TOKEN: str = ""
    BOT_ADMIN_IDS: list[int] = []
    WEB_BASE_URL: str = "http://localhost:8000"

    MAX_FILE_SIZE_GB: float = 5.0
    MIN_AUDIO_RATIO: float = 0.5

    @property
    def db_path(self) -> Path:
        return self.DATA_DIR / "abooker.db"

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"


settings = Settings()
