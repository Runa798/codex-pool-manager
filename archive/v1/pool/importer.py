#!/usr/bin/env python3
import json
from pathlib import Path
from typing import Any

import reservoir


def import_to_reservoir(
    email: str,
    password: str,
    access_token: str,
    refresh_token: str = "",
    id_token: str = "",
    account_id: str = "",
    expired_at: str = "",
) -> bool:
    return reservoir.add_account(
        email=email,
        password=password,
        access_token=access_token,
        refresh_token=refresh_token,
        id_token=id_token,
        account_id=account_id,
        expired_at=expired_at,
    )


def _read_auth(auths_dir: Path, email: str) -> dict[str, Any] | None:
    direct = auths_dir / f"{email}.json"
    candidates = [direct] if direct.exists() else []
    if not candidates:
        candidates = list(auths_dir.glob("*.json"))

    for p in candidates:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if str(data.get("email", "")).strip() == email or p.stem == email:
            return data
    return None


def import_from_registered_accounts_txt(filepath: str, auths_dir: str) -> dict[str, int]:
    src = Path(filepath)
    ad = Path(auths_dir)
    if not src.exists():
        raise FileNotFoundError(filepath)

    total = 0
    imported = 0
    missing_auth = 0
    duplicated = 0

    for line in src.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("----")
        if len(parts) < 2:
            continue

        total += 1
        email = parts[0].strip()
        chatgpt_password = parts[1].strip()

        auth = _read_auth(ad, email)
        if not auth:
            missing_auth += 1
            continue

        ok = reservoir.add_account(
            email=email,
            password=chatgpt_password,
            access_token=auth.get("access_token", ""),
            refresh_token=auth.get("refresh_token", ""),
            id_token=auth.get("id_token", ""),
            account_id=auth.get("account_id", ""),
            expired_at=auth.get("expired", ""),
        )
        if ok:
            imported += 1
        else:
            duplicated += 1

    return {
        "total": total,
        "imported": imported,
        "missing_auth": missing_auth,
        "duplicated": duplicated,
    }
