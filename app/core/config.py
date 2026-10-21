from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+asyncpg://postgres:dev@localhost:5432/trainer_test"
    redis_url: str = "redis://localhost:6379/0"
    jwt_keys: dict[str, str] = {}
    jwt_current_kid: str = "k1"
    access_ttl_seconds: int = 900
    refresh_ttl_days: int = 30
    ip_hash_salt: str = ""
    s3_bucket: str = ""
    public_base_url: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
