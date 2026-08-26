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

    # Minimum free disk space to keep available (GB). When free space on the
    # DATA_DIR filesystem drops below this, cached chapters are evicted
    # globally (oldest last-requested first) regardless of per-book warmup
    # windows, until the threshold is satisfied again.
    MIN_FREE_DISK_GB: float = 3.0

    BOT_TOKEN: str = ""
    BOT_ADMIN_IDS: list[int] = []
    WEB_BASE_URL: str = "http://localhost:8000"
    WEB_HOST: str = "0.0.0.0"
    WEB_PORT: int = 8000

    MAX_FILE_SIZE_GB: float = 5.0
    MIN_AUDIO_RATIO: float = 0.5

    # When true, serve_file responds with an X-Accel-Redirect header instead
    # of streaming the file itself, offloading byte-transfer to nginx (see
    # deploy/nginx/*.conf, location /_internal_files/). Off by default so a
    # plain `uvicorn --reload` dev run (no nginx in front) still works.
    NGINX_ACCEL: bool = False
    NGINX_ACCEL_PREFIX: str = "/_internal_files"

    @property
    def db_path(self) -> Path:
        return self.DATA_DIR / "abooker.db"

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"


settings = Settings()
