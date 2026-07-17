from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .repository import Repository
from .ssh_monitor import SSHMonitorService
from .xiaozhi_bridge import XiaozhiBridge


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AlertingStatus:
    enabled: bool = False
    interval_seconds: int = 60
    notify_offline: bool = True
    notify_recovery: bool = True
    state: str = "disabled"
    last_error: str = ""
    last_checked_at: str | None = None
    last_alert_at: str | None = None
    sent_alerts: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "interval_seconds": self.interval_seconds,
            "notify_offline": self.notify_offline,
            "notify_recovery": self.notify_recovery,
            "state": self.state,
            "last_error": self.last_error,
            "last_checked_at": self.last_checked_at,
            "last_alert_at": self.last_alert_at,
            "sent_alerts": self.sent_alerts,
        }


class AlertingService:
    def __init__(
        self,
        repository: Repository,
        monitor: SSHMonitorService,
        bridge: XiaozhiBridge,
    ) -> None:
        self.repository = repository
        self.monitor = monitor
        self.bridge = bridge
        self.status = AlertingStatus()
        self._configuration_changed = asyncio.Event()
        self._last_health: dict[int, dict[str, Any]] = {}

    def configure(
        self,
        *,
        enabled: bool,
        interval_seconds: int,
        notify_offline: bool,
        notify_recovery: bool,
    ) -> None:
        normalized_interval = max(15, min(int(interval_seconds), 3600))
        changed = (
            enabled != self.status.enabled
            or normalized_interval != self.status.interval_seconds
            or notify_offline != self.status.notify_offline
            or notify_recovery != self.status.notify_recovery
        )
        self.status.enabled = enabled
        self.status.interval_seconds = normalized_interval
        self.status.notify_offline = notify_offline
        self.status.notify_recovery = notify_recovery
        if not enabled:
            self.status.state = "disabled"
            self.status.last_error = ""
            self._last_health = {}
        if changed:
            self._configuration_changed.set()

    def snapshot(self) -> dict[str, Any]:
        return self.status.as_dict()

    async def run_forever(self) -> None:
        try:
            while True:
                self._configuration_changed.clear()
                if not self.status.enabled:
                    self.status.state = "disabled"
                    await self._configuration_changed.wait()
                    continue

                self.status.state = "running"
                try:
                    rows = await self.monitor.get_status_board()
                    self.status.last_checked_at = utc_now()
                    self.status.last_error = ""
                    await self._process_rows(rows)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    self.status.state = "error"
                    self.status.last_error = str(exc)
                    self.repository.add_activity_log(
                        category="alert",
                        source="scheduler",
                        event="Alert inspection failed",
                        level="error",
                        response={"error": str(exc)},
                        success=False,
                    )

                try:
                    await asyncio.wait_for(
                        self._configuration_changed.wait(),
                        timeout=self.status.interval_seconds,
                    )
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            self.status.state = "stopped"
            raise

    async def send_test_alert(self) -> None:
        message = "McpEye 智守已向小智发送主动告警测试。"
        await self.bridge.push_alert("测试告警", message)
        self.status.last_alert_at = utc_now()
        self.status.sent_alerts += 1
        self.repository.add_activity_log(
            category="alert",
            source="web",
            event="Send test alert",
            request={"message": message},
            response={"delivered": True},
        )

    async def _process_rows(self, rows: list[dict[str, Any]]) -> None:
        current_health: dict[int, dict[str, Any]] = {}
        for row in rows:
            server_id = int(row["server_id"])
            current_health[server_id] = row
            previous = self._last_health.get(server_id)
            alert = self._build_alert(previous, row)
            if alert is None:
                continue
            await self._deliver_alert(alert)
        self._last_health = current_health

    def _build_alert(
        self,
        previous: dict[str, Any] | None,
        current: dict[str, Any],
    ) -> dict[str, Any] | None:
        if previous is None:
            return None

        previous_key = (
            previous.get("status"),
            previous.get("issue_code"),
            previous.get("auth_status"),
        )
        current_key = (
            current.get("status"),
            current.get("issue_code"),
            current.get("auth_status"),
        )
        if previous_key == current_key:
            return None

        if current.get("status") == "online":
            if previous.get("status") == "offline" and not self.status.notify_offline:
                return None
            if not self.status.notify_recovery or previous.get("status") == "online":
                return None
            return {
                "severity": "info",
                "title": "服务器恢复",
                "message": f"{current['server_name']} 已恢复在线，当前延迟 {self._latency_text(current)}。",
                "payload": {
                    "server": current["server_name"],
                    "status": current["status"],
                    "previous_status": previous.get("status"),
                    "latency_ms": current.get("latency_ms"),
                },
            }

        severity = "warning"
        if current.get("status") in {"offline", "error"} or current.get("issue_code") in {"disk_full", "auth_failed"}:
            severity = "error"

        if current.get("status") == "offline" and not self.status.notify_offline:
            return None

        return {
            "severity": severity,
            "title": self._alert_title(current),
            "message": self._alert_message(current),
            "payload": {
                "server": current["server_name"],
                "status": current.get("status"),
                "issue_code": current.get("issue_code"),
                "issue_message": current.get("issue_message"),
                "latency_ms": current.get("latency_ms"),
            },
        }

    async def _deliver_alert(self, alert: dict[str, Any]) -> None:
        try:
            await self.bridge.push_alert(
                alert["title"],
                alert["message"],
                payload=alert.get("payload"),
                level=alert["severity"],
            )
            self.status.last_alert_at = utc_now()
            self.status.sent_alerts += 1
            self.repository.add_activity_log(
                category="alert",
                source="scheduler",
                event=alert["title"],
                request=alert.get("payload"),
                response={"message": alert["message"], "delivered": True},
            )
        except Exception as exc:  # noqa: BLE001
            self.repository.add_activity_log(
                category="alert",
                source="scheduler",
                event=alert["title"],
                level="error",
                request=alert.get("payload"),
                response={"error": str(exc), "message": alert["message"]},
                success=False,
            )

    def _alert_title(self, row: dict[str, Any]) -> str:
        issue_code = row.get("issue_code") or ""
        if issue_code in {"auth_failed", "ssh_error"}:
            return "SSH 告警"
        if issue_code in {"disk_high", "disk_full"}:
            return "磁盘告警"
        if issue_code == "network_degraded":
            return "网络告警"
        if row.get("status") == "offline":
            return "服务器离线"
        return "服务器异常"

    def _alert_message(self, row: dict[str, Any]) -> str:
        name = row["server_name"]
        issue_code = row.get("issue_code") or ""
        if issue_code == "auth_failed":
            return f"{name} 的 SSH 认证失败，请检查用户名、密码或密钥。"
        if issue_code == "timeout":
            return f"{name} 连接超时，目前无法建立 SSH 连接。"
        if issue_code == "unreachable":
            return f"{name} 当前无法连接，SSH 端口不可达。"
        if issue_code == "disk_full":
            return f"{name} 的系统盘已接近满载，当前占用 {row.get('disk_used_percent') or '-'}。"
        if issue_code == "disk_high":
            return f"{name} 的系统盘占用偏高，当前占用 {row.get('disk_used_percent') or '-'}。"
        if issue_code == "network_degraded":
            return f"{name} 的网络状态异常，请检查出口连通性或 DNS。"
        if row.get("status") == "offline":
            return f"{name} 已离线，目前无法连接。"
        if row.get("issue_message"):
            return f"{name} 异常：{row['issue_message']}"
        return f"{name} 状态发生变化，请及时检查。"

    def _latency_text(self, row: dict[str, Any]) -> str:
        latency = row.get("latency_ms")
        if latency is None:
            return "未知"
        return f"{latency} ms"
