"""VoiceForge configuration from environment variables."""

import os
# Disable Objective-C fork safety checks on macOS to prevent crashes in multi-threaded child forks (e.g. PyTorch/MPS + Celery)
os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"

import secrets
from pathlib import Path
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings

BACKEND_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    # Server. The API has no authentication, so it only listens on this computer by
    # default. Set HOST=0.0.0.0 only on a trusted network.
    HOST: str = "127.0.0.1"
    PORT: int = 8001
    DEBUG: bool = False
    RVC_SERVE_FRONTEND: bool = False
    SECRET_KEY: str = secrets.token_hex(32)

    # Database (relative SQLite paths are resolved against voiceforge-backend/)
    DATABASE_URL: str = "sqlite:///./voiceforge.db"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Celery
    CELERY_WORKERS: int = 4
    MAX_CONCURRENT_DOWNLOADS: int = 3

    # Storage (relative paths are resolved against voiceforge-backend/, not the shell's cwd)
    BASE_DIR: Path = BACKEND_DIR
    OUTPUT_DIR: Path = BACKEND_DIR / "static" / "outputs"
    TEMP_DIR: Path = BACKEND_DIR / "tmp"
    MAX_OUTPUT_SIZE_GB: float = 50.0
    RETENTION_DAYS: int = 7

    # HuggingFace
    HF_TOKEN: str = ""

    # ML Models
    DEMUCS_MODEL: str = "htdemucs_ft"
    WHISPER_MODEL: str = "large-v3"
    PYANNOTE_MODEL: str = "pyannote/speaker-diarization-3.1"
    USE_GPU: str = "auto"  # auto | cuda | cpu | mps

    # Processing Defaults
    DEFAULT_SAMPLE_RATE: int = 22050
    DEFAULT_BIT_DEPTH: int = 16
    DEFAULT_MIN_CLIP_S: float = 1.5
    DEFAULT_MAX_CLIP_S: float = 25.0
    DEFAULT_MIN_SNR_DB: float = 15.0
    DEFAULT_QUALITY_THRESHOLD: int = 60

    # Rate Limiting
    ANIDB_DELAY_S: float = 2.5
    YOUTUBE_MAX_RESULTS: int = 50
    BILIBILI_MAX_RESULTS: int = 30

    # CORS: extra browser origins allowed to call the API. Any http://localhost:<port> and
    # http://127.0.0.1:<port> origin is always allowed (see main.py); the bundled UI uses
    # the Vite proxy and does not need CORS at all.
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    model_config = {
        "env_file": str(BACKEND_DIR / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @model_validator(mode="after")
    def _resolve_paths(self) -> "Settings":
        base = Path(self.BASE_DIR).resolve()
        if not self.OUTPUT_DIR.is_absolute():
            self.OUTPUT_DIR = (base / self.OUTPUT_DIR).resolve()
        if not self.TEMP_DIR.is_absolute():
            self.TEMP_DIR = (base / self.TEMP_DIR).resolve()
        prefix = "sqlite:///"
        if self.DATABASE_URL.startswith(prefix):
            db_path = self.DATABASE_URL[len(prefix):]
            if db_path and db_path != ":memory:" and not Path(db_path).is_absolute():
                self.DATABASE_URL = prefix + str((base / db_path).resolve())
        return self

    def ensure_dirs(self) -> None:
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.TEMP_DIR.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
