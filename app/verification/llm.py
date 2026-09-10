"""Provider settings and errors for the Geometry Repair Router."""

from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ROOMSCOUT_LLM_",
        env_file=".env",
        extra="ignore",
    )

    base_url: str = ""
    api_key: SecretStr = SecretStr("")
    model: str = ""
    timeout_seconds: float = Field(default=60, gt=0, le=300)
    max_tokens: int = Field(default=1500, ge=1, le=16000)
    token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"

    @model_validator(mode="after")
    def valid_url(self):
        if self.base_url:
            parsed = urlparse(self.base_url)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("base_url must be an HTTP(S) API root without credentials or query")
        return self

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    def public(self) -> dict:
        return self.model_dump(exclude={"api_key"})


class LLMRepairError(RuntimeError):
    """The provider could not produce a valid tool selection."""
