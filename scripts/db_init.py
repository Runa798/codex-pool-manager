#!/usr/bin/env python3
import sqlite3
from pathlib import Path

DB_PATH = Path('/home/heye/.openclaw/data/codex_pool.db')


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS staging (
                email           TEXT PRIMARY KEY,
                domain          TEXT NOT NULL,
                registrar       TEXT NOT NULL,
                proxy_used      TEXT,
                registered_at   TEXT NOT NULL,
                registered_date TEXT NOT NULL,
                token_path      TEXT NOT NULL,
                expired_at      TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                imported_at     TEXT,
                last_error      TEXT,
                retry_count     INTEGER NOT NULL DEFAULT 0,
                updated_at      TEXT
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_staging_status_exp ON staging(status, expired_at);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_staging_date ON staging(registered_date);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_staging_status ON staging(status);")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS register_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT NOT NULL,
                registrar   TEXT NOT NULL,
                proxy       TEXT,
                success     INTEGER NOT NULL DEFAULT 0,
                failed      INTEGER NOT NULL DEFAULT 0,
                started_at  TEXT,
                finished_at TEXT,
                stop_reason TEXT
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_log_date ON register_log(date, registrar);")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pool_snapshot (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshotted_at TEXT NOT NULL,
                active         INTEGER NOT NULL,
                disabled       INTEGER NOT NULL DEFAULT 0,
                error          INTEGER NOT NULL DEFAULT 0,
                total          INTEGER NOT NULL,
                imported_count INTEGER NOT NULL DEFAULT 0
            );
            """
        )

        conn.commit()
    finally:
        conn.close()


if __name__ == '__main__':
    init_db(DB_PATH)
    print(f'DB initialized at {DB_PATH}')
