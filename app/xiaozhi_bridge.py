from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import mcp.types as types
from mcp.client.websocket import websocket_client
from mcp.server.fastmcp import FastMCP
from mcp.shared.message import SessionMessage

from .config import mask_endpoint_url
from .repository import Repository


logger = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BridgeStatus:
    enabled: bool = False
    endpoint_url: str = ""
    state: str = "disabled"
    connected: bool = False
    last_error: str = ""
    last_attempt_at: str | None = None
    last_connected_at: str | None = None
    reconnect_delay_seconds: int = 5

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "endpoint_url": self.endpoint_url,
            "state": self.state,
            "connected": self.connected,
            "last_error": self.last_error,
            "last_attempt_at": self.last_attempt_at,
            "last_connected_at": self.last_connected_at,
            "reconnect_delay_seconds": self.reconnect_delay_seconds,
        }


class LoggedReceiveStream:
    def __init__(self, stream: Any, bridge: "XiaozhiBridge") -> None:
        self.stream = stream
        self.bridge = bridge

    async def __aenter__(self) -> "LoggedReceiveStream":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    def __aiter__(self) -> "LoggedReceiveStream":
        return self

    async def __anext__(self) -> Any:
        item = await self.stream.__anext__()
        self.bridge.record_protocol_message("request", item)
        return item

    async def receive(self) -> Any:
        item = await self.stream.receive()
        self.bridge.record_protocol_message("request", item)
        return item

    async def aclose(self) -> None:
        await self.stream.aclose()


class LoggedSendStream:
    def __init__(self, stream: Any, bridge: "XiaozhiBridge") -> None:
        self.stream = stream
        self.bridge = bridge

    async def __aenter__(self) -> "LoggedSendStream":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def send(self, item: Any) -> None:
        self.bridge.record_protocol_message("response", item)
        await self.stream.send(item)

    async def aclose(self) -> None:
        await self.stream.aclose()


class XiaozhiBridge:
    def __init__(
        self,
        fastmcp: FastMCP,
        repository: Repository,
        reconnect_delay_seconds: int = 5,
    ) -> None:
        self.fastmcp = fastmcp
        self.repository = repository
        self.status = BridgeStatus(reconnect_delay_seconds=reconnect_delay_seconds)
        self._endpoint_url = ""
        self._configuration_changed = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._active_write_stream: LoggedSendStream | None = None

    def configure(self, *, enabled: bool, endpoint_url: str) -> None:
        normalized_url = endpoint_url.strip()
        changed = enabled != self.status.enabled or normalized_url != self._endpoint_url
        self._endpoint_url = normalized_url
        self.status.enabled = enabled and bool(normalized_url)
        self.status.endpoint_url = mask_endpoint_url(normalized_url)
        if not self.status.enabled:
            self.status.state = "disabled"
            self.status.connected = False
        if changed:
            self._configuration_changed.set()

    def request_reconnect(self) -> None:
        self._configuration_changed.set()

    def snapshot(self) -> dict[str, Any]:
        return self.status.as_dict()

    async def run_forever(self) -> None:
        try:
            while True:
                self._configuration_changed.clear()
                endpoint_url = self._endpoint_url
                if not self.status.enabled or not endpoint_url:
                    self.status.state = "disabled"
                    self.status.connected = False
                    await self._configuration_changed.wait()
                    continue

                self.status.state = "connecting"
                self.status.connected = False
                self.status.last_attempt_at = utc_now()
                self.status.last_error = ""
                self.repository.add_activity_log(
                    category="xiaozhi",
                    source="bridge",
                    event="Connect Xiaozhi bridge",
                    direction="outbound",
                    request={"endpoint": mask_endpoint_url(endpoint_url)},
                )

                connection_task = asyncio.create_task(self._run_once(endpoint_url))
                change_task = asyncio.create_task(self._configuration_changed.wait())
                try:
                    done, _ = await asyncio.wait(
                        {connection_task, change_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if change_task in done:
                        connection_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await connection_task
                        continue

                    change_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await change_task
                    await connection_task
                    raise RuntimeError("Xiaozhi WebSocket connection closed")
                except asyncio.CancelledError:
                    connection_task.cancel()
                    change_task.cancel()
                    await asyncio.gather(connection_task, change_task, return_exceptions=True)
                    raise
                except Exception as exc:  # noqa: BLE001
                    self.status.state = "retrying"
                    self.status.connected = False
                    self.status.last_error = str(exc)
                    self.repository.add_activity_log(
                        category="xiaozhi",
                        source="bridge",
                        event="Xiaozhi bridge disconnected",
                        level="error",
                        response={"error": str(exc)},
                        success=False,
                    )
                    logger.exception("Xiaozhi bridge disconnected")

                try:
                    await asyncio.wait_for(
                        self._configuration_changed.wait(),
                        timeout=self.status.reconnect_delay_seconds,
                    )
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            self.status.state = "stopped"
            self.status.connected = False
            raise

    async def _run_once(self, endpoint_url: str) -> None:
        logger.info("Connecting Xiaozhi MCP bridge: %s", mask_endpoint_url(endpoint_url))
        async with websocket_client(endpoint_url) as (read_stream, write_stream):
            logged_read_stream = LoggedReceiveStream(read_stream, self)
            logged_write_stream = LoggedSendStream(write_stream, self)
            self.status.state = "connected"
            self.status.connected = True
            self.status.last_connected_at = utc_now()
            self.status.last_error = ""
            self._active_write_stream = logged_write_stream
            self.repository.add_activity_log(
                category="xiaozhi",
                source="bridge",
                event="Xiaozhi bridge connected",
                response={"endpoint": mask_endpoint_url(endpoint_url)},
            )
            try:
                await self.fastmcp._mcp_server.run(
                    logged_read_stream,
                    logged_write_stream,
                    self.fastmcp._mcp_server.create_initialization_options(),
                    stateless=False,
                )
            finally:
                if self._active_write_stream is logged_write_stream:
                    self._active_write_stream = None

    async def push_alert(
        self,
        title: str,
        message: str,
        *,
        payload: dict[str, Any] | None = None,
        level: str = "warning",
    ) -> None:
        if not self.status.connected or self._active_write_stream is None:
            raise RuntimeError("Xiaozhi bridge is not connected")

        level_name = level if level in {"debug", "info", "warning", "error"} else "warning"
        text = f"{title}: {message}" if title else message
        if payload:
            server_name = payload.get("server")
            if server_name and server_name not in text:
                text = f"{server_name}, {text}"

        notification = types.ServerNotification(
            types.LoggingMessageNotification(
                params=types.LoggingMessageNotificationParams(
                    level=level_name,
                    logger="mcpeye.alert",
                    data=text,
                )
            )
        )
        session_message = SessionMessage(
            message=types.JSONRPCMessage(
                types.JSONRPCNotification(
                    jsonrpc="2.0",
                    **notification.model_dump(by_alias=True, mode="json", exclude_none=True),
                )
            )
        )

        async with self._send_lock:
            await self._active_write_stream.send(session_message)

    def record_protocol_message(self, direction: str, item: Any) -> None:
        if isinstance(item, Exception):
            self.repository.add_activity_log(
                category="mcp_protocol",
                source="xiaozhi",
                event="Protocol parse error",
                level="error",
                direction=direction,
                response={"error": str(item)},
                success=False,
            )
            return
        if not isinstance(item, SessionMessage):
            return
        payload = item.message.model_dump(by_alias=True, mode="json", exclude_none=True)
        event = payload.get("method") or ("MCP response" if "result" in payload else "MCP error response")
        request_id = str(payload.get("id", ""))
        is_success = "error" not in payload
        self.repository.add_activity_log(
            category="mcp_protocol",
            source="xiaozhi",
            event=event,
            level="info" if is_success else "error",
            direction=direction,
            request_id=request_id,
            request=payload if direction == "request" else None,
            response=payload if direction == "response" else None,
            success=is_success,
        )
