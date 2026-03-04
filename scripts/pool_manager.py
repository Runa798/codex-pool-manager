#!/usr/bin/env python3
"""
pool_manager.py — Codex号池管理

用法:
  python3 pool_manager.py --check
  python3 pool_manager.py --replenish
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import requests

EXIT_OK = 0
EXIT_QUOTA_REACHED = 10
EXIT_CPA_UNAVAILABLE = 20
EXIT_DB_LOCK_TIMEOUT = 30
EXIT_ERROR = 1


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_env(env_path: Path) -> Dict[str, str]:
    env = {}
    if not env_path.exists():
        return env
    for line in env_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def script_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_config():
    root = script_root()
    cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
    env = load_env(root / ".env")
    return {
        "db_path": cfg["db_path"],
        "daily_quota": int(cfg.get("daily_quota", 250)),
        "pool_threshold": int(cfg.get("pool_threshold", 388)),
        "cpa_base": cfg.get("cpa_base", "http://127.0.0.1:8317"),
        "cpa_key": env.get(cfg.get("cpa_key_env", "CPA_API_KEY"), env.get("CPA_API_KEY", "")),
        "cpa_auths_dir": cfg["cpa_auths_dir"],
        "register_manager_path": str(Path(__file__).resolve().parent / "register_manager.py"),
    }


def connect_db(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA busy_timeout=10000;")
    return con


def cpa_list_files(cpa_base: str, cpa_key: str) -> List[dict]:
    if not cpa_key:
        raise RuntimeError("CPA_API_KEY 缺失")
    url = f"{cpa_base}/v0/management/auth-files"
    resp = requests.get(url, headers={"Authorization": f"Bearer {cpa_key}"}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data.get("files", [])


def get_cpa_active_count(cpa_base: str, cpa_key: str) -> Tuple[int, int, int, int, List[dict]]:
    files = cpa_list_files(cpa_base, cpa_key)
    active = sum(1 for f in files if f.get("status") == "active")
    disabled = sum(1 for f in files if f.get("status") == "disabled")
    error = sum(1 for f in files if f.get("status") == "error")
    total = len(files)
    return active, disabled, error, total, files


def write_snapshot(con: sqlite3.Connection, active: int, disabled: int, error: int, total: int, imported_count: int = 0):
    con.execute(
        """
        INSERT INTO pool_snapshot (snapshotted_at, active, disabled, error, total, imported_count)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (now_utc_iso(), active, disabled, error, total, imported_count),
    )
    con.commit()


def _parse_iso(v: str):
    try:
        return datetime.fromisoformat(v)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def get_pending_for_import(con: sqlite3.Connection, gap: int) -> List[sqlite3.Row]:
    rows = con.execute(
        """
        SELECT email, domain, token_path, expired_at
          FROM staging
         WHERE status='pending'
         ORDER BY expired_at DESC
        """
    ).fetchall()
    by_domain = defaultdict(deque)
    for r in rows:
        by_domain[r["domain"]].append(r)

    domains = sorted(by_domain.keys())
    selected = []
    idx = 0
    while len(selected) < gap and domains:
        d = domains[idx % len(domains)]
        q = by_domain[d]
        if q:
            selected.append(q.popleft())
        if not q:
            domains.remove(d)
            if not domains:
                break
            idx -= 1
        idx += 1

    return selected


def db_update_status(con: sqlite3.Connection, email: str, status: str, imported_at: str = None, last_error: str = None):
    con.execute(
        """
        UPDATE staging
           SET status=?, imported_at=COALESCE(?, imported_at), last_error=?, updated_at=datetime('now')
         WHERE email=?
        """,
        (status, imported_at, last_error, email),
    )
    con.commit()


def import_to_pool(con: sqlite3.Connection, acc: sqlite3.Row, cpa_auths_dir: str):
    src = acc["token_path"]
    dst = os.path.join(cpa_auths_dir, os.path.basename(src))
    tmp = dst + ".tmp"

    with open(src, "rb") as rf, open(tmp, "wb") as wf:
        shutil.copyfileobj(rf, wf)
        wf.flush()
        os.fsync(wf.fileno())
    os.rename(tmp, dst)

    db_update_status(con, acc["email"], "imported", imported_at=now_utc_iso(), last_error=None)


def _extract_email_from_file_entry(f: dict) -> str:
    for k in ("email", "account", "account_email", "user", "username"):
        v = f.get(k)
        if isinstance(v, str) and "@" in v:
            return v.strip().lower()
    fp = f.get("file_path") or f.get("path") or f.get("name") or ""
    if isinstance(fp, str) and "@" in fp:
        token = fp.split("/")[-1]
        return token.split(".json")[0].lower()
    return ""


def verify_imported(con: sqlite3.Connection, accounts: List[sqlite3.Row], cpa_base: str, cpa_key: str, timeout: int = 30):
    target_emails = {a["email"].lower() for a in accounts}
    deadline = time.time() + timeout
    while time.time() < deadline:
        files = cpa_list_files(cpa_base, cpa_key)
        seen = {_extract_email_from_file_entry(f) for f in files}
        seen = {x for x in seen if x}
        if target_emails.issubset(seen):
            return True
        time.sleep(5)

    for a in accounts:
        db_update_status(con, a["email"], "import_failed", last_error="CPA验证超时")
    return False


def get_today_staging_count(con: sqlite3.Connection) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    row = con.execute(
        "SELECT COUNT(*) c FROM staging WHERE registered_date=? AND status!='dead'", (today,)
    ).fetchone()
    return int(row["c"] if row else 0)


def check_mode(con: sqlite3.Connection, cfg: dict) -> int:
    active, disabled, error, total, _ = get_cpa_active_count(cfg["cpa_base"], cfg["cpa_key"])
    write_snapshot(con, active, disabled, error, total, 0)
    print(f"CPA状态: active={active}, disabled={disabled}, error={error}, total={total}")
    return EXIT_OK


def replenish_mode(con: sqlite3.Connection, cfg: dict) -> int:
    active, disabled, error, total, _ = get_cpa_active_count(cfg["cpa_base"], cfg["cpa_key"])
    write_snapshot(con, active, disabled, error, total, 0)

    threshold = cfg["pool_threshold"]
    gap = threshold - active

    imported_count = 0
    if gap <= 0:
        print(f"[INFO] 号池充足({active}/{threshold})，无需补充")
    else:
        accounts = get_pending_for_import(con, gap)
        if not accounts:
            print("[WARN] staging中无pending账号可导入")
        for acc in accounts:
            try:
                import_to_pool(con, acc, cfg["cpa_auths_dir"])
                imported_count += 1
            except Exception as e:
                db_update_status(con, acc["email"], "import_failed", last_error=str(e))

        if accounts:
            verify_imported(con, accounts, cfg["cpa_base"], cfg["cpa_key"], timeout=30)

        # 写一次补充后快照
        active2, disabled2, error2, total2, _ = get_cpa_active_count(cfg["cpa_base"], cfg["cpa_key"])
        write_snapshot(con, active2, disabled2, error2, total2, imported_count)

    today_new = get_today_staging_count(con)
    if today_new < cfg["daily_quota"]:
        print(f"[INFO] 今日新增{today_new}<{cfg['daily_quota']}，触发注册机")
        subprocess.run(["python3", cfg["register_manager_path"], "--run"], check=False)

    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--replenish", action="store_true")
    args = parser.parse_args()

    try:
        cfg = load_config()
        con = connect_db(cfg["db_path"])

        if args.check:
            return check_mode(con, cfg)
        if args.replenish:
            return replenish_mode(con, cfg)
        return EXIT_ERROR
    except requests.RequestException as e:
        print(f"[ERROR] CPA不可用: {e}")
        return EXIT_CPA_UNAVAILABLE
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower() or "busy" in str(e).lower():
            print(f"[ERROR] DB锁超时: {e}")
            return EXIT_DB_LOCK_TIMEOUT
        print(f"[ERROR] DB错误: {e}")
        return EXIT_ERROR
    except Exception as e:
        print(f"[ERROR] {e}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
