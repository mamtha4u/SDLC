from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORKESTRA_", env_file=".env", extra="ignore")

    app_name: str = "Orkestra"
    data_dir: Path = BACKEND_DIR / "data"
    frontend_dist: Path = BACKEND_DIR.parent / "frontend" / "dist"
    prompts_dir: Path = BACKEND_DIR / "prompts"

    aws_account_id: str = "144831534428"
    aws_region: str = "eu-west-1"
    environment: str = "sandbox"

    session_ttl_hours: int = 72
    cookie_secure: bool = False  # true once served over TLS
    login_attempts_per_minute: int = 8

    job_workers: int = 4
    # Terra's drift watch: compare every live, idle project with its Terraform state and Dev's packages (0 = off)
    drift_check_minutes: int = 30
    crew_watch_seconds: int = 60  # agents/watch.py: how often Orion and Archie look at the working agents (0 = off)

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{(self.data_dir / 'orkestra.db').as_posix()}"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def master_key_file(self) -> Path:
        return self.data_dir / "master.key"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.projects_dir.mkdir(parents=True, exist_ok=True)
    return s
