"""Small application settings shared by the HTTP layer."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    demo_token: str = "roomscout-local-demo"


settings = Settings()
