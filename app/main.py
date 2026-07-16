from __future__ import annotations

import anyio
import sqlite3
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import (
    STATIC_DIR,
    TEMPLATES_DIR,
    build_xiaozhi_endpoint,
    mask_token,
    settings,
    split_xiaozhi_endpoint,
)
from .db import init_db
from .mcp_tools import mcp, monitor, repository
from .schemas import (
    CommandRunPayload,
    CustomCheckPayload,
    LoginPayload,
    MonitorCommandPayload,
    ServerPayload,
    XiaozhiSettingsPayload,
)
from .security import hash_password, verify_password
from .xiaozhi_bridge import XiaozhiBridge


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
mcp_http_app = mcp.streamable_http_app()
xiaozhi_bridge = XiaozhiBridge(
    mcp,
    repository,
    reconnect_delay_seconds=settings.xiaozhi_reconnect_delay_seconds,
)

XIAOZHI_ENABLED_KEY = "xiaozhi_bridge_enabled"
XIAOZHI_ENDPOINT_KEY = "xiaozhi_endpoint_url"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    bootstrap_admin_user()
    repository.ensure_builtin_monitor_commands()
    runtime_config = get_xiaozhi_runtime_config()
    xiaozhi_bridge.configure(**runtime_config)
    async with anyio.create_task_group() as task_group:
        async with mcp_http_app.router.lifespan_context(mcp_http_app):
            task_group.start_soon(xiaozhi_bridge.run_forever)
            yield
            task_group.cancel_scope.cancel()


APP_NAME = "McpEye 智守"


app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.resolved_secret,
    same_site="lax",
    https_only=False,
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/mcp", mcp_http_app)


def bootstrap_admin_user() -> None:
    user = repository.get_user_by_username(settings.admin_username)
    if user:
        return
    repository.create_user(
        settings.admin_username,
        hash_password(settings.admin_password),
    )


def get_xiaozhi_runtime_config() -> dict[str, Any]:
    stored_enabled = repository.get_app_setting(XIAOZHI_ENABLED_KEY)
    stored_endpoint = repository.get_app_setting(XIAOZHI_ENDPOINT_KEY)
    enabled = settings.xiaozhi_bridge_enabled if stored_enabled is None else stored_enabled == "true"
    endpoint_url = stored_endpoint if stored_endpoint is not None else settings.xiaozhi_endpoint_url
    return {"enabled": enabled, "endpoint_url": endpoint_url}


def get_xiaozhi_public_config() -> dict[str, Any]:
    runtime_config = get_xiaozhi_runtime_config()
    endpoint_base_url, token = split_xiaozhi_endpoint(runtime_config["endpoint_url"])
    return {
        "enabled": runtime_config["enabled"],
        "endpoint_base_url": endpoint_base_url,
        "has_token": bool(token),
        "token_masked": mask_token(token),
        "status": xiaozhi_bridge.snapshot(),
    }


def render_dashboard(request: Request, user: dict[str, Any], initial_view: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "username": user["username"],
            "initial_view": initial_view,
        },
    )


def get_current_user(request: Request) -> dict[str, Any]:
    user_id = request.session.get("user_id")
    username = request.session.get("username")
    if not user_id or not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required")
    user = repository.get_user_by_username(username)
    if not user:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required")
    return user


def require_html_user(request: Request) -> dict[str, Any] | None:
    try:
        return get_current_user(request)
    except HTTPException:
        return None


def validate_server_credentials(payload: ServerPayload, existing: dict[str, Any] | None = None) -> None:
    if payload.auth_type == "password":
        has_existing = bool(existing and existing.get("has_password"))
        if not payload.password and not has_existing:
            raise HTTPException(status_code=400, detail="Password auth requires a password")
        return
    has_existing = bool(existing and existing.get("has_private_key"))
    if not payload.private_key and not has_existing:
        raise HTTPException(status_code=400, detail="Key auth requires a private key")


def bootstrap_payload(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "user": {"id": user["id"], "username": user["username"]},
        "servers": repository.list_servers(),
        "server_tags": repository.list_server_tags(),
        "commands": repository.list_monitor_commands(),
        "defaults": {
            "mcp_url": f"http://{settings.app_host}:{settings.app_port}/mcp",
        },
        "integrations": {
            "xiaozhi": get_xiaozhi_public_config(),
        },
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    if require_html_user(request):
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "default_username": settings.admin_username,
        },
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    user = require_html_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    return render_dashboard(request, user, "overview")


@app.get("/servers", response_class=HTMLResponse)
async def servers_page(request: Request) -> HTMLResponse:
    user = require_html_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    return render_dashboard(request, user, "servers")


@app.get("/commands", response_class=HTMLResponse)
async def commands_page(request: Request) -> HTMLResponse:
    user = require_html_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    return render_dashboard(request, user, "commands")


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request) -> HTMLResponse:
    user = require_html_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    return render_dashboard(request, user, "logs")


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    user = require_html_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    return render_dashboard(request, user, "settings")


@app.get("/about", response_class=HTMLResponse)
async def about_page(request: Request) -> HTMLResponse:
    user = require_html_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    return render_dashboard(request, user, "about")


@app.post("/api/auth/login")
async def login(payload: LoginPayload, request: Request) -> JSONResponse:
    user = repository.get_user_by_username(payload.username)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    request.session["user_id"] = user["id"]
    request.session["username"] = user["username"]
    return JSONResponse({"ok": True, "username": user["username"]})


@app.post("/api/auth/logout")
async def logout(request: Request) -> JSONResponse:
    request.session.clear()
    return JSONResponse({"ok": True})


@app.get("/api/bootstrap")
async def bootstrap(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return bootstrap_payload(user)


@app.get("/api/integrations/xiaozhi")
async def get_xiaozhi_bridge_status(
    _: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    return get_xiaozhi_public_config()


@app.put("/api/integrations/xiaozhi")
async def update_xiaozhi_bridge_settings(
    payload: XiaozhiSettingsPayload,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    parts = urlsplit(payload.endpoint_base_url.strip())
    if parts.scheme not in {"ws", "wss"} or not parts.netloc:
        raise HTTPException(status_code=400, detail="Endpoint must be a valid ws:// or wss:// address")

    current = get_xiaozhi_runtime_config()
    _, current_token = split_xiaozhi_endpoint(current["endpoint_url"])
    token = payload.token.strip() if payload.token and payload.token.strip() else current_token
    if payload.enabled and not token:
        raise HTTPException(status_code=400, detail="A Xiaozhi token is required before enabling the bridge")

    endpoint_url = build_xiaozhi_endpoint(payload.endpoint_base_url, token)
    repository.set_app_setting(XIAOZHI_ENABLED_KEY, "true" if payload.enabled else "false")
    repository.set_app_setting(XIAOZHI_ENDPOINT_KEY, endpoint_url)
    xiaozhi_bridge.configure(enabled=payload.enabled, endpoint_url=endpoint_url)
    repository.add_activity_log(
        category="settings",
        source="web",
        event="Update Xiaozhi bridge settings",
        request={
            "enabled": payload.enabled,
            "endpoint_base_url": payload.endpoint_base_url,
            "token": mask_token(token),
            "updated_by": user["username"],
        },
    )
    return get_xiaozhi_public_config()


@app.post("/api/integrations/xiaozhi/reconnect")
async def reconnect_xiaozhi_bridge(
    _: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    xiaozhi_bridge.request_reconnect()
    repository.add_activity_log(
        category="xiaozhi",
        source="web",
        event="Reconnect Xiaozhi bridge",
        request={"action": "reconnect"},
    )
    return xiaozhi_bridge.snapshot()


@app.get("/api/logs")
async def list_logs(
    limit: int = 100,
    category: str = "",
    level: str = "",
    _: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    return repository.list_activity_logs(limit=limit, category=category, level=level)


@app.delete("/api/logs")
async def clear_logs(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, bool]:
    repository.clear_activity_logs()
    repository.add_activity_log(
        category="settings",
        source="web",
        event="Clear activity logs",
        request={"cleared_by": user["username"]},
    )
    return {"ok": True}


@app.get("/api/servers")
async def list_servers(_: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return repository.list_servers()


@app.post("/api/servers")
async def create_server(
    payload: ServerPayload,
    _: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    validate_server_credentials(payload)
    try:
        return repository.create_server(payload.model_dump())
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=400, detail=f"Unable to save server: {exc}") from exc


@app.put("/api/servers/{server_id}")
async def update_server(
    server_id: int,
    payload: ServerPayload,
    _: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    existing = repository.get_server(server_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Server not found")
    validate_server_credentials(payload, existing)
    try:
        server = repository.update_server(server_id, payload.model_dump())
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=400, detail=f"Unable to update server: {exc}") from exc
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


@app.delete("/api/servers/{server_id}")
async def delete_server(
    server_id: int,
    _: dict[str, Any] = Depends(get_current_user),
) -> dict[str, bool]:
    repository.delete_server(server_id)
    return {"ok": True}


@app.post("/api/servers/{server_id}/probe")
async def probe_server(
    server_id: int,
    _: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    started_at = perf_counter()
    try:
        result = await monitor.get_snapshot_by_id(server_id)
        repository.add_activity_log(
            category="ssh_probe",
            source="web",
            event="Probe server snapshot",
            direction="request_response",
            request={"server_id": server_id},
            response=result,
            duration_ms=round((perf_counter() - started_at) * 1000),
        )
        return result
    except ValueError as exc:
        repository.add_activity_log(
            category="ssh_probe",
            source="web",
            event="Probe server snapshot",
            level="error",
            request={"server_id": server_id},
            response={"error": str(exc)},
            duration_ms=round((perf_counter() - started_at) * 1000),
            success=False,
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        repository.add_activity_log(
            category="ssh_probe",
            source="web",
            event="Probe server snapshot",
            level="error",
            request={"server_id": server_id},
            response={"error": str(exc)},
            duration_ms=round((perf_counter() - started_at) * 1000),
            success=False,
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/server-status-board")
async def get_server_status_board(
    _: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    started_at = perf_counter()
    result = await monitor.get_status_board()
    repository.add_activity_log(
        category="ssh_probe",
        source="web",
        event="Refresh server status board",
        direction="request_response",
        request={"scope": "all_servers"},
        response={"count": len(result), "items": result},
        duration_ms=round((perf_counter() - started_at) * 1000),
    )
    return result


@app.get("/api/commands")
async def list_commands(_: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return repository.list_monitor_commands()


@app.post("/api/commands")
async def create_command(
    payload: MonitorCommandPayload,
    _: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return repository.create_monitor_command(payload.model_dump())
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=400, detail=f"Unable to save command: {exc}") from exc


@app.put("/api/commands/{command_id}")
async def update_command(
    command_id: int,
    payload: MonitorCommandPayload,
    _: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        command = repository.update_monitor_command(command_id, payload.model_dump())
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=400, detail=f"Unable to update command: {exc}") from exc
    if not command:
        raise HTTPException(status_code=404, detail="Command not found")
    return command


@app.delete("/api/commands/{command_id}")
async def delete_command(
    command_id: int,
    _: dict[str, Any] = Depends(get_current_user),
) -> dict[str, bool]:
    repository.delete_monitor_command(command_id)
    return {"ok": True}


@app.post("/api/commands/{command_id}/run")
async def run_command(
    command_id: int,
    payload: CommandRunPayload,
    _: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    started_at = perf_counter()
    try:
        result = await monitor.run_monitor_command_by_id(payload.server_id, command_id)
        repository.add_activity_log(
            category="ssh_probe",
            source="web",
            event="Run monitor command",
            direction="request_response",
            request={"command_id": command_id, "server_id": payload.server_id},
            response=result,
            duration_ms=round((perf_counter() - started_at) * 1000),
        )
        return result
    except ValueError as exc:
        repository.add_activity_log(
            category="ssh_probe",
            source="web",
            event="Run monitor command",
            level="error",
            request={"command_id": command_id, "server_id": payload.server_id},
            response={"error": str(exc)},
            duration_ms=round((perf_counter() - started_at) * 1000),
            success=False,
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        repository.add_activity_log(
            category="ssh_probe",
            source="web",
            event="Run monitor command",
            level="error",
            request={"command_id": command_id, "server_id": payload.server_id},
            response={"error": str(exc)},
            duration_ms=round((perf_counter() - started_at) * 1000),
            success=False,
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/servers/{server_id}/checks")
async def list_custom_checks(
    server_id: int,
    _: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    return repository.list_custom_checks(server_id)


@app.post("/api/servers/{server_id}/checks")
async def create_custom_check(
    server_id: int,
    payload: CustomCheckPayload,
    _: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    if not repository.get_server(server_id):
        raise HTTPException(status_code=404, detail="Server not found")
    return repository.create_custom_check(server_id, payload.model_dump())


@app.put("/api/servers/{server_id}/checks/{check_id}")
async def update_custom_check(
    server_id: int,
    check_id: int,
    payload: CustomCheckPayload,
    _: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    check = repository.update_custom_check(server_id, check_id, payload.model_dump())
    if not check:
        raise HTTPException(status_code=404, detail="Custom check not found")
    return check


@app.delete("/api/servers/{server_id}/checks/{check_id}")
async def delete_custom_check(
    server_id: int,
    check_id: int,
    _: dict[str, Any] = Depends(get_current_user),
) -> dict[str, bool]:
    repository.delete_custom_check(server_id, check_id)
    return {"ok": True}


@app.post("/api/servers/{server_id}/checks/{check_id}/run")
async def run_custom_check(
    server_id: int,
    check_id: int,
    _: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    started_at = perf_counter()
    try:
        result = await monitor.run_custom_check_by_id(server_id, check_id)
        repository.add_activity_log(
            category="ssh_probe",
            source="web",
            event="Run legacy custom check",
            direction="request_response",
            request={"server_id": server_id, "check_id": check_id},
            response=result,
            duration_ms=round((perf_counter() - started_at) * 1000),
        )
        return result
    except ValueError as exc:
        repository.add_activity_log(
            category="ssh_probe",
            source="web",
            event="Run legacy custom check",
            level="error",
            request={"server_id": server_id, "check_id": check_id},
            response={"error": str(exc)},
            duration_ms=round((perf_counter() - started_at) * 1000),
            success=False,
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        repository.add_activity_log(
            category="ssh_probe",
            source="web",
            event="Run legacy custom check",
            level="error",
            request={"server_id": server_id, "check_id": check_id},
            response={"error": str(exc)},
            duration_ms=round((perf_counter() - started_at) * 1000),
            success=False,
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
