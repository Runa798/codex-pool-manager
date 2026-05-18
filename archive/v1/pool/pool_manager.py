#!/usr/bin/env python3
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import CPA_AUTHS_DIR, POOL_MAX


def _auth_dir() -> Path:
    p = Path(CPA_AUTHS_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _json_files() -> list[Path]:
    return sorted(_auth_dir().glob("*.json"))


def _parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def count_active() -> int:
    return len(_json_files())


def get_all_accounts() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for p in _json_files():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("email", p.stem)
                items.append(data)
        except Exception:
            continue
    return items


def import_account(account_dict: dict[str, Any]) -> bool:
    if count_active() >= POOL_MAX:
        return False

    email = str(account_dict.get("email", "")).strip()
    if not email:
        return False

    p = _auth_dir() / f"{email}.json"
    if p.exists():
        return False

    payload = {
        "type": "codex",
        "email": email,
        "expired": account_dict.get("expired_at") or account_dict.get("expired") or "",
        "access_token": account_dict.get("access_token", ""),
        "refresh_token": account_dict.get("refresh_token", ""),
        "id_token": account_dict.get("id_token", ""),
        "account_id": account_dict.get("account_id", ""),
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def remove_account(email: str) -> None:
    p = _auth_dir() / f"{email}.json"
    if p.exists():
        p.unlink()


def get_expiring_soon(days: int = 2) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    threshold = now + timedelta(days=days)
    result: list[dict[str, Any]] = []
    for account in get_all_accounts():
        exp = _parse_time(account.get("expired"))
        if exp and exp <= threshold:
            result.append(account)
    return result


def get_expired() -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    result: list[dict[str, Any]] = []
    for account in get_all_accounts():
        exp = _parse_time(account.get("expired"))
        if exp and exp <= now:
            result.append(account)
    return result


def update_tokens(email: str, new_tokens: dict[str, Any]) -> bool:
    p = _auth_dir() / f"{email}.json"
    if not p.exists():
        return False

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False

    if new_tokens.get("access_token"):
        data["access_token"] = new_tokens["access_token"]
    if new_tokens.get("refresh_token"):
        data["refresh_token"] = new_tokens["refresh_token"]
    if new_tokens.get("id_token"):
        data["id_token"] = new_tokens["id_token"]

    expired = new_tokens.get("expired_at") or new_tokens.get("expired")
    if expired:
        data["expired"] = expired

    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return True
