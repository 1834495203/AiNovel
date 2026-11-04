from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    database_url: str = "postgresql+asyncpg://novel:change-me@localhost:5432/novel"
    redis_url: str = "redis://localhost:6379/0"
    access_password_hash: SecretStr = SecretStr("")
    access_session_secret: SecretStr = SecretStr("")
    access_session_hours: int = Field(default=12, ge=1, le=168)
    llm_provider: Literal["mock", "openai"] = "mock"
    llm_base_url: str = Field(
        default="https://api.openai.com/v1", validation_alias=AliasChoices("LLM_BASE_URL", "OPENAI_BASE_URL")
    )
    llm_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("LLM_API_KEY", "OPENAI_API_KEY")
    )
    llm_model: str = "gpt-4.1-mini"
    llm_thinking: Literal["enabled", "disabled"] = "disabled"
    llm_max_tokens: int = Field(default=8192, ge=256, le=65536)
    llm_output_retries: int = Field(default=2, ge=0, le=3)
    embedding_provider: Literal["hash", "openai"] = "hash"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: SecretStr = SecretStr("")
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=8)
    max_rewrites: int = Field(default=2, ge=0, le=5)
    recent_scenes: int = Field(default=2, ge=1, le=10)
    story_maintenance_interval: int = Field(default=5, ge=2, le=50)
    agent_max_steps: int = Field(default=6, ge=1, le=20)
    chunk_target: int = Field(default=1500, ge=100)
    model_timeout: float = Field(default=120, ge=1)
