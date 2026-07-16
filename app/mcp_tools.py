from __future__ import annotations

from time import perf_counter
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import settings
from .repository import Repository
from .security import CredentialCipher
from .ssh_monitor import SSHMonitorService


cipher = CredentialCipher(settings.resolved_secret)
repository = Repository(cipher)
monitor = SSHMonitorService(repository, cipher)

mcp = FastMCP(
    name="McpEye 智守",
    instructions=(
        "Use the McpEye 智守 tools to inspect named Linux servers that were configured in the web admin console. "
        "When the user asks for CPU, memory, disk, network, processor, or uptime information, "
        "call the relevant monitoring tool with the server name."
    ),
    stateless_http=True,
    streamable_http_path="/",
)


@mcp.tool()
def list_servers() -> list[dict[str, Any]]:
    """List configured servers and their connection metadata."""
    started_at = perf_counter()
    try:
        result = repository.list_servers()
        _record_tool_call("list_servers", {}, result, started_at)
        return result
    except Exception as exc:
        _record_tool_error("list_servers", {}, exc, started_at)
        raise


@mcp.tool()
async def get_server_snapshot(server_name: str) -> dict[str, Any]:
    """Get a full live monitoring snapshot for the named server."""
    request = {"server_name": server_name}
    started_at = perf_counter()
    try:
        result = await monitor.get_snapshot_by_name(server_name)
        _record_tool_call("get_server_snapshot", request, result, started_at)
        return result
    except Exception as exc:
        _record_tool_error("get_server_snapshot", request, exc, started_at)
        raise


@mcp.tool()
async def get_server_metric(server_name: str, metric: str) -> dict[str, Any]:
    """Get one metric from the named server, such as cpu, memory, disk, network, processor, hostname, or os."""
    request = {"server_name": server_name, "metric": metric}
    started_at = perf_counter()
    try:
        result = await monitor.get_metric_by_name(server_name, metric)
        _record_tool_call("get_server_metric", request, result, started_at)
        return result
    except Exception as exc:
        _record_tool_error("get_server_metric", request, exc, started_at)
        raise


@mcp.tool()
async def run_server_custom_check(server_name: str, check_name: str) -> dict[str, Any]:
    """Run a saved custom SSH command on the named server."""
    request = {"server_name": server_name, "check_name": check_name}
    started_at = perf_counter()
    try:
        result = await monitor.run_custom_check_by_name(server_name, check_name)
        _record_tool_call("run_server_custom_check", request, result, started_at)
        return result
    except Exception as exc:
        _record_tool_error("run_server_custom_check", request, exc, started_at)
        raise


def _duration_ms(started_at: float) -> int:
    return round((perf_counter() - started_at) * 1000)


def _record_tool_call(
    tool_name: str,
    request: dict[str, Any],
    response: Any,
    started_at: float,
) -> None:
    repository.add_activity_log(
        category="mcp_tool",
        source="mcp",
        event=tool_name,
        direction="request_response",
        request=request,
        response=response,
        duration_ms=_duration_ms(started_at),
    )


def _record_tool_error(
    tool_name: str,
    request: dict[str, Any],
    error: Exception,
    started_at: float,
) -> None:
    repository.add_activity_log(
        category="mcp_tool",
        source="mcp",
        event=tool_name,
        level="error",
        direction="request_response",
        request=request,
        response={"error": str(error)},
        duration_ms=_duration_ms(started_at),
        success=False,
    )
