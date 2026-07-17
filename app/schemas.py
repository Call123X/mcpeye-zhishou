from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class LoginPayload(BaseModel):
    username: str
    password: str


class ServerPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=100)
    auth_type: Literal["password", "key"]
    password: str | None = None
    private_key: str | None = None
    private_key_passphrase: str | None = None
    notes: str = ""
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            items = [item.strip() for item in value.split(",")]
            return [item for item in items if item]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise ValueError("Unsupported tag format")


class CustomCheckPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    command: str = Field(min_length=1)


class MonitorCommandPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    command: str = Field(min_length=1)
    scope_all_servers: bool = False
    server_ids: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("server_ids", mode="before")
    @classmethod
    def normalize_server_ids(cls, value: object) -> list[int]:
        if value is None:
            return []
        if isinstance(value, list):
            items: list[int] = []
            for item in value:
                try:
                    parsed = int(item)
                except (TypeError, ValueError) as exc:
                    raise ValueError("Invalid server id") from exc
                if parsed > 0 and parsed not in items:
                    items.append(parsed)
            return items
        raise ValueError("Unsupported server id format")

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            items = [item.strip() for item in value.split(",")]
            return [item for item in items if item]
        if isinstance(value, list):
            normalized: list[str] = []
            seen: set[str] = set()
            for item in value:
                text = str(item).strip()
                if not text:
                    continue
                lowered = text.lower()
                if lowered in seen:
                    continue
                seen.add(lowered)
                normalized.append(text)
            return normalized
        raise ValueError("Unsupported tag format")


class CommandRunPayload(BaseModel):
    server_id: int = Field(ge=1)


class MetricQuery(BaseModel):
    metric: str = Field(min_length=1, max_length=100)


class XiaozhiSettingsPayload(BaseModel):
    enabled: bool = True
    endpoint_base_url: str = Field(min_length=1, max_length=500)
    token: str | None = Field(default=None, max_length=4096)


class AlertSettingsPayload(BaseModel):
    enabled: bool = False
    interval_seconds: int = Field(default=60, ge=15, le=3600)
    notify_offline: bool = True
    notify_recovery: bool = True
