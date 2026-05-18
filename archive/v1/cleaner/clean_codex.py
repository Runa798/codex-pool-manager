#!/usr/bin/env python3
"""Clean invalid codex auth files via CPA management API."""

import argparse
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

API_URL = ""
HEADERS = {}


def init_config(url: str, key: str):
    global API_URL, HEADERS
    API_URL = url.rstrip("/")
    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {key}",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


def get_auth_files():
    """Fetch all auth files from CPA."""
    resp = requests.get(f"{API_URL}/v0/management/auth-files", headers=HEADERS)
    resp.raise_for_status()
    return resp.json().get("files", [])


def check_quota(file_info: dict) -> dict:
    """Check quota for a single auth file, return result dict."""
    auth_index = file_info["auth_index"]
    account_id = file_info.get("id_token", {}).get("chatgpt_account_id", "")
    file_id = file_info["id"]

    payload = {
        "authIndex": auth_index,
        "method": "GET",
        "url": "https://chatgpt.com/backend-api/wham/usage",
        "header": {
            "Authorization": "Bearer $TOKEN$",
            "Content-Type": "application/json",
            "User-Agent": "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal",
            "Chatgpt-Account-Id": account_id,
        },
    }
    hdrs = {**HEADERS, "Content-Type": "application/json"}
    try:
        resp = requests.post(
            f"{API_URL}/v0/management/api-call", headers=hdrs, json=payload, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        status_code = data.get("status_code", -1)
        body = data.get("body", "")
        log.info("ID=%s  status_code=%s  body=%s", file_id, status_code, body)
        return {"id": file_id, "status_code": status_code, "body": body}
    except Exception as exc:
        log.error("ID=%s  error=%s", file_id, exc)
        return {"id": file_id, "status_code": -1, "body": str(exc)}


def disable_file(file_id: str) -> bool:
    """Disable an auth file by its id (name). Returns True on success."""
    hdrs = {**HEADERS, "Content-Type": "application/json"}
    payload = {"name": file_id, "disabled": True}
    try:
        resp = requests.patch(
            f"{API_URL}/v0/management/auth-files/status", headers=hdrs, json=payload
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "ok":
            log.warning("Disable %s unexpected response: %s", file_id, data)
            return False
        log.info("Disabled: %s", file_id)
        return True
    except Exception as exc:
        log.error("Disable %s failed: %s", file_id, exc)
        return False


def delete_file(file_id: str) -> bool:
    """Delete an auth file by its id (name). Returns True on success."""
    try:
        resp = requests.delete(
            f"{API_URL}/v0/management/auth-files",
            headers=HEADERS,
            params={"name": file_id},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "ok":
            log.warning("Delete %s unexpected response: %s", file_id, data)
            return False
        log.info("Deleted: %s", file_id)
        return True
    except Exception as exc:
        log.error("Delete %s failed: %s", file_id, exc)
        return False


# ---- Quota plugin (independent of existing check/delete logic) ----
def quota_enable_file(file_id: str) -> bool:
    hdrs = {**HEADERS, "Content-Type": "application/json"}
    payload = {"name": file_id, "disabled": False}
    try:
        resp = requests.patch(
            f"{API_URL}/v0/management/auth-files/status", headers=hdrs, json=payload
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "ok":
            log.warning("Enable %s unexpected response: %s", file_id, data)
            return False
        log.info("Enabled: %s", file_id)
        return True
    except Exception as exc:
        log.error("Enable %s failed: %s", file_id, exc)
        return False


def quota_parse_usage_body(body):
    if isinstance(body, dict):
        return body
    if isinstance(body, str):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None
    return None


def quota_is_exhausted(usage_obj: dict) -> bool:
    if not isinstance(usage_obj, dict):
        return False
    rate_limit = usage_obj.get("rate_limit")
    if not isinstance(rate_limit, dict):
        return False

    if rate_limit.get("limit_reached") is True:
        return True

    primary = rate_limit.get("primary_window")
    if isinstance(primary, dict):
        used_percent = primary.get("used_percent", 0)
        try:
            if float(used_percent) >= 100:
                return True
        except (TypeError, ValueError):
            pass

    secondary = rate_limit.get("secondary_window")
    if isinstance(secondary, dict) and secondary.get("limit_reached") is True:
        return True

    return False


def quota_update_marker(path: str, set_disabled: bool) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as f:
            auth_obj = json.load(f)
    except Exception as exc:
        log.error("Read auth json failed: %s (%s)", path, exc)
        return False

    changed = False
    if set_disabled:
        if auth_obj.get("quota_disabled") is not True:
            auth_obj["quota_disabled"] = True
            changed = True
    else:
        if "quota_disabled" in auth_obj:
            del auth_obj["quota_disabled"]
            changed = True

    if not changed:
        return True

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(auth_obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return True
    except Exception as exc:
        log.error("Write auth json failed: %s (%s)", path, exc)
        return False


def cmd_check_quota(args):
    files = get_auth_files()
    codex_files = [
        f for f in files if f.get("provider") == "codex" and not f.get("disabled")
    ]
    log.info("Found %d active codex auth files (skipped disabled)", len(codex_files))

    if not codex_files:
        log.info("Done. checked=0, exhausted=0, disabled=0")
        return

    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(check_quota, f): f for f in codex_files}
        for future in as_completed(futures):
            results.append((futures[future], future.result()))

    exhausted = []
    for file_info, result in results:
        if result.get("status_code") != 200:
            continue
        usage_obj = quota_parse_usage_body(result.get("body"))
        if quota_is_exhausted(usage_obj):
            exhausted.append((file_info, result))

    disabled_count = 0
    for file_info, result in exhausted:
        file_id = result["id"]
        auth_path = file_info.get("path", "")
        if not auth_path:
            log.error("ID=%s missing path, skip", file_id)
            continue

        if disable_file(file_id) and quota_update_marker(auth_path, True):
            disabled_count += 1

    log.info(
        "Done. checked=%d, exhausted=%d, disabled=%d",
        len(results),
        len(exhausted),
        disabled_count,
    )


def cmd_restore_quota(args):
    files = get_auth_files()
    targets = [
        f for f in files if f.get("provider") == "codex" and f.get("disabled") is True
    ]
    log.info("Found %d disabled codex files", len(targets))

    restored_count = 0
    for file_info in targets:
        file_id = file_info["id"]
        auth_path = file_info.get("path", "")
        if not auth_path:
            log.error("ID=%s missing path, skip", file_id)
            continue

        try:
            with open(auth_path, "r", encoding="utf-8") as f:
                auth_obj = json.load(f)
        except Exception as exc:
            log.error("Read auth json failed: %s (%s)", auth_path, exc)
            continue

        if auth_obj.get("quota_disabled") is not True:
            continue

        if quota_enable_file(file_id) and quota_update_marker(auth_path, False):
            restored_count += 1

    log.info("Done. checked=%d, restored=%d", len(targets), restored_count)


def cmd_check(args):
    """Default mode: check quota and disable 401 files."""
    files = get_auth_files()
    codex_files = [
        f for f in files
        if f.get("provider") == "codex" and not f.get("disabled")
    ]
    log.info("Found %d active codex auth files (skipped disabled)", len(codex_files))

    if not codex_files:
        return

    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(check_quota, f): f for f in codex_files}
        for future in as_completed(futures):
            results.append(future.result())

    invalid = [r for r in results if r["status_code"] == 401]
    log.info("Found %d files with 401, disabling...", len(invalid))
    disabled_count = 0
    for r in invalid:
        if disable_file(r["id"]):
            disabled_count += 1

    log.info("Done. checked=%d, found_401=%d, disabled=%d", len(results), len(invalid), disabled_count)


def cmd_delete(args):
    """Delete mode: remove disabled codex files."""
    files = get_auth_files()
    targets = [
        f
        for f in files
        if f.get("provider") == "codex" and f.get("disabled") is True
    ]
    log.info("Found %d disabled codex files to delete", len(targets))

    deleted_count = 0
    for f in targets:
        if delete_file(f["id"]):
            deleted_count += 1

    log.info("Done. found=%d, deleted=%d", len(targets), deleted_count)


def main():
    parser = argparse.ArgumentParser(description="Clean invalid codex auth files")
    parser.add_argument("--url", required=True, help="CPA API URL (e.g. http://localahost:4001)")
    parser.add_argument("--key", required=True, help="CPA admin key")

    sub = parser.add_subparsers(dest="command")

    check_p = sub.add_parser("check", help="Check quota and disable 401 files (default)")
    check_p.add_argument(
        "-c", "--concurrency", type=int, default=20, help="Concurrent workers (default: 20)"
    )

    sub.add_parser("delete", help="Delete disabled & unavailable codex files")

    check_quota_p = sub.add_parser(
        "check-quota", help="Check quota and disable exhausted accounts"
    )
    check_quota_p.add_argument(
        "-c", "--concurrency", type=int, default=10, help="Concurrent workers (default: 10)"
    )

    sub.add_parser("restore-quota", help="Restore quota-disabled accounts")

    args = parser.parse_args()
    init_config(args.url, args.key)

    if args.command == "delete":
        cmd_delete(args)
    elif args.command == "check-quota":
        cmd_check_quota(args)
    elif args.command == "restore-quota":
        cmd_restore_quota(args)
    else:
        if not hasattr(args, "concurrency"):
            args.concurrency = 20
        cmd_check(args)


if __name__ == "__main__":
    main()
