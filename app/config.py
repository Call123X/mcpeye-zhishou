from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "app" / "templates"
STATIC_DIR = BASE_DIR / "app" / "static"
SECRET_FILE = DATA_DIR / ".app_secret"
DATABASE_PATH = DATA_DIR / "server_monitor.db"
DEFAULT_XIAOZHI_ENDPOINT_BASE = "wss://api.xiaozhi.me/mcp/"


def _load_or_create_secret() -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    secret = secrets.token_urlsafe(48)
    SECRET_FILE.write_text(secret, encoding="utf-8")
    return secret


class Settings(BaseSettings):
    app_host: str = "127.0.0.1"
    app_port: int = 8765
    admin_username: str = "admin"
    admin_password: str = "admin123456"
    app_secret: str = ""
    xiaozhi_bridge_enabled: bool = False
    xiaozhi_endpoint_url: str = ""
    xiaozhi_reconnect_delay_seconds: int = 5

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def resolved_secret(self) -> str:
        return self.app_secret or _load_or_create_secret()

    @property
    def masked_xiaozhi_endpoint_url(self) -> str:
        return mask_endpoint_url(self.xiaozhi_endpoint_url)


def mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 20:
        return "******"
    return f"{token[:10]}...{token[-8:]}"


def mask_endpoint_url(endpoint_url: str) -> str:
    if not endpoint_url:
        return ""
    parts = urlsplit(endpoint_url)
    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        query_items.append((key, mask_token(value) if key.lower() == "token" else value))
    return urlunsplit(parts._replace(query=urlencode(query_items)))


def split_xiaozhi_endpoint(endpoint_url: str) -> tuple[str, str]:
    if not endpoint_url:
        return DEFAULT_XIAOZHI_ENDPOINT_BASE, ""
    parts = urlsplit(endpoint_url)
    token = ""
    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() == "token":
            token = value
        else:
            query_items.append((key, value))
    base_url = urlunsplit(parts._replace(query=urlencode(query_items)))
    return base_url, token


def build_xiaozhi_endpoint(base_url: str, token: str) -> str:
    parts = urlsplit(base_url.strip())
    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() != "token"
    ]
    query_items.append(("token", token.strip()))
    return urlunsplit(parts._replace(query=urlencode(query_items)))


settings = Settings()
