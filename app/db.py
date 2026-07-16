from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import DATABASE_PATH


def get_connection() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def db_cursor() -> Iterator[sqlite3.Cursor]:
    connection = get_connection()
    cursor = connection.cursor()
    try:
        yield cursor
        connection.commit()
    finally:
        cursor.close()
        connection.close()


def init_db() -> None:
    with db_cursor() as cursor:
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS servers (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL UNIQUE,
              host TEXT NOT NULL,
              port INTEGER NOT NULL DEFAULT 22,
              username TEXT NOT NULL,
              auth_type TEXT NOT NULL CHECK (auth_type IN ('password', 'key')),
              password_cipher TEXT,
              private_key_cipher TEXT,
              private_key_passphrase_cipher TEXT,
              notes TEXT NOT NULL DEFAULT '',
              tags TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS custom_checks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              command TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS monitor_commands (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              legacy_custom_check_id INTEGER UNIQUE,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              command TEXT NOT NULL,
              scope_all_servers INTEGER NOT NULL DEFAULT 0,
              is_builtin INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS monitor_command_server_targets (
              command_id INTEGER NOT NULL REFERENCES monitor_commands(id) ON DELETE CASCADE,
              server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
              PRIMARY KEY (command_id, server_id)
            );

            CREATE TABLE IF NOT EXISTS monitor_command_tag_targets (
              command_id INTEGER NOT NULL REFERENCES monitor_commands(id) ON DELETE CASCADE,
              tag TEXT NOT NULL,
              PRIMARY KEY (command_id, tag)
            );

            CREATE TABLE IF NOT EXISTS app_settings (
              key TEXT PRIMARY KEY,
              value_cipher TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS activity_logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL,
              level TEXT NOT NULL,
              category TEXT NOT NULL,
              source TEXT NOT NULL,
              direction TEXT NOT NULL DEFAULT '',
              event TEXT NOT NULL,
              request_id TEXT NOT NULL DEFAULT '',
              request_json TEXT,
              response_json TEXT,
              duration_ms INTEGER,
              success INTEGER NOT NULL DEFAULT 1
            );

            CREATE INDEX IF NOT EXISTS idx_activity_logs_created_at
              ON activity_logs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_activity_logs_category
              ON activity_logs(category, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_monitor_command_server_targets_server
              ON monitor_command_server_targets(server_id);
            CREATE INDEX IF NOT EXISTS idx_monitor_command_tag_targets_tag
              ON monitor_command_tag_targets(tag);
            """
        )
        for statement in (
            "ALTER TABLE monitor_commands ADD COLUMN scope_all_servers INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE monitor_commands ADD COLUMN is_builtin INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                cursor.execute(statement)
            except sqlite3.OperationalError:
                pass
        cursor.execute(
            """
            INSERT INTO monitor_commands (
              legacy_custom_check_id, name, description, command, created_at, updated_at
            )
            SELECT c.id, c.name, c.description, c.command, c.created_at, c.updated_at
            FROM custom_checks c
            WHERE NOT EXISTS (
              SELECT 1
              FROM monitor_commands m
              WHERE m.legacy_custom_check_id = c.id
            )
            """
        )
        cursor.execute(
            """
            INSERT OR IGNORE INTO monitor_command_server_targets (command_id, server_id)
            SELECT m.id, c.server_id
            FROM custom_checks c
            JOIN monitor_commands m
              ON m.legacy_custom_check_id = c.id
            """
        )
