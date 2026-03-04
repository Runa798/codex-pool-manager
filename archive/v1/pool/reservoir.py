#!/usr/bin/env python3
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from config import RESERVOIR_DB

DDL = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password TEXT,
    access_token TEXT,
    refresh_token TEXT,
    id_token TEXT,
    account_id TEXT,
    expired_at TEXT,
    status TEXT DEFAULT 'available',
    created_at TEXT DEFAULT (datetime('now')),
    imported_at TEXT,
    last_refresh_at TEXT
);
"""


@contextmanager
def _conn():
    Path(RESERVOIR_DB).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(RESERVOIR_DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        conn.execute(DDL)


def add_account(
    email: str,
    password: str = "",
    access_token: str = "",
    refresh_token: str = "",
    id_token: str = "",
    account_id: str = "",
    expired_at: str = "",
) -> bool:
    init_db()
    with _conn() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO accounts
            (email, password, access_token, refresh_token, id_token, account_id, expired_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'available')
            """,
            (email, password, access_token, refresh_token, id_token, account_id, expired_at),
        )
        return cur.rowcount > 0


def get_available(limit: int) -> list[dict[str, Any]]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM accounts
            WHERE status = 'available'
            ORDER BY id ASC
            LIMIT ?
            """,
            (max(limit, 0),),
        ).fetchall()
        return [dict(row) for row in rows]


def mark_imported(email: str) -> None:
    with _conn() as conn:
        conn.execute(
            """
            UPDATE accounts
            SET status='imported', imported_at=datetime('now')
            WHERE email=?
            """,
            (email,),
        )


def mark_dead(email: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE accounts SET status='dead' WHERE email=?", (email,))


def update_tokens(email: str, new_tokens: dict[str, Any]) -> None:
    with _conn() as conn:
        conn.execute(
            """
            UPDATE accounts
            SET access_token=?,
                refresh_token=COALESCE(NULLIF(?, ''), refresh_token),
                id_token=COALESCE(NULLIF(?, ''), id_token),
                expired_at=COALESCE(NULLIF(?, ''), expired_at),
                last_refresh_at=datetime('now')
            WHERE email=?
            """,
            (
                new_tokens.get("access_token", ""),
                new_tokens.get("refresh_token", ""),
                new_tokens.get("id_token", ""),
                new_tokens.get("expired_at", "") or new_tokens.get("expired", ""),
                email,
            ),
        )


def count_available() -> int:
    init_db()
    with _conn() as conn:
        row = conn.execute("SELECT COUNT(1) AS c FROM accounts WHERE status='available'").fetchone()
        return int(row["c"]) if row else 0


def count_all() -> int:
    init_db()
    with _conn() as conn:
        row = conn.execute("SELECT COUNT(1) AS c FROM accounts").fetchone()
        return int(row["c"]) if row else 0
