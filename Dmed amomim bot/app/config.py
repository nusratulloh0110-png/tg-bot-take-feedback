from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = Field(..., alias="BOT_TOKEN")
    bot_username: str | None = Field(default=None, alias="BOT_USERNAME")
    database_url: str = Field(..., alias="DATABASE_URL")
    redis_url: str | None = Field(default=None, alias="REDIS_URL")
    admin_password: str = Field(..., alias="ADMIN_PASSWORD")
    admin_ids: list[int] = Field(default_factory=list, alias="ADMIN_IDS")
    timezone: str = Field(default="Asia/Tashkent", alias="TIMEZONE")
    weekly_digest_enabled: bool = Field(default=True, alias="WEEKLY_DIGEST_ENABLED")
    auto_create_tables: bool = Field(default=False, alias="AUTO_CREATE_TABLES")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: Any) -> list[int]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [int(item) for item in value]
        if isinstance(value, str):
            return [int(item.strip()) for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()

