from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .builtin_commands import BUILTIN_MONITOR_COMMANDS
from .db import db_cursor
from .security import CredentialCipher


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Repository:
    def __init__(self, cipher: CredentialCipher) -> None:
        self.cipher = cipher

    def create_user(self, username: str, password_hash: str) -> None:
        with db_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (username, password_hash, created_at)
                VALUES (?, ?, ?)
                """,
                (username, password_hash, utc_now()),
            )

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with db_cursor() as cursor:
            row = cursor.execute(
                "SELECT * FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        return dict(row) if row else None

    def get_app_setting(self, key: str) -> str | None:
        with db_cursor() as cursor:
            row = cursor.execute(
                "SELECT value_cipher FROM app_settings WHERE key = ?",
                (key,),
            ).fetchone()
        if not row:
            return None
        return self.cipher.decrypt(row["value_cipher"])

    def set_app_setting(self, key: str, value: str) -> None:
        value_cipher = self.cipher.encrypt(value)
        if value_cipher is None:
            raise ValueError("Setting value cannot be empty")
        with db_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO app_settings (key, value_cipher, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  value_cipher = excluded.value_cipher,
                  updated_at = excluded.updated_at
                """,
                (key, value_cipher, utc_now()),
            )

    def add_activity_log(
        self,
        *,
        category: str,
        source: str,
        event: str,
        level: str = "info",
        direction: str = "",
        request_id: str = "",
        request: Any = None,
        response: Any = None,
        duration_ms: int | None = None,
        success: bool = True,
    ) -> None:
        with db_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO activity_logs (
                  created_at, level, category, source, direction, event,
                  request_id, request_json, response_json, duration_ms, success
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now(),
                    level,
                    category,
                    source,
                    direction,
                    event,
                    request_id,
                    self._serialize_log_value(request),
                    self._serialize_log_value(response),
                    duration_ms,
                    1 if success else 0,
                ),
            )

    def list_activity_logs(
        self,
        *,
        limit: int = 100,
        category: str = "",
        level: str = "",
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if level:
            clauses.append("level = ?")
            params.append(level)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        with db_cursor() as cursor:
            rows = cursor.execute(
                f"""
                SELECT * FROM activity_logs
                {where_sql}
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["success"] = bool(item["success"])
            item["request"] = self._deserialize_log_value(item.pop("request_json"))
            item["response"] = self._deserialize_log_value(item.pop("response_json"))
            items.append(item)
        return items

    def clear_activity_logs(self) -> None:
        with db_cursor() as cursor:
            cursor.execute("DELETE FROM activity_logs")

    def ensure_builtin_monitor_commands(self) -> None:
        with db_cursor() as cursor:
            for item in BUILTIN_MONITOR_COMMANDS:
                existing = cursor.execute(
                    "SELECT id FROM monitor_commands WHERE name = ?",
                    (item["name"],),
                ).fetchone()
                now = utc_now()
                if existing:
                    command_id = int(existing["id"])
                    cursor.execute(
                        """
                        UPDATE monitor_commands
                        SET description = ?, command = ?, scope_all_servers = ?, is_builtin = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            item["description"],
                            item["command"],
                            1 if item.get("scope_all_servers") else 0,
                            1 if item.get("is_builtin") else 0,
                            now,
                            command_id,
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO monitor_commands (
                          name, description, command, scope_all_servers, is_builtin, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item["name"],
                            item["description"],
                            item["command"],
                            1 if item.get("scope_all_servers") else 0,
                            1 if item.get("is_builtin") else 0,
                            now,
                            now,
                        ),
                    )
                    command_id = int(cursor.lastrowid)
                self._replace_monitor_command_targets(cursor, command_id, [], [])

    def list_servers(self) -> list[dict[str, Any]]:
        with db_cursor() as cursor:
            rows = cursor.execute(
                "SELECT * FROM servers ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [self._server_row_to_public_dict(row) for row in rows]

    def get_server(self, server_id: int) -> dict[str, Any] | None:
        with db_cursor() as cursor:
            row = cursor.execute(
                "SELECT * FROM servers WHERE id = ?",
                (server_id,),
            ).fetchone()
        return self._server_row_to_full_dict(row) if row else None

    def get_server_by_name(self, name: str) -> dict[str, Any] | None:
        with db_cursor() as cursor:
            row = cursor.execute(
                "SELECT * FROM servers WHERE lower(name) = lower(?)",
                (name,),
            ).fetchone()
        return self._server_row_to_full_dict(row) if row else None

    def create_server(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with db_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO servers (
                  name, host, port, username, auth_type,
                  password_cipher, private_key_cipher, private_key_passphrase_cipher,
                  notes, tags, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["name"],
                    payload["host"],
                    payload["port"],
                    payload["username"],
                    payload["auth_type"],
                    self.cipher.encrypt(payload.get("password")),
                    self.cipher.encrypt(payload.get("private_key")),
                    self.cipher.encrypt(payload.get("private_key_passphrase")),
                    payload.get("notes", ""),
                    ",".join(payload.get("tags", [])),
                    now,
                    now,
                ),
            )
            server_id = cursor.lastrowid
        server = self.get_server(server_id)
        assert server is not None
        return server

    def update_server(self, server_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        current = self.get_server(server_id)
        if not current:
            return None

        password = payload.get("password")
        private_key = payload.get("private_key")
        private_key_passphrase = payload.get("private_key_passphrase")

        if payload["auth_type"] == "password":
            encrypted_password = self.cipher.encrypt(password) if password else current["password_cipher"]
            encrypted_private_key = None
            encrypted_passphrase = None
        else:
            encrypted_password = None
            encrypted_private_key = self.cipher.encrypt(private_key) if private_key else current["private_key_cipher"]
            encrypted_passphrase = (
                self.cipher.encrypt(private_key_passphrase)
                if private_key_passphrase
                else current["private_key_passphrase_cipher"]
            )

        with db_cursor() as cursor:
            cursor.execute(
                """
                UPDATE servers
                SET name = ?, host = ?, port = ?, username = ?, auth_type = ?,
                    password_cipher = ?, private_key_cipher = ?, private_key_passphrase_cipher = ?,
                    notes = ?, tags = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    payload["name"],
                    payload["host"],
                    payload["port"],
                    payload["username"],
                    payload["auth_type"],
                    encrypted_password,
                    encrypted_private_key,
                    encrypted_passphrase,
                    payload.get("notes", ""),
                    ",".join(payload.get("tags", [])),
                    utc_now(),
                    server_id,
                ),
            )
        return self.get_server(server_id)

    def delete_server(self, server_id: int) -> None:
        with db_cursor() as cursor:
            cursor.execute("DELETE FROM servers WHERE id = ?", (server_id,))

    def list_server_tags(self) -> list[str]:
        tags: dict[str, str] = {}
        for server in self.list_servers():
            for tag in server["tags"]:
                key = tag.lower()
                if key not in tags:
                    tags[key] = tag
        return sorted(tags.values(), key=str.lower)

    def list_monitor_commands(self) -> list[dict[str, Any]]:
        with db_cursor() as cursor:
            command_rows = cursor.execute(
                """
                SELECT * FROM monitor_commands
                ORDER BY updated_at DESC, name COLLATE NOCASE
                """
            ).fetchall()
            server_target_rows = cursor.execute(
                """
                SELECT command_id, server_id
                FROM monitor_command_server_targets
                """
            ).fetchall()
            tag_target_rows = cursor.execute(
                """
                SELECT command_id, tag
                FROM monitor_command_tag_targets
                ORDER BY tag COLLATE NOCASE
                """
            ).fetchall()
        return self._hydrate_monitor_commands(command_rows, server_target_rows, tag_target_rows)

    def get_monitor_command(self, command_id: int) -> dict[str, Any] | None:
        with db_cursor() as cursor:
            command_row = cursor.execute(
                "SELECT * FROM monitor_commands WHERE id = ?",
                (command_id,),
            ).fetchone()
            if not command_row:
                return None
            server_target_rows = cursor.execute(
                """
                SELECT command_id, server_id
                FROM monitor_command_server_targets
                WHERE command_id = ?
                """
                ,
                (command_id,),
            ).fetchall()
            tag_target_rows = cursor.execute(
                """
                SELECT command_id, tag
                FROM monitor_command_tag_targets
                WHERE command_id = ?
                ORDER BY tag COLLATE NOCASE
                """,
                (command_id,),
            ).fetchall()
        items = self._hydrate_monitor_commands([command_row], server_target_rows, tag_target_rows)
        return items[0] if items else None

    def create_monitor_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with db_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO monitor_commands (name, description, command, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    payload["name"],
                    payload.get("description", ""),
                    payload["command"],
                    now,
                    now,
                ),
            )
            command_id = cursor.lastrowid
            cursor.execute(
                """
                UPDATE monitor_commands
                SET scope_all_servers = ?, is_builtin = 0
                WHERE id = ?
                """,
                (1 if payload.get("scope_all_servers") else 0, command_id),
            )
            self._replace_monitor_command_targets(
                cursor,
                command_id,
                payload.get("server_ids", []),
                payload.get("tags", []),
            )
        command = self.get_monitor_command(command_id)
        assert command is not None
        return command

    def update_monitor_command(
        self,
        command_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        current = self.get_monitor_command(command_id)
        if not current:
            return None
        with db_cursor() as cursor:
            cursor.execute(
                """
                UPDATE monitor_commands
                SET name = ?, description = ?, command = ?, scope_all_servers = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    payload["name"],
                    payload.get("description", ""),
                    payload["command"],
                    1 if payload.get("scope_all_servers") else 0,
                    utc_now(),
                    command_id,
                ),
            )
            self._replace_monitor_command_targets(
                cursor,
                command_id,
                payload.get("server_ids", []),
                payload.get("tags", []),
            )
        return self.get_monitor_command(command_id)

    def delete_monitor_command(self, command_id: int) -> None:
        with db_cursor() as cursor:
            cursor.execute("DELETE FROM monitor_commands WHERE id = ?", (command_id,))

    def get_monitor_command_for_server(
        self,
        command_id: int,
        server_id: int,
    ) -> dict[str, Any] | None:
        command = self.get_monitor_command(command_id)
        if not command:
            return None
        if any(server["id"] == server_id for server in command["applicable_servers"]):
            return command
        return None

    def find_monitor_commands_by_name_for_server(
        self,
        server_id: int,
        name: str,
    ) -> list[dict[str, Any]]:
        normalized = name.strip().lower()
        if not normalized:
            return []
        items: list[dict[str, Any]] = []
        for command in self.list_monitor_commands():
            if command["name"].strip().lower() != normalized:
                continue
            if any(server["id"] == server_id for server in command["applicable_servers"]):
                items.append(command)
        return items

    def list_custom_checks(self, server_id: int) -> list[dict[str, Any]]:
        with db_cursor() as cursor:
            rows = cursor.execute(
                """
                SELECT * FROM custom_checks
                WHERE server_id = ?
                ORDER BY name COLLATE NOCASE
                """,
                (server_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_custom_check(self, server_id: int, check_id: int) -> dict[str, Any] | None:
        with db_cursor() as cursor:
            row = cursor.execute(
                """
                SELECT * FROM custom_checks
                WHERE server_id = ? AND id = ?
                """,
                (server_id, check_id),
            ).fetchone()
        return dict(row) if row else None

    def get_custom_check_by_name(self, server_id: int, name: str) -> dict[str, Any] | None:
        with db_cursor() as cursor:
            row = cursor.execute(
                """
                SELECT * FROM custom_checks
                WHERE server_id = ? AND lower(name) = lower(?)
                """,
                (server_id, name),
            ).fetchone()
        return dict(row) if row else None

    def create_custom_check(self, server_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with db_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO custom_checks (server_id, name, description, command, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    server_id,
                    payload["name"],
                    payload.get("description", ""),
                    payload["command"],
                    now,
                    now,
                ),
            )
            check_id = cursor.lastrowid
        check = self.get_custom_check(server_id, check_id)
        assert check is not None
        return check

    def update_custom_check(
        self,
        server_id: int,
        check_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        with db_cursor() as cursor:
            cursor.execute(
                """
                UPDATE custom_checks
                SET name = ?, description = ?, command = ?, updated_at = ?
                WHERE server_id = ? AND id = ?
                """,
                (
                    payload["name"],
                    payload.get("description", ""),
                    payload["command"],
                    utc_now(),
                    server_id,
                    check_id,
                ),
            )
        return self.get_custom_check(server_id, check_id)

    def delete_custom_check(self, server_id: int, check_id: int) -> None:
        with db_cursor() as cursor:
            cursor.execute(
                "DELETE FROM custom_checks WHERE server_id = ? AND id = ?",
                (server_id, check_id),
            )

    def _hydrate_monitor_commands(
        self,
        command_rows: list[Any],
        server_target_rows: list[Any],
        tag_target_rows: list[Any],
    ) -> list[dict[str, Any]]:
        servers = self.list_servers()
        servers_by_id = {server["id"]: server for server in servers}

        direct_targets: dict[int, list[int]] = {}
        for row in server_target_rows:
            item = dict(row)
            direct_targets.setdefault(item["command_id"], [])
            if item["server_id"] not in direct_targets[item["command_id"]]:
                direct_targets[item["command_id"]].append(item["server_id"])

        tag_targets: dict[int, list[str]] = {}
        for row in tag_target_rows:
            item = dict(row)
            tag_targets.setdefault(item["command_id"], [])
            if item["tag"] not in tag_targets[item["command_id"]]:
                tag_targets[item["command_id"]].append(item["tag"])

        items: list[dict[str, Any]] = []
        for row in command_rows:
            item = dict(row)
            server_ids = sorted(direct_targets.get(item["id"], []))
            tags = sorted(tag_targets.get(item["id"], []), key=str.lower)
            applicable: dict[int, dict[str, Any]] = {}
            item["scope_all_servers"] = bool(item.get("scope_all_servers"))
            item["is_builtin"] = bool(item.get("is_builtin"))
            if item["scope_all_servers"]:
                for server in servers:
                    applicable[server["id"]] = server
            for server_id in server_ids:
                server = servers_by_id.get(server_id)
                if server:
                    applicable[server_id] = server
            if tags:
                lowered_tags = {tag.lower() for tag in tags}
                for server in servers:
                    if lowered_tags.intersection(tag.lower() for tag in server["tags"]):
                        applicable[server["id"]] = server

            item["server_ids"] = server_ids
            item["server_names"] = [
                servers_by_id[server_id]["name"]
                for server_id in server_ids
                if server_id in servers_by_id
            ]
            item["tags"] = tags
            item["applicable_servers"] = [
                {"id": server["id"], "name": server["name"], "tags": server["tags"]}
                for server in sorted(applicable.values(), key=lambda server: server["name"].lower())
            ]
            items.append(item)
        return items

    def _replace_monitor_command_targets(
        self,
        cursor: Any,
        command_id: int,
        server_ids: list[int],
        tags: list[str],
    ) -> None:
        normalized_server_ids: list[int] = []
        for server_id in server_ids:
            parsed = int(server_id)
            if parsed > 0 and parsed not in normalized_server_ids:
                normalized_server_ids.append(parsed)

        normalized_tags: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            text = str(tag).strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized_tags.append(text)

        cursor.execute(
            "DELETE FROM monitor_command_server_targets WHERE command_id = ?",
            (command_id,),
        )
        cursor.execute(
            "DELETE FROM monitor_command_tag_targets WHERE command_id = ?",
            (command_id,),
        )
        cursor.executemany(
            """
            INSERT INTO monitor_command_server_targets (command_id, server_id)
            VALUES (?, ?)
            """,
            [(command_id, server_id) for server_id in normalized_server_ids],
        )
        cursor.executemany(
            """
            INSERT INTO monitor_command_tag_targets (command_id, tag)
            VALUES (?, ?)
            """,
            [(command_id, tag) for tag in normalized_tags],
        )

    def _server_row_to_public_dict(self, row: Any) -> dict[str, Any]:
        item = dict(row)
        return {
            "id": item["id"],
            "name": item["name"],
            "host": item["host"],
            "port": item["port"],
            "username": item["username"],
            "auth_type": item["auth_type"],
            "notes": item["notes"],
            "tags": [tag for tag in item["tags"].split(",") if tag],
            "created_at": item["created_at"],
            "updated_at": item["updated_at"],
            "has_password": bool(item["password_cipher"]),
            "has_private_key": bool(item["private_key_cipher"]),
        }

    def _server_row_to_full_dict(self, row: Any) -> dict[str, Any]:
        item = dict(row)
        public = self._server_row_to_public_dict(row)
        public["password_cipher"] = item["password_cipher"]
        public["private_key_cipher"] = item["private_key_cipher"]
        public["private_key_passphrase_cipher"] = item["private_key_passphrase_cipher"]
        return public

    def _serialize_log_value(self, value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, default=str)

    def _deserialize_log_value(self, value: str | None) -> Any:
        if value is None:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
