import secrets
from functools import lru_cache
from typing import Self

from pydantic import model_validator
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
    media_base_url: str = ""  # public base URL for stored media; defaults to the S3 bucket URL
    public_base_url: str = "http://localhost:8000"

    @model_validator(mode="after")
    def _require_secrets_outside_dev(self) -> Self:
        if self.jwt_keys and self.jwt_current_kid not in self.jwt_keys:
            raise ValueError("jwt_current_kid must be one of jwt_keys")
        if self.env == "dev":
            if not self.jwt_keys:  # ephemeral key: tokens die on restart, fine for local dev
                self.jwt_keys = {self.jwt_current_kid: secrets.token_urlsafe(48)}
            if not self.ip_hash_salt:
                self.ip_hash_salt = secrets.token_urlsafe(16)
        elif not self.jwt_keys or not self.ip_hash_salt:
            raise ValueError("jwt_keys and ip_hash_salt are required when env != 'dev'")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
