from __future__ import annotations

import asyncio
import io
import json
import re
import socket
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

import paramiko

from .repository import Repository
from .security import CredentialCipher


class SSHMonitorService:
    def __init__(self, repository: Repository, cipher: CredentialCipher) -> None:
        self.repository = repository
        self.cipher = cipher

    async def get_snapshot_by_id(self, server_id: int) -> dict[str, Any]:
        server = self.repository.get_server(server_id)
        if not server:
            raise ValueError("Server not found")
        return await asyncio.to_thread(self._build_snapshot, server)

    async def get_snapshot_by_name(self, server_name: str) -> dict[str, Any]:
        server = self.repository.get_server_by_name(server_name)
        if not server:
            raise ValueError(f"Server '{server_name}' not found")
        return await asyncio.to_thread(self._build_snapshot, server)

    async def get_metric_by_name(self, server_name: str, metric: str) -> dict[str, Any]:
        snapshot = await self.get_snapshot_by_name(server_name)
        normalized = metric.strip().lower()
        if normalized in snapshot:
            return {"metric": normalized, "value": snapshot[normalized]}
        aliases = {
            "cpu": snapshot["cpu"],
            "memory": snapshot["memory"],
            "disk": snapshot["disk"],
            "network": snapshot["network"],
            "processor": snapshot["processor_model"],
            "hostname": snapshot["hostname"],
            "os": snapshot["os_info"],
        }
        if normalized in aliases:
            return {"metric": normalized, "value": aliases[normalized]}
        raise ValueError(f"Unsupported metric '{metric}'")

    async def get_status_board(self) -> list[dict[str, Any]]:
        servers = [self.repository.get_server(item["id"]) for item in self.repository.list_servers()]
        valid_servers = [server for server in servers if server]
        semaphore = asyncio.Semaphore(5)

        async def inspect(server: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                return await asyncio.to_thread(self._build_health_summary, server)

        return await asyncio.gather(*(inspect(server) for server in valid_servers))

    async def run_custom_check_by_name(self, server_name: str, check_name: str) -> dict[str, Any]:
        server = self.repository.get_server_by_name(server_name)
        if not server:
            raise ValueError(f"Server '{server_name}' not found")

        commands = self.repository.find_monitor_commands_by_name_for_server(server["id"], check_name)
        if len(commands) > 1:
            raise ValueError(
                f"Multiple commands named '{check_name}' apply to server '{server_name}'. "
                "Please rename one of them in the web console."
            )
        if len(commands) == 1:
            return await asyncio.to_thread(self._run_custom_check, server, commands[0])

        legacy_check = self.repository.get_custom_check_by_name(server["id"], check_name)
        if not legacy_check:
            raise ValueError(f"Custom check '{check_name}' not found")
        return await asyncio.to_thread(self._run_custom_check, server, legacy_check)

    async def run_custom_check_by_id(self, server_id: int, check_id: int) -> dict[str, Any]:
        server = self.repository.get_server(server_id)
        if not server:
            raise ValueError("Server not found")
        check = self.repository.get_custom_check(server_id, check_id)
        if not check:
            raise ValueError("Custom check not found")
        return await asyncio.to_thread(self._run_custom_check, server, check)

    async def run_monitor_command_by_id(self, server_id: int, command_id: int) -> dict[str, Any]:
        server = self.repository.get_server(server_id)
        if not server:
            raise ValueError("Server not found")
        command = self.repository.get_monitor_command_for_server(command_id, server_id)
        if not command:
            raise ValueError("Command not found or not available for this server")
        return await asyncio.to_thread(self._run_custom_check, server, command)

    def _build_snapshot(self, server: dict[str, Any]) -> dict[str, Any]:
        with self._connect(server) as client:
            snapshot = {
                "server_name": server["name"],
                "host": server["host"],
                "port": server["port"],
                "username": server["username"],
                "status": "online",
                "hostname": self._run_command(client, "hostname"),
                "uptime": self._run_command(client, "uptime -p 2>/dev/null || uptime"),
                "os_info": self._run_command(
                    client,
                    "sh -lc \"uname -srvmo 2>/dev/null || cat /etc/os-release | head -n 4\"",
                ),
                "processor_model": self._get_processor_model(client),
                "cpu": self._get_cpu_stats(client),
                "memory": self._get_memory_stats(client),
                "disk": self._get_disk_stats(client),
                "network": self._get_network_stats(client),
            }
        return snapshot

    def _build_health_summary(self, server: dict[str, Any]) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            client, latency_ms = self._connect_with_latency(server)
        except Exception as exc:  # noqa: BLE001
            issue_code, issue_message, auth_status, status = self._classify_connection_error(exc)
            return {
                "server_id": server["id"],
                "server_name": server["name"],
                "host": server["host"],
                "port": server["port"],
                "username": server["username"],
                "checked_at": checked_at,
                "latency_ms": None,
                "status": status,
                "auth_status": auth_status,
                "disk_used_percent": "",
                "disk_available": "",
                "network_status": "unknown",
                "issue_code": issue_code,
                "issue_message": issue_message,
            }

        with client:
            disk = self._get_disk_stats(client)
            network = self._get_network_health(client)

        status = "online"
        issue_code = ""
        issue_message = "Server is healthy"
        disk_percent = self._parse_percent_value(disk.get("used_percent"))

        if disk_percent >= 95:
            status = "error"
            issue_code = "disk_full"
            issue_message = f"Root filesystem usage is critical: {disk.get('used_percent') or '-'}"
        elif disk_percent >= 85:
            status = "warning"
            issue_code = "disk_high"
            issue_message = f"Root filesystem usage is high: {disk.get('used_percent') or '-'}"
        elif network["status"] == "degraded":
            status = "warning"
            issue_code = "network_degraded"
            issue_message = "Network reachability is degraded"

        return {
            "server_id": server["id"],
            "server_name": server["name"],
            "host": server["host"],
            "port": server["port"],
            "username": server["username"],
            "checked_at": checked_at,
            "latency_ms": latency_ms,
            "status": status,
            "auth_status": "ok",
            "disk_used_percent": disk.get("used_percent", ""),
            "disk_available": disk.get("available", ""),
            "network_status": network["status"],
            "issue_code": issue_code,
            "issue_message": issue_message,
        }

    def _run_custom_check(self, server: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
        with self._connect(server) as client:
            stdout, stderr, exit_status = self._run_command_with_status(client, check["command"])
        return {
            "server_name": server["name"],
            "check_name": check["name"],
            "description": check.get("description", ""),
            "command": check["command"],
            "exit_status": exit_status,
            "stdout": stdout,
            "stderr": stderr,
        }

    def _connect(self, server: dict[str, Any]) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_args = self._build_connect_args(server)
        try:
            client.connect(**connect_args)
        except (paramiko.SSHException, socket.error) as exc:
            client.close()
            raise RuntimeError(f"SSH connection failed for {server['name']}: {exc}") from exc
        return client

    def _connect_with_latency(self, server: dict[str, Any]) -> tuple[paramiko.SSHClient, int]:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_args = self._build_connect_args(server)
        started_at = perf_counter()
        try:
            client.connect(**connect_args)
            latency_ms = round((perf_counter() - started_at) * 1000)
            return client, latency_ms
        except Exception:
            client.close()
            raise

    def _build_connect_args(self, server: dict[str, Any]) -> dict[str, Any]:
        connect_args: dict[str, Any] = {
            "hostname": server["host"],
            "port": server["port"],
            "username": server["username"],
            "timeout": 10,
            "auth_timeout": 10,
            "banner_timeout": 10,
            "look_for_keys": False,
            "allow_agent": False,
        }
        if server["auth_type"] == "password":
            connect_args["password"] = self.cipher.decrypt(server["password_cipher"])
        else:
            private_key = self.cipher.decrypt(server["private_key_cipher"])
            passphrase = self.cipher.decrypt(server["private_key_passphrase_cipher"])
            connect_args["pkey"] = self._load_private_key(private_key or "", passphrase)
        return connect_args

    def _load_private_key(self, key_material: str, passphrase: str | None) -> paramiko.PKey:
        key_types = (
            paramiko.RSAKey,
            paramiko.Ed25519Key,
            paramiko.ECDSAKey,
            paramiko.DSSKey,
        )
        last_error: Exception | None = None
        for key_type in key_types:
            buffer = io.StringIO(key_material)
            try:
                return key_type.from_private_key(buffer, password=passphrase)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise RuntimeError(f"Unsupported private key format: {last_error}") from last_error

    def _run_command(self, client: paramiko.SSHClient, command: str) -> str:
        stdout, _, _ = self._run_command_with_status(client, command)
        return stdout

    def _run_command_with_status(
        self,
        client: paramiko.SSHClient,
        command: str,
    ) -> tuple[str, str, int]:
        stdin = stdout = stderr = None
        try:
            stdin, stdout, stderr = client.exec_command(command, timeout=15)
            if stdin:
                stdin.close()
            out = stdout.read().decode("utf-8", errors="replace").strip()
            err = stderr.read().decode("utf-8", errors="replace").strip()
            status = stdout.channel.recv_exit_status()
            return out, err, status
        except socket.timeout:
            return "", "command timed out", 124
        except paramiko.SSHException as exc:
            return "", str(exc), 255
        finally:
            for stream in (stdin, stdout, stderr):
                if stream:
                    stream.close()

    def _get_cpu_stats(self, client: paramiko.SSHClient) -> dict[str, Any]:
        proc_stat = self._run_command(
            client,
            "sh -lc \"head -n 1 /proc/stat 2>/dev/null; sleep 0.5; head -n 1 /proc/stat 2>/dev/null\"",
        )
        load_averages = self._run_command(
            client,
            "cat /proc/loadavg 2>/dev/null || uptime",
        )
        return {
            "usage_percent": self._parse_cpu_usage(proc_stat),
            "load_average": self._parse_load_average(load_averages),
        }

    def _get_processor_model(self, client: paramiko.SSHClient) -> str:
        lscpu = self._run_command(client, "LC_ALL=C lscpu 2>/dev/null")
        for line in lscpu.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key.strip().lower() in {"model name", "vendor id", "architecture"} and value.strip():
                return value.strip()

        cpuinfo = self._run_command(client, "cat /proc/cpuinfo 2>/dev/null")
        for line in cpuinfo.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key.strip().lower() in {"model name", "hardware", "processor"} and value.strip():
                return value.strip()

        return self._run_command(client, "uname -m 2>/dev/null")

    def _get_memory_stats(self, client: paramiko.SSHClient) -> dict[str, Any]:
        output = self._run_command(
            client,
            "free -m | awk 'NR==2 {printf \"{\\\"total_mb\\\":%s,\\\"used_mb\\\":%s,\\\"free_mb\\\":%s,"
            "\\\"used_percent\\\":%.2f}\", $2, $3, $4, ($3/$2)*100}'",
        )
        return self._safe_json_loads(
            output,
            {"total_mb": 0, "used_mb": 0, "free_mb": 0, "used_percent": 0.0},
        )

    def _get_disk_stats(self, client: paramiko.SSHClient) -> dict[str, Any]:
        output = self._run_command(
            client,
            "df -hP / | awk 'NR==2 {printf \"{\\\"filesystem\\\":\\\"%s\\\",\\\"size\\\":\\\"%s\\\","
            "\\\"used\\\":\\\"%s\\\",\\\"available\\\":\\\"%s\\\",\\\"used_percent\\\":\\\"%s\\\","
            "\\\"mount\\\":\\\"%s\\\"}\", $1, $2, $3, $4, $5, $6}'",
        )
        return self._safe_json_loads(
            output,
            {
                "filesystem": "",
                "size": "",
                "used": "",
                "available": "",
                "used_percent": "",
                "mount": "/",
            },
        )

    def _get_network_stats(self, client: paramiko.SSHClient) -> dict[str, Any]:
        route = self._run_command(
            client,
            "sh -lc \"ip route get 8.8.8.8 2>/dev/null | head -n 1 || ip route 2>/dev/null | head -n 1\"",
        )
        interfaces = self._run_command(
            client,
            "sh -lc \"ip -brief addr 2>/dev/null || ifconfig 2>/dev/null\"",
        )
        proc_net_dev = self._run_command(client, "cat /proc/net/dev 2>/dev/null")
        external_ping = self._run_command(
            client,
            "sh -lc \"ping -c 1 -W 1 8.8.8.8 >/dev/null 2>&1 && echo reachable || echo degraded\"",
        )
        dns_check = self._run_command(
            client,
            "sh -lc \"getent hosts openai.com >/dev/null 2>&1 && echo ok || echo unavailable\"",
        )
        return {
            "status": external_ping or "unknown",
            "default_route": route,
            "dns": dns_check or "unknown",
            "interfaces": [line for line in interfaces.splitlines() if line.strip()],
            "traffic": self._parse_proc_net_dev(proc_net_dev),
        }

    def _get_network_health(self, client: paramiko.SSHClient) -> dict[str, str]:
        external_ping = self._run_command(
            client,
            "sh -lc \"ping -c 1 -W 1 8.8.8.8 >/dev/null 2>&1 && echo reachable || echo degraded\"",
        )
        dns_check = self._run_command(
            client,
            "sh -lc \"getent hosts openai.com >/dev/null 2>&1 && echo ok || echo unavailable\"",
        )
        status = external_ping or "unknown"
        if status == "reachable" and dns_check == "unavailable":
            status = "degraded"
        return {"status": status, "dns": dns_check or "unknown"}

    def _parse_proc_net_dev(self, value: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for line in value.splitlines():
            if ":" not in line:
                continue
            name, stats = line.split(":", 1)
            fields = stats.split()
            if len(fields) < 16:
                continue
            items.append(
                {
                    "interface": name.strip(),
                    "rx_bytes": int(fields[0]),
                    "rx_packets": int(fields[1]),
                    "tx_bytes": int(fields[8]),
                    "tx_packets": int(fields[9]),
                }
            )
        return items

    def _safe_json_loads(self, value: str, fallback: dict[str, Any]) -> dict[str, Any]:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback

    def _parse_float(self, value: str) -> float:
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return 0.0

    def _parse_cpu_usage(self, value: str) -> float:
        rows = [line.split() for line in value.splitlines() if line.startswith("cpu ")]
        if len(rows) < 2:
            return 0.0
        first = self._parse_cpu_row(rows[0])
        second = self._parse_cpu_row(rows[1])
        if not first or not second:
            return 0.0
        idle_delta = second["idle"] - first["idle"]
        total_delta = second["total"] - first["total"]
        if total_delta <= 0:
            return 0.0
        return round(max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100)), 2)

    def _parse_cpu_row(self, fields: list[str]) -> dict[str, int] | None:
        try:
            values = [int(item) for item in fields[1:8]]
        except ValueError:
            return None
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return {"idle": idle, "total": sum(values)}

    def _parse_load_average(self, value: str) -> list[str]:
        if not value:
            return []
        proc_match = re.match(r"^([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)", value)
        if proc_match:
            return list(proc_match.groups())
        if "load average:" in value:
            tail = value.split("load average:", 1)[1]
            return [part for part in re.split(r"[,\s]+", tail.strip()) if part][:3]
        return []

    def _parse_percent_value(self, value: str | None) -> float:
        if not value:
            return 0.0
        cleaned = str(value).strip().rstrip("%")
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    def _classify_connection_error(self, error: Exception) -> tuple[str, str, str, str]:
        if isinstance(error, paramiko.AuthenticationException):
            return ("auth_failed", "SSH authentication failed", "auth_failed", "error")
        if isinstance(error, socket.timeout):
            return ("timeout", "Connection timed out", "unknown", "offline")
        if isinstance(error, paramiko.NoValidConnectionsError):
            return ("unreachable", "Cannot reach the SSH port", "unknown", "offline")
        if isinstance(error, OSError):
            return ("unreachable", f"Network connection failed: {error}", "unknown", "offline")
        if isinstance(error, paramiko.SSHException):
            return ("ssh_error", f"SSH handshake failed: {error}", "unknown", "error")
        return ("unknown", f"Inspection failed: {error}", "unknown", "error")
