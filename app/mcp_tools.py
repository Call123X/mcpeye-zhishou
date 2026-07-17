from __future__ import annotations

import re
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

COMMAND_GROUPS: list[dict[str, Any]] = [
    {
        "name": "基础巡检",
        "aliases": {"basic", "基础", "overview", "总览", "常规巡检"},
        "commands": ["识别 Linux 系统", "服务器运行状态总览", "网络与监听服务检查"],
    },
    {
        "name": "故障排查",
        "aliases": {"troubleshoot", "diagnose", "故障", "排障", "诊断"},
        "commands": ["服务器运行状态总览", "网络与监听服务检查", "软件补丁与安全更新检查"],
    },
    {
        "name": "安全基线",
        "aliases": {"security", "baseline", "安全", "基线", "等保"},
        "commands": ["SSH 与账户安全基线", "防火墙与暴露面检查", "等保基线快速检查", "计划任务与持久化检查"],
    },
    {
        "name": "网络检查",
        "aliases": {"network", "net", "网络"},
        "commands": ["网络与监听服务检查"],
    },
    {
        "name": "补丁检查",
        "aliases": {"patch", "update", "补丁", "更新"},
        "commands": ["软件补丁与安全更新检查"],
    },
    {
        "name": "全部内置巡检",
        "aliases": {"all", "全部", "full", "完整"},
        "commands": [
            "识别 Linux 系统",
            "服务器运行状态总览",
            "网络与监听服务检查",
            "SSH 与账户安全基线",
            "防火墙与暴露面检查",
            "等保基线快速检查",
            "软件补丁与安全更新检查",
            "计划任务与持久化检查",
        ],
    },
]

mcp = FastMCP(
    name="McpEye 智守",
    instructions=(
        "Use the McpEye 智守 tools to inspect named Linux servers configured in the web admin console. "
        "Always call list_servers first when you need to know which servers are available. "
        "Use diagnose_server for one-machine health diagnosis, compare_servers for multi-machine differences, "
        "get_recent_alerts for alert review, get_server_history for recent status history, "
        "run_command_group for grouped inspection commands, and explain_check_result to summarize command output. "
        "When the user asks for CPU, memory, disk, network, processor, hostname, os, or uptime information, "
        "call the relevant monitoring tool with the exact server name returned by list_servers."
    ),
    stateless_http=True,
    streamable_http_path="/",
)


@mcp.tool()
def list_servers() -> dict[str, Any]:
    """List configured servers and their connection metadata."""
    started_at = perf_counter()
    try:
        servers = repository.list_servers()
        result = {
            "count": len(servers),
            "servers": servers,
            "server_names": [server["name"] for server in servers],
        }
        _record_tool_call("list_servers", {}, result, started_at)
        return result
    except Exception as exc:
        _record_tool_error("list_servers", {}, exc, started_at)
        raise


@mcp.tool()
def list_monitor_commands(server_name: str = "") -> dict[str, Any]:
    """List saved inspection commands, optionally filtered to one server name."""
    request = {"server_name": server_name}
    started_at = perf_counter()
    try:
        commands = repository.list_monitor_commands()
        normalized_name = server_name.strip().lower()
        if normalized_name:
            commands = [
                command
                for command in commands
                if any(server["name"].strip().lower() == normalized_name for server in command["applicable_servers"])
            ]
        result = {
            "count": len(commands),
            "commands": [_public_command(command) for command in commands],
            "command_names": [command["name"] for command in commands],
            "groups": _public_command_groups(),
        }
        _record_tool_call("list_monitor_commands", request, result, started_at)
        return result
    except Exception as exc:
        _record_tool_error("list_monitor_commands", request, exc, started_at)
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


@mcp.tool()
async def diagnose_server(server_name: str) -> dict[str, Any]:
    """Run a live diagnosis for one named server and return findings, suggestions, history, and recent alerts."""
    request = {"server_name": server_name}
    started_at = perf_counter()
    try:
        snapshot = await monitor.get_snapshot_by_name(server_name)
        history = repository.list_server_history(server_name=server_name, hours=24, limit=50)
        alerts = repository.list_recent_alerts(server_name=server_name, limit=10)
        analysis = _analyze_snapshot(snapshot, history, alerts)
        result = {
            "server_name": server_name,
            **analysis,
            "history_summary": _summarize_history(history),
            "recent_alerts": alerts,
            "snapshot": snapshot,
        }
        _record_tool_call("diagnose_server", request, result, started_at)
        return result
    except Exception as exc:
        _record_tool_error("diagnose_server", request, exc, started_at)
        raise


@mcp.tool()
async def compare_servers(server_names: str) -> dict[str, Any]:
    """Compare two or more named servers. Pass names separated by comma, Chinese comma, semicolon, or newline."""
    request = {"server_names": server_names}
    started_at = perf_counter()
    try:
        names = _split_names(server_names)
        if len(names) < 2:
            raise ValueError("Please provide at least two server names separated by commas")
        snapshots: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for name in names:
            try:
                snapshots.append(await monitor.get_snapshot_by_name(name))
            except Exception as exc:  # noqa: BLE001
                errors.append({"server_name": name, "error": str(exc)})
        rows = [_snapshot_compare_row(snapshot) for snapshot in snapshots]
        result = {
            "count": len(rows),
            "requested_server_names": names,
            "servers": rows,
            "differences": _compare_rows(rows),
            "errors": errors,
            "summary": _compare_summary(rows, errors),
        }
        _record_tool_call("compare_servers", request, result, started_at)
        return result
    except Exception as exc:
        _record_tool_error("compare_servers", request, exc, started_at)
        raise


@mcp.tool()
def get_recent_alerts(limit: int = 20, server_name: str = "", level: str = "") -> dict[str, Any]:
    """List recent active alert records, optionally filtered by server name and log level."""
    request = {"limit": limit, "server_name": server_name, "level": level}
    started_at = perf_counter()
    try:
        alerts = repository.list_recent_alerts(limit=limit, server_name=server_name, level=level)
        result = {
            "count": len(alerts),
            "alerts": alerts,
            "summary": _alerts_summary(alerts),
        }
        _record_tool_call("get_recent_alerts", request, result, started_at)
        return result
    except Exception as exc:
        _record_tool_error("get_recent_alerts", request, exc, started_at)
        raise


@mcp.tool()
def get_server_history(server_name: str, hours: int = 24, limit: int = 100) -> dict[str, Any]:
    """Get recent status history for one server. History is kept for up to 30 days."""
    request = {"server_name": server_name, "hours": hours, "limit": limit}
    started_at = perf_counter()
    try:
        history = repository.list_server_history(server_name=server_name, hours=hours, limit=limit)
        result = {
            "server_name": server_name,
            "hours": max(1, min(int(hours or 24), 24 * 30)),
            "count": len(history),
            "summary": _summarize_history(history),
            "items": history,
        }
        _record_tool_call("get_server_history", request, result, started_at)
        return result
    except Exception as exc:
        _record_tool_error("get_server_history", request, exc, started_at)
        raise


@mcp.tool()
async def run_command_group(
    server_name: str,
    group_name: str = "基础巡检",
    command_names: str = "",
    max_output_chars: int = 4000,
) -> dict[str, Any]:
    """Run a named command group or comma-separated command names on one server."""
    request = {
        "server_name": server_name,
        "group_name": group_name,
        "command_names": command_names,
        "max_output_chars": max_output_chars,
    }
    started_at = perf_counter()
    try:
        server = repository.get_server_by_name(server_name)
        if not server:
            raise ValueError(f"Server '{server_name}' not found")
        selection = _select_commands_for_group(server_name, group_name, command_names)
        results: list[dict[str, Any]] = []
        for command in selection["commands"]:
            command_started = perf_counter()
            try:
                run_result = await monitor.run_monitor_command_by_id(server["id"], command["id"])
                explanation = _explain_check_payload(
                    command["name"],
                    run_result.get("stdout", ""),
                    run_result.get("stderr", ""),
                    int(run_result.get("exit_status") or 0),
                )
                results.append(
                    {
                        "command_id": command["id"],
                        "command_name": command["name"],
                        "description": command.get("description", ""),
                        "exit_status": run_result.get("exit_status"),
                        "duration_ms": _duration_ms(command_started),
                        "stdout": _truncate_text(run_result.get("stdout", ""), max_output_chars),
                        "stderr": _truncate_text(run_result.get("stderr", ""), max_output_chars),
                        "output_truncated": _is_truncated(run_result.get("stdout", ""), max_output_chars)
                        or _is_truncated(run_result.get("stderr", ""), max_output_chars),
                        "explanation": explanation,
                        "success": int(run_result.get("exit_status") or 0) == 0,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "command_id": command["id"],
                        "command_name": command["name"],
                        "duration_ms": _duration_ms(command_started),
                        "error": str(exc),
                        "success": False,
                    }
                )
        result = {
            "server_name": server_name,
            "group_name": selection["group_name"],
            "available_groups": _public_command_groups(),
            "missing_commands": selection["missing_commands"],
            "count": len(results),
            "status": _group_status(results),
            "summary": _group_summary(results, selection["missing_commands"]),
            "results": results,
        }
        _record_tool_call("run_command_group", request, result, started_at)
        return result
    except Exception as exc:
        _record_tool_error("run_command_group", request, exc, started_at)
        raise


@mcp.tool()
def explain_check_result(
    check_name: str = "",
    command_output: str = "",
    stderr: str = "",
    exit_status: int = 0,
) -> dict[str, Any]:
    """Explain a saved command result with rule-based findings and suggestions."""
    request = {
        "check_name": check_name,
        "command_output_length": len(command_output or ""),
        "stderr_length": len(stderr or ""),
        "exit_status": exit_status,
    }
    started_at = perf_counter()
    try:
        result = _explain_check_payload(check_name, command_output, stderr, exit_status)
        _record_tool_call("explain_check_result", request, result, started_at)
        return result
    except Exception as exc:
        _record_tool_error("explain_check_result", request, exc, started_at)
        raise


def _public_command(command: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": command["id"],
        "name": command["name"],
        "description": command.get("description", ""),
        "scope_all_servers": command.get("scope_all_servers", False),
        "is_builtin": command.get("is_builtin", False),
        "server_names": command.get("server_names", []),
        "tags": command.get("tags", []),
        "applicable_server_names": [server["name"] for server in command.get("applicable_servers", [])],
    }


def _public_command_groups() -> list[dict[str, Any]]:
    return [{"name": group["name"], "commands": group["commands"]} for group in COMMAND_GROUPS]


def _split_names(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,，;；\n]+", value or "") if part.strip()]


def _parse_percent(value: Any) -> float:
    if value is None:
        return 0.0
    cleaned = str(value).strip().rstrip("%")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _severity_score(value: str) -> int:
    return {"ok": 0, "info": 0, "warning": 1, "error": 2}.get(value, 0)


def _status_from_findings(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "ok"
    worst = max(findings, key=lambda item: _severity_score(item.get("severity", "ok")))
    return worst.get("severity", "ok")


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        text = value.strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(text)
    return items


def _analyze_snapshot(
    snapshot: dict[str, Any],
    history: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    suggestions: list[str] = []

    cpu_percent = _parse_percent(snapshot.get("cpu", {}).get("usage_percent"))
    if cpu_percent >= 90:
        findings.append({"severity": "error", "title": "CPU 使用率过高", "detail": f"当前 CPU 使用率 {cpu_percent:.1f}%"})
        suggestions.append("查看 top-cpu 进程并确认是否存在异常计算任务。")
    elif cpu_percent >= 75:
        findings.append({"severity": "warning", "title": "CPU 使用率偏高", "detail": f"当前 CPU 使用率 {cpu_percent:.1f}%"})
        suggestions.append("持续观察 CPU 趋势，必要时限制高占用进程。")

    memory_percent = _parse_percent(snapshot.get("memory", {}).get("used_percent"))
    if memory_percent >= 90:
        findings.append({"severity": "error", "title": "内存使用率过高", "detail": f"当前内存使用率 {memory_percent:.1f}%"})
        suggestions.append("检查内存占用最高的进程，评估是否需要重启服务或扩容。")
    elif memory_percent >= 80:
        findings.append({"severity": "warning", "title": "内存使用率偏高", "detail": f"当前内存使用率 {memory_percent:.1f}%"})
        suggestions.append("关注缓存、常驻进程和近期发布变更。")

    disk = snapshot.get("disk", {})
    disk_percent = _parse_percent(disk.get("used_percent"))
    if disk_percent >= 95:
        findings.append({"severity": "error", "title": "系统盘接近满载", "detail": f"根分区已使用 {disk.get('used_percent') or '-'}"})
        suggestions.append("优先清理日志、缓存、临时文件，或扩容根分区。")
    elif disk_percent >= 85:
        findings.append({"severity": "warning", "title": "系统盘占用偏高", "detail": f"根分区已使用 {disk.get('used_percent') or '-'}"})
        suggestions.append("排查大文件和日志增长，避免磁盘继续上涨。")

    network = snapshot.get("network", {})
    if network.get("status") not in {"reachable", "ok", ""}:
        findings.append({"severity": "warning", "title": "外网连通性异常", "detail": f"网络状态：{network.get('status') or 'unknown'}"})
        suggestions.append("检查默认路由、出口连通性、安全组或防火墙策略。")
    if network.get("dns") == "unavailable":
        findings.append({"severity": "warning", "title": "DNS 解析异常", "detail": "DNS 检查不可用"})
        suggestions.append("检查 /etc/resolv.conf、DNS 服务和网络出口。")

    history_summary = _summarize_history(history)
    if history_summary.get("offline_count", 0):
        findings.append({"severity": "warning", "title": "近期有离线记录", "detail": f"近 24 小时离线 {history_summary['offline_count']} 次"})
        suggestions.append("结合最近告警和系统日志确认是否是网络抖动或 SSH 服务异常。")
    if alerts:
        findings.append({"severity": "info", "title": "近期存在告警记录", "detail": f"最近匹配到 {len(alerts)} 条告警"})

    status = _status_from_findings(findings)
    summary = "未发现明显异常" if status == "ok" else "；".join(item["title"] for item in findings[:3])
    return {
        "status": status,
        "summary": summary,
        "findings": findings,
        "suggestions": _unique(suggestions),
    }


def _summarize_history(history: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    issue_counts: dict[str, int] = {}
    latencies: list[int] = []
    for item in history:
        status = str(item.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        issue = str(item.get("issue_code") or "")
        if issue:
            issue_counts[issue] = issue_counts.get(issue, 0) + 1
        latency = item.get("latency_ms")
        if isinstance(latency, int):
            latencies.append(latency)
    latest = history[0] if history else None
    return {
        "count": len(history),
        "latest_status": latest.get("status") if latest else "unknown",
        "latest_checked_at": latest.get("created_at") if latest else None,
        "status_counts": status_counts,
        "issue_counts": issue_counts,
        "offline_count": status_counts.get("offline", 0),
        "warning_count": status_counts.get("warning", 0),
        "error_count": status_counts.get("error", 0),
        "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "max_latency_ms": max(latencies) if latencies else None,
    }


def _snapshot_compare_row(snapshot: dict[str, Any]) -> dict[str, Any]:
    memory = snapshot.get("memory", {})
    disk = snapshot.get("disk", {})
    network = snapshot.get("network", {})
    return {
        "server_name": snapshot.get("server_name"),
        "host": snapshot.get("host"),
        "username": snapshot.get("username"),
        "hostname": snapshot.get("hostname"),
        "os_info": _first_line(snapshot.get("os_info")),
        "processor_model": snapshot.get("processor_model"),
        "cpu_usage_percent": snapshot.get("cpu", {}).get("usage_percent"),
        "load_average": snapshot.get("cpu", {}).get("load_average", []),
        "memory_total_mb": memory.get("total_mb"),
        "memory_used_percent": memory.get("used_percent"),
        "disk_size": disk.get("size"),
        "disk_used_percent": disk.get("used_percent"),
        "disk_available": disk.get("available"),
        "network_status": network.get("status"),
        "dns": network.get("dns"),
        "uptime": snapshot.get("uptime"),
    }


def _compare_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    differences: list[dict[str, str]] = []
    for field, label in (
        ("os_info", "系统版本"),
        ("processor_model", "处理器型号"),
        ("memory_total_mb", "内存容量"),
        ("disk_size", "根分区容量"),
        ("network_status", "网络状态"),
        ("dns", "DNS 状态"),
    ):
        values = {str(row.get(field) or "") for row in rows}
        if len(values) > 1:
            differences.append({"field": field, "label": label, "detail": "不同服务器该项不一致"})
    if rows:
        busiest = max(rows, key=lambda row: _parse_percent(row.get("memory_used_percent")))
        fullest = max(rows, key=lambda row: _parse_percent(row.get("disk_used_percent")))
        differences.append({"field": "memory_used_percent", "label": "内存压力最高", "detail": str(busiest.get("server_name"))})
        differences.append({"field": "disk_used_percent", "label": "磁盘占用最高", "detail": str(fullest.get("server_name"))})
    return differences


def _compare_summary(rows: list[dict[str, Any]], errors: list[dict[str, str]]) -> str:
    if not rows:
        return "没有成功获取任何服务器快照"
    parts = [f"成功比较 {len(rows)} 台服务器"]
    if errors:
        parts.append(f"{len(errors)} 台获取失败")
    abnormal = [row["server_name"] for row in rows if row.get("network_status") not in {"reachable", "ok", ""}]
    if abnormal:
        parts.append("网络异常：" + "、".join(str(name) for name in abnormal))
    return "；".join(parts)


def _first_line(value: Any) -> str:
    return str(value or "").splitlines()[0] if value else ""


def _alerts_summary(alerts: list[dict[str, Any]]) -> str:
    if not alerts:
        return "最近没有匹配的告警记录"
    failed = sum(1 for item in alerts if not item.get("success", True))
    servers = _unique([str(item.get("server") or "") for item in alerts])
    server_text = "，涉及 " + "、".join(servers) if servers else ""
    return f"最近 {len(alerts)} 条告警{server_text}" + (f"，其中 {failed} 条发送失败" if failed else "")


def _select_commands_for_group(server_name: str, group_name: str, command_names: str) -> dict[str, Any]:
    normalized_server = server_name.strip().lower()
    candidates = [
        command
        for command in repository.list_monitor_commands()
        if any(server["name"].strip().lower() == normalized_server for server in command.get("applicable_servers", []))
    ]
    missing: list[str] = []
    selected: list[dict[str, Any]] = []
    requested_names = _split_names(command_names)
    group_used = "自定义命令"
    if requested_names:
        for name in requested_names:
            command = _find_command(candidates, name)
            if command:
                selected.append(command)
            else:
                missing.append(name)
    else:
        group = _find_group(group_name) or COMMAND_GROUPS[0]
        group_used = group["name"]
        for name in group["commands"]:
            command = _find_command(candidates, name)
            if command:
                selected.append(command)
            else:
                missing.append(name)
        if not selected and group_name:
            command = _find_command(candidates, group_name)
            if command:
                selected.append(command)
                group_used = "单条命令"
    deduped: dict[int, dict[str, Any]] = {}
    for command in selected:
        deduped[int(command["id"])] = command
    return {"group_name": group_used, "commands": list(deduped.values()), "missing_commands": missing}


def _find_group(name: str) -> dict[str, Any] | None:
    normalized = name.strip().lower()
    if not normalized:
        return None
    for group in COMMAND_GROUPS:
        if normalized == group["name"].lower() or normalized in group["aliases"]:
            return group
    for group in COMMAND_GROUPS:
        if normalized in group["name"].lower():
            return group
    return None


def _find_command(commands: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    normalized = name.strip().lower()
    if not normalized:
        return None
    exact = [command for command in commands if command["name"].strip().lower() == normalized]
    if exact:
        return exact[0]
    fuzzy = [command for command in commands if normalized in command["name"].strip().lower() or command["name"].strip().lower() in normalized]
    if len(fuzzy) == 1:
        return fuzzy[0]
    return None


def _group_status(results: list[dict[str, Any]]) -> str:
    if not results:
        return "error"
    if any(not result.get("success") for result in results):
        return "warning"
    if any(result.get("explanation", {}).get("severity") in {"warning", "error"} for result in results):
        return "warning"
    return "ok"


def _group_summary(results: list[dict[str, Any]], missing: list[str]) -> str:
    if not results:
        return "没有找到可执行的命令"
    failed = sum(1 for result in results if not result.get("success"))
    warned = sum(1 for result in results if result.get("explanation", {}).get("severity") in {"warning", "error"})
    parts = [f"已执行 {len(results)} 条命令"]
    if failed:
        parts.append(f"{failed} 条执行失败")
    if warned:
        parts.append(f"{warned} 条存在需要关注的发现")
    if missing:
        parts.append("缺少命令：" + "、".join(missing))
    return "；".join(parts)


def _truncate_text(value: Any, max_chars: int) -> str:
    text = str(value or "")
    limit = max(500, min(int(max_chars or 4000), 20000))
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... output truncated, total {len(text)} chars"


def _is_truncated(value: Any, max_chars: int) -> bool:
    return len(str(value or "")) > max(500, min(int(max_chars or 4000), 20000))


def _explain_check_payload(check_name: str, stdout: str, stderr: str = "", exit_status: int = 0) -> dict[str, Any]:
    output = "\n".join(part for part in [stdout or "", stderr or ""] if part)
    lower = output.lower()
    findings: list[dict[str, str]] = []
    suggestions: list[str] = []

    if int(exit_status or 0) != 0:
        findings.append({"severity": "warning", "title": "命令非零退出", "detail": f"exit_status={exit_status}"})
        suggestions.append("先查看 stderr 和命令权限，确认是否需要 sudo 或目标系统缺少命令。")

    percents = [int(match) for match in re.findall(r"\b(\d{1,3})%", output) if int(match) <= 100]
    high_percents = [value for value in percents if value >= 85]
    if high_percents:
        max_percent = max(high_percents)
        severity = "error" if max_percent >= 95 else "warning"
        findings.append({"severity": severity, "title": "发现高占用百分比", "detail": f"最高 {max_percent}%"})
        suggestions.append("结合输出中的磁盘、CPU 或内存字段定位具体资源瓶颈。")

    if re.search(r"\b(failed|failure|error|denied|unreachable|timed out|timeout)\b", lower):
        findings.append({"severity": "warning", "title": "输出中包含失败或错误关键词", "detail": "发现 failed/error/denied/unreachable/timeout 等信号"})
        suggestions.append("优先检查对应服务状态、网络连通性和权限配置。")

    if "authentication failed" in lower or "permission denied" in lower:
        findings.append({"severity": "error", "title": "认证或权限异常", "detail": "SSH/命令权限相关输出异常"})
        suggestions.append("检查用户名、密码、密钥、sudo 权限和目标文件权限。")

    if "passwordauthentication yes" in lower or "permitrootlogin yes" in lower:
        findings.append({"severity": "warning", "title": "SSH 配置偏宽松", "detail": "检测到密码登录或 root 登录可能开启"})
        suggestions.append("按安全策略评估是否关闭密码登录或限制 root 登录。")

    if "ping: failed" in lower or "dns" in lower and "unavailable" in lower or "degraded" in lower:
        findings.append({"severity": "warning", "title": "网络或 DNS 可能异常", "detail": "检测到 ping failed、DNS unavailable 或 degraded"})
        suggestions.append("检查路由、DNS、出口防火墙和安全组。")

    if "unsupported package manager" in lower:
        findings.append({"severity": "info", "title": "未识别包管理器", "detail": "补丁检查无法适配该发行版"})

    status = _status_from_findings(findings)
    summary = "未发现明显异常信号" if status == "ok" else "；".join(item["title"] for item in findings[:3])
    return {
        "check_name": check_name,
        "severity": status,
        "summary": summary,
        "findings": findings,
        "suggestions": _unique(suggestions),
    }


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
