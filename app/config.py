from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://roomscout_app:roomscout_local_app@localhost:55432/roomscout"
    checkpoint_url: str = "postgresql://roomscout_app:roomscout_local_app@localhost:55432/roomscout?options=-csearch_path%3Dcheckpoints"
    demo_token: str = "roomscout-local-demo"
    poll_interval: float = 0.5
    memory_debounce_seconds: int = 30


settings = Settings()
