from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

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
                    event="连接小智",
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
                    raise RuntimeError("小智 WebSocket 连接已关闭")
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
                        event="小智连接中断",
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
            self.status.state = "connected"
            self.status.connected = True
            self.status.last_connected_at = utc_now()
            self.status.last_error = ""
            self.repository.add_activity_log(
                category="xiaozhi",
                source="bridge",
                event="小智连接成功",
                response={"endpoint": mask_endpoint_url(endpoint_url)},
            )
            await self.fastmcp._mcp_server.run(
                LoggedReceiveStream(read_stream, self),
                LoggedSendStream(write_stream, self),
                self.fastmcp._mcp_server.create_initialization_options(),
                stateless=False,
            )

    def record_protocol_message(self, direction: str, item: Any) -> None:
        if isinstance(item, Exception):
            self.repository.add_activity_log(
                category="mcp_protocol",
                source="xiaozhi",
                event="协议解析错误",
                level="error",
                direction=direction,
                response={"error": str(item)},
                success=False,
            )
            return
        if not isinstance(item, SessionMessage):
            return
        payload = item.message.model_dump(by_alias=True, mode="json", exclude_none=True)
        event = payload.get("method") or ("MCP 响应" if "result" in payload else "MCP 错误响应")
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
