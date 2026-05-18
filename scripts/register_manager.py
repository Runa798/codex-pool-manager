#!/usr/bin/env python3
"""
register_manager.py — Codex注册机管理器

用法:
  python3 register_manager.py --status           # 查今日进度
  python3 register_manager.py --run              # 启动注册（达配额停止）
  python3 register_manager.py --run --quota 50   # 覆盖配额
  python3 register_manager.py --scan-only        # 只扫描新token文件写入DB，不启动注册
"""

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

EXIT_OK = 0
EXIT_QUOTA_REACHED = 10
EXIT_CPA_UNAVAILABLE = 20
EXIT_DB_LOCK_TIMEOUT = 30
EXIT_ERROR = 1


@dataclass
class Registrar:
    name: str
    dir: str
    domain: str
    proxy_env: str
    weight: int = 1


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

    registrars = [Registrar(**r) for r in cfg.get("registrars", [])]
    staging_dirs = cfg.get("staging_dirs") or {
        r.name: os.path.join(cfg["staging_dir"], r.name) + "/" for r in registrars
    }

    return {
        "db_path": cfg["db_path"],
        "daily_quota": int(cfg.get("daily_quota", 250)),
        "registrars": registrars,
        "staging_dir": cfg.get("staging_dir"),
        "staging_dirs": staging_dirs,
        "env": env,
    }


def connect_db(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA busy_timeout=10000;")
    return con


def _parse_time_for_compare(s: str):
    if not s:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def scan_and_import(con: sqlite3.Connection, registrar_name: str, staging_subdir: str, domain_hint: str = "") -> int:
    p = Path(staging_subdir)
    if not p.exists():
        return 0

    imported = 0
    cur = con.cursor()
    for fp in sorted(p.glob("*.json")):
        try:
            token = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue

        email = (token.get("email") or "").strip().lower()
        expired = token.get("expired")
        if not email or not expired:
            continue

        try:
            st = fp.stat()
            dt_local = datetime.fromtimestamp(st.st_mtime)
            dt_utc = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        except Exception:
            dt_local = datetime.now()
            dt_utc = datetime.now(timezone.utc)

        domain = email.split("@", 1)[1] if "@" in email else domain_hint
        registered_date = dt_local.strftime("%Y-%m-%d")
        registered_at = dt_utc.isoformat()
        token_path = str(fp)

        cur.execute("SELECT expired_at FROM staging WHERE email=?", (email,))
        row = cur.fetchone()
        do_update = True
        if row is not None:
            old = row["expired_at"]
            do_update = _parse_time_for_compare(expired) > _parse_time_for_compare(old)

        if row is None:
            cur.execute(
                """
                INSERT INTO staging (
                    email, domain, registrar, proxy_used,
                    registered_at, registered_date, token_path, expired_at,
                    status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now'))
                """,
                (email, domain, registrar_name, None, registered_at, registered_date, token_path, expired),
            )
            imported += 1
        elif do_update:
            cur.execute(
                """
                UPDATE staging
                   SET expired_at=?, token_path=?, registrar=?, domain=?, updated_at=datetime('now')
                 WHERE email=?
                """,
                (expired, token_path, registrar_name, domain, email),
            )
            imported += 1

    con.commit()
    return imported


def scan_and_import_all(con: sqlite3.Connection, cfg: dict) -> Dict[str, int]:
    result = {}
    for r in cfg["registrars"]:
        sdir = cfg["staging_dirs"].get(r.name) or os.path.join(cfg["staging_dir"], r.name)
        result[r.name] = scan_and_import(con, r.name, sdir, r.domain)
    return result


def get_today_count(con: sqlite3.Connection) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    row = con.execute(
        "SELECT COUNT(*) c FROM staging WHERE registered_date=? AND status!='dead'", (today,)
    ).fetchone()
    return int(row["c"] if row else 0)


def get_today_count_by_registrar(con: sqlite3.Connection) -> Dict[str, int]:
    today = datetime.now().strftime("%Y-%m-%d")
    rows = con.execute(
        """
        SELECT registrar, COUNT(*) c
          FROM staging
         WHERE registered_date=? AND status!='dead'
         GROUP BY registrar
        """,
        (today,),
    ).fetchall()
    m = {r["registrar"]: int(r["c"]) for r in rows}
    return m


def status_mode(con: sqlite3.Connection, cfg: dict, quota: int):
    today = datetime.now().strftime("%Y-%m-%d")
    by_reg = get_today_count_by_registrar(con)
    total = sum(by_reg.values())
    remaining = max(quota - total, 0)

    print(f"今日注册进度 ({today})")
    print("─" * 25)
    for r in cfg["registrars"]:
        print(f"{r.name:<7}: 已注册 {by_reg.get(r.name, 0)} 个")
    print("─" * 25)
    print(f"合计: {total} / {quota}")
    print(f"剩余配额: {remaining}")


def allocate_quota(registrars: List[Registrar], remaining: int) -> Dict[str, int]:
    total_weight = sum(max(1, r.weight) for r in registrars)
    alloc = {}
    used = 0
    for i, r in enumerate(registrars):
        if i == len(registrars) - 1:
            n = remaining - used
        else:
            n = (remaining * max(1, r.weight)) // total_weight
            used += n
        alloc[r.name] = max(0, n)

    # 余数给第一个registrar
    diff = remaining - sum(alloc.values())
    if registrars and diff > 0:
        alloc[registrars[0].name] += diff
    return alloc


def _count_json_files(path: str) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    return len(list(p.glob("*.json")))


def run_mode(con: sqlite3.Connection, cfg: dict, quota: int) -> int:
    scan_and_import_all(con, cfg)
    today_count = get_today_count(con)
    remaining = quota - today_count
    if remaining <= 0:
        print(f"[INFO] 今日配额已达成({today_count}/{quota})，退出")
        return EXIT_QUOTA_REACHED

    regs = cfg["registrars"]
    alloc = allocate_quota(regs, remaining)
    print(f"[INFO] 今日已注册 {today_count}/{quota}，剩余 {remaining}")
    print(f"[INFO] 配额分配: {alloc}")

    baseline = {}
    proc_map = {}
    started_at = now_utc_iso()

    for r in regs:
        sdir = cfg["staging_dirs"].get(r.name) or os.path.join(cfg["staging_dir"], r.name)
        baseline[r.name] = _count_json_files(sdir)
        if alloc.get(r.name, 0) <= 0:
            continue

        env = {**os.environ}
        env["TOTAL_ACCOUNTS"] = str(alloc[r.name])
        # 代理也传入环境变量
        proxy_val = cfg["env"].get(r.proxy_env, "")
        if proxy_val:
            env["PROXY"] = proxy_val
        cmd = ["python3", "chatgpt_register.py"]
        p = subprocess.Popen(cmd, cwd=r.dir, env=env, stdin=subprocess.PIPE)
        # 发送: 代理确认(Y), 账号数量(默认), 并发数(默认)
        p.stdin.write(b"Y\n\n\n")
        p.stdin.flush()
        proc_map[r.name] = p
        print(f"[INFO] 启动注册机 {r.name}: pid={p.pid}")

    if not proc_map:
        print("[INFO] 无需启动注册机")
        return EXIT_OK

    achieved = defaultdict(int)
    done = {name: False for name in proc_map}

    try:
        while True:
            all_done = True
            for name, p in list(proc_map.items()):
                target = alloc.get(name, 0)
                sdir = cfg["staging_dirs"].get(name) or os.path.join(cfg["staging_dir"], name)
                current = _count_json_files(sdir)
                new_count = max(0, current - baseline.get(name, 0))
                achieved[name] = new_count

                if not done[name] and new_count >= target and p.poll() is None:
                    print(f"[INFO] {name} 达到分配配额({new_count}/{target})，终止进程")
                    p.terminate()
                    try:
                        p.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.kill(p.pid, signal.SIGKILL)
                    done[name] = True

                if p.poll() is None:
                    all_done = False
                else:
                    done[name] = True

            if all_done:
                break

            # 每30秒扫描
            time.sleep(30)

    finally:
        for name, p in proc_map.items():
            if p.poll() is None:
                p.terminate()

    scan_and_import_all(con, cfg)
    finished_at = now_utc_iso()
    today = datetime.now().strftime("%Y-%m-%d")

    for r in regs:
        if r.name not in proc_map:
            continue
        con.execute(
            """
            INSERT INTO register_log (date, registrar, proxy, success, failed, started_at, finished_at, stop_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                today,
                r.name,
                cfg["env"].get(r.proxy_env, ""),
                int(achieved.get(r.name, 0)),
                0,
                started_at,
                finished_at,
                "quota_reached_or_process_exit",
            ),
        )
    con.commit()
    print("[INFO] 运行完成")
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--status", action="store_true")
    g.add_argument("--scan-only", action="store_true")
    g.add_argument("--run", action="store_true")
    parser.add_argument("--quota", type=int, default=None)
    args = parser.parse_args()

    try:
        cfg = load_config()
        quota = args.quota if args.quota is not None else int(cfg["daily_quota"])
        con = connect_db(cfg["db_path"])

        if args.status:
            status_mode(con, cfg, quota)
            return EXIT_OK
        if args.scan_only:
            res = scan_and_import_all(con, cfg)
            print("[INFO] 扫描完成:", res)
            return EXIT_OK
        if args.run:
            return run_mode(con, cfg, quota)

        return EXIT_ERROR
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
