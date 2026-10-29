from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Desarrollo: se mantiene bloqueado en código, sin variable de entorno para desactivarlo.
READ_ONLY_MODE = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://avantia:avantia@db:5432/avantia"
    app_secret: str = ""
    cors_origin: str = "http://localhost:15173"
    public_base_url: str = "http://localhost:15173"
    availability_max_age_hours: int = 6
    availability_source: str = "csv"
    overdue_minutes: int = 15
    worker_poll_seconds: int = 5
    csv_watch_dir: str = "/data/imports"
    csv_column_map: str = ""
    ollama_url: str = ""
    ollama_model: str = ""
    ollama_timeout_seconds: int = 45
    gowa_url: str = "http://gowa:3000"
    gowa_basic_auth: str = ""
    gowa_webhook_secret: str = ""
    gowa_device_id: str = ""
    gowa_internal_chat_id: str = ""
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_poll_seconds: int = 120
    imap_initial_backfill: int = 0
    mail_message_url_template: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    booking_sender_domains: str = "booking.com"
    admin_username: str = ""
    admin_password: str = ""
    avantio_export_url: str = ""
    avantio_username: str = ""
    avantio_password: str = ""
    avantio_sync_interval_hours: int = Field(default=6, ge=1, le=168)
    avantio_storage_state_path: str = "/run/avantio-auth/storage-state.json"
    avantio_import_dir: str = "/run/avantio-import"


@lru_cache
def get_settings() -> Settings:
    return Settings()
