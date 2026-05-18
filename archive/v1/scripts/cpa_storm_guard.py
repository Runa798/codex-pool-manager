#!/usr/bin/env python3
"""CLIProxyAPI auth-dir storm detector + protector.

Purpose
- Detect auth-dir watcher storms by counting auth file change events in `main.log`.
- Apply a *local, reversible* protection when storm is detected.

Why local protection
- Toggling remote management (e.g. allow-remote) does not necessarily stop the
  write-amplification source, because the storm may be caused by local code paths
  (auto-refresh / watcher / reload) and/or localhost management calls.

Protection strategy (default)
- Freeze auth-dir writes by removing write permission bits from:
  - auth directory (prevents create/rename/remove)
  - auth json files (prevents in-place writes)

This is fail-closed and reversible via `unprotect`.

Usage
  python3 cpa_storm_guard.py check
  python3 cpa_storm_guard.py daemon
  python3 cpa_storm_guard.py status
  python3 cpa_storm_guard.py protect
  python3 cpa_storm_guard.py unprotect

Config (env or defaults)
  CPA_MGMT_URL       default http://127.0.0.1:8317
  CPA_MGMT_KEY       optional; only used for optional mgmt toggles
  CPA_LOG_PATH       default ~/cliproxyapi_runtime/auths/logs/main.log
  CPA_AUTH_DIR       optional; default derived from CPA_LOG_PATH
  CPA_WINDOW_S       default 300 (5 minutes)
  CPA_THRESHOLD      default 30 (WRITE events per window)
  CPA_CALM_THRESHOLD default threshold//2
  CPA_COOLDOWN_S     default 900 (minimum protected time before auto-unprotect)
  CPA_SCAN_BYTES     default 2000000 (read tail bytes from log)

State
- Stored at /tmp/cpa_storm_guard_state.json (local-only).

- updated_at: 2026-03-01T23:50:00+08:00
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


DEFAULTS = {
    "mgmt_url": "http://127.0.0.1:8317",
    "log_path": os.path.expanduser("~/cliproxyapi_runtime/auths/logs/main.log"),
    "window_s": 300,
    "threshold": 30,
    "cooldown_s": 900,
    "scan_bytes": 2_000_000,
}

STATE_FILE = Path("/tmp/cpa_storm_guard_state.json")


@dataclass
class Counts:
    create: int
    write: int
    remove: int

    def total(self) -> int:
        return self.create + self.write + self.remove


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


def _read_counts(log_path: str, window_s: int, scan_bytes: int) -> Counts:
    ts_re = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
    authchg_re = re.compile(r"auth file changed \((CREATE|WRITE|REMOVE)\)")

    now = dt.datetime.now()
    start = now - dt.timedelta(seconds=window_s)

    counts = {"CREATE": 0, "WRITE": 0, "REMOVE": 0}

    p = Path(log_path)
    if not p.exists():
        return Counts(0, 0, 0)

    # Read tail only for speed
    with p.open("rb") as f:
        try:
            f.seek(-scan_bytes, 2)
        except OSError:
            f.seek(0)
        data = f.read().decode("utf-8", "replace")

    for line in data.splitlines():
        m = ts_re.match(line)
        if not m:
            continue
        ts = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        if ts < start:
            continue
        m2 = authchg_re.search(line)
        if m2:
            counts[m2.group(1)] += 1

    return Counts(create=counts["CREATE"], write=counts["WRITE"], remove=counts["REMOVE"])


def _mgmt_api_call(method: str, path: str, key: str, body: bytes | None = None) -> tuple[int, bytes]:
    url = f"{os.environ.get('CPA_MGMT_URL', DEFAULTS['mgmt_url'])}{path}"
    req = urllib.request.Request(url=url, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=body, timeout=10) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if hasattr(e, "read") else b""


def _try_set_allow_remote(key: str, value: bool) -> bool:
    # Best-effort: may not affect localhost writes.
    body = json.dumps({"value": bool(value)}).encode("utf-8")
    code, _ = _mgmt_api_call("PATCH", "/v0/management/allow-remote", key, body)
    return code in (200, 204)


def _derive_auth_dir(log_path: str) -> str:
    # Default layout: <auth-dir>/logs/main.log
    p = Path(log_path)
    if p.name == "main.log" and p.parent.name == "logs":
        return str(p.parent.parent)
    raise ValueError(f"cannot derive auth-dir from log path: {log_path}")


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"protected": False, "since": None}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"protected": False, "since": None}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=True, sort_keys=True))


def _oct_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _freeze_auth_dir(auth_dir: str) -> dict:
    """Make auth_dir and auth json files non-writable.

    Returns a dict with saved modes for rollback.
    """
    d = Path(auth_dir)
    if not d.exists() or not d.is_dir():
        raise FileNotFoundError(f"auth_dir not found or not a dir: {auth_dir}")

    saved = {"dir": _oct_mode(d), "files": {}}

    # Freeze directory first (prevents new file creation/rename/remove).
    os.chmod(d, saved["dir"] & ~0o222)

    # Freeze json files (prevents in-place writes).
    for fp in sorted(d.glob("*.json")):
        try:
            m = _oct_mode(fp)
            saved["files"][str(fp)] = m
            os.chmod(fp, m & ~0o222)
        except FileNotFoundError:
            # file might disappear between glob and chmod; ignore
            continue

    return saved


def _unfreeze_auth_dir(auth_dir: str, saved_modes: dict | None) -> None:
    d = Path(auth_dir)
    if not d.exists() or not d.is_dir():
        return

    if saved_modes and isinstance(saved_modes, dict):
        # Restore exact previous modes when available.
        try:
            if "dir" in saved_modes:
                os.chmod(d, int(saved_modes["dir"]))
        except Exception:
            pass

        files = saved_modes.get("files") if isinstance(saved_modes.get("files"), dict) else {}
        for p_str, mode in files.items():
            fp = Path(p_str)
            try:
                if fp.exists():
                    os.chmod(fp, int(mode))
            except Exception:
                continue
        return

    # Fallback: reasonable defaults.
    try:
        os.chmod(d, 0o755)
    except Exception:
        pass
    for fp in d.glob("*.json"):
        try:
            os.chmod(fp, 0o644)
        except Exception:
            continue


def cmd_check(args) -> int:
    window = _env_int("CPA_WINDOW_S", DEFAULTS["window_s"])
    threshold = _env_int("CPA_THRESHOLD", DEFAULTS["threshold"])
    log_path = os.environ.get("CPA_LOG_PATH", DEFAULTS["log_path"])
    scan_bytes = _env_int("CPA_SCAN_BYTES", DEFAULTS["scan_bytes"])

    c = _read_counts(log_path, window, scan_bytes)
    print(f"window_s={window} threshold={threshold} scan_bytes={scan_bytes}")
    print(f"counts: CREATE={c.create} WRITE={c.write} REMOVE={c.remove} total={c.total()}")

    if c.write >= threshold:
        print("STATUS: STORM_DETECTED")
        return 1
    print("STATUS: CALM")
    return 0


def cmd_protect(args) -> int:
    log_path = os.environ.get("CPA_LOG_PATH", DEFAULTS["log_path"])
    auth_dir = os.environ.get("CPA_AUTH_DIR") or _derive_auth_dir(log_path)

    state = _load_state()
    if state.get("protected"):
        print("NO-OP: already protected")
        return 0

    ts = dt.datetime.now().isoformat(timespec="seconds")
    saved = _freeze_auth_dir(auth_dir)

    state = {
        "protected": True,
        "since": ts,
        "mode": "freeze_auth_dir",
        "auth_dir": auth_dir,
        "saved_modes": saved,
    }

    # Optional best-effort mgmt toggle
    key = os.environ.get("CPA_MGMT_KEY", "")
    if key:
        state["mgmt_allow_remote_disabled"] = _try_set_allow_remote(key, False)

    _save_state(state)
    print(f"PROTECTED: auth-dir frozen ({auth_dir})")
    return 0


def cmd_unprotect(args) -> int:
    state = _load_state()
    if not state.get("protected"):
        print("NO-OP: not protected")
        return 0

    auth_dir = state.get("auth_dir") or os.environ.get("CPA_AUTH_DIR")
    if not auth_dir:
        print("ERROR: missing auth_dir in state and CPA_AUTH_DIR not set", file=sys.stderr)
        return 2

    _unfreeze_auth_dir(auth_dir, state.get("saved_modes"))

    # Optional best-effort mgmt toggle
    key = os.environ.get("CPA_MGMT_KEY", "")
    if key:
        _try_set_allow_remote(key, True)

    _save_state({"protected": False, "since": None, "auth_dir": auth_dir})
    print(f"UNPROTECTED: auth-dir unfrozen ({auth_dir})")
    return 0


def cmd_daemon(args) -> int:
    interval_s = 60

    window = _env_int("CPA_WINDOW_S", DEFAULTS["window_s"])
    threshold = _env_int("CPA_THRESHOLD", DEFAULTS["threshold"])
    calm_threshold = _env_int("CPA_CALM_THRESHOLD", max(1, threshold // 2))
    cooldown_s = _env_int("CPA_COOLDOWN_S", DEFAULTS["cooldown_s"])
    scan_bytes = _env_int("CPA_SCAN_BYTES", DEFAULTS["scan_bytes"])

    log_path = os.environ.get("CPA_LOG_PATH", DEFAULTS["log_path"])

    print(
        "Daemon started: "
        f"interval={interval_s}s window={window}s threshold={threshold} "
        f"calm_threshold={calm_threshold} cooldown_s={cooldown_s}"
    )

    while True:
        c = _read_counts(log_path, window, scan_bytes)
        ts = dt.datetime.now().isoformat(timespec="seconds")

        state = _load_state()
        protected = bool(state.get("protected"))

        since = None
        if state.get("since"):
            try:
                since = dt.datetime.fromisoformat(state["since"])
            except Exception:
                since = None

        in_cooldown = False
        if protected and since:
            in_cooldown = (dt.datetime.now() - since).total_seconds() < cooldown_s

        if c.write >= threshold and not protected:
            print(f"[{ts}] STORM DETECTED (WRITE={c.write}), protecting...")
            rc = cmd_protect(args)
            if rc != 0:
                print(f"[{ts}] FAILED to protect (rc={rc})")
        elif protected and (not in_cooldown) and c.write < calm_threshold:
            print(f"[{ts}] CALM (WRITE={c.write}) and cooldown passed, unprotecting...")
            rc = cmd_unprotect(args)
            if rc != 0:
                print(f"[{ts}] FAILED to unprotect (rc={rc})")
        else:
            status = "PROTECTED" if protected else "CALM"
            extra = " cooldown" if (protected and in_cooldown) else ""
            print(f"[{ts}] {status}{extra} WRITE={c.write}")

        time.sleep(interval_s)


def cmd_status(args) -> int:
    window = _env_int("CPA_WINDOW_S", DEFAULTS["window_s"])
    threshold = _env_int("CPA_THRESHOLD", DEFAULTS["threshold"])
    scan_bytes = _env_int("CPA_SCAN_BYTES", DEFAULTS["scan_bytes"])

    log_path = os.environ.get("CPA_LOG_PATH", DEFAULTS["log_path"])
    auth_dir = os.environ.get("CPA_AUTH_DIR")
    if not auth_dir:
        try:
            auth_dir = _derive_auth_dir(log_path)
        except Exception:
            auth_dir = None

    c = _read_counts(log_path, window, scan_bytes)
    print(f"counts(window_s={window}, threshold={threshold}): WRITE={c.write} CREATE={c.create} REMOVE={c.remove}")

    state = _load_state()
    print(f"local_state: protected={state.get('protected')}, since={state.get('since')}, mode={state.get('mode')}")

    if auth_dir:
        d = Path(auth_dir)
        if d.exists():
            print(f"auth_dir={auth_dir} mode={oct(_oct_mode(d))}")
        else:
            print(f"auth_dir={auth_dir} (missing)")

    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("check", help="Single check, exit 1 if storm")
    sp.add_parser("daemon", help="Run continuous protection")
    sp.add_parser("status", help="Show current state")
    sp.add_parser("protect", help="Manually protect (freeze auth-dir)")
    sp.add_parser("unprotect", help="Manual unprotect")
    args = ap.parse_args()

    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "daemon":
        return cmd_daemon(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "protect":
        return cmd_protect(args)
    if args.cmd == "unprotect":
        return cmd_unprotect(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
