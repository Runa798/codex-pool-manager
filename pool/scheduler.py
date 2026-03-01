#!/usr/bin/env python3
import logging
import sys
from pathlib import Path

import pool_manager
import reservoir
from config import CPA_AUTHS_DIR, LOG_FILE, POOL_MAX, POOL_MIN, REGISTERED_ACCOUNTS_TXT
from token_refresher import refresh_via_relogin, refresh_via_token


def setup_logging() -> None:
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
    )


def parse_password_map(path: str) -> dict[str, str]:
    pw_map: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return pw_map

    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.strip().split("----")
        if len(parts) < 2:
            continue
        email = parts[0].strip()
        password = parts[1].strip()
        if email and password and email not in pw_map:
            pw_map[email] = password
    return pw_map


def cmd_status() -> None:
    active = pool_manager.count_active()
    available = reservoir.count_available()
    total = reservoir.count_all()
    expiring = len(pool_manager.get_expiring_soon(days=2))
    print(f"CPA号池: {active}/{POOL_MAX}")
    print(f"蓄水池可用: {available}")
    print(f"蓄水池总量: {total}")
    print(f"2天内过期: {expiring}")


def cmd_fill_pool() -> None:
    active = pool_manager.count_active()
    if active >= POOL_MIN:
        print(f"号池充足: {active} >= {POOL_MIN}")
        return

    need = min(POOL_MAX - active, POOL_MAX)
    candidates = reservoir.get_available(need)
    imported = 0

    for account in candidates:
        if pool_manager.count_active() >= POOL_MAX:
            break
        if pool_manager.import_account(account):
            reservoir.mark_imported(account["email"])
            imported += 1

    print(f"fill_pool: 当前{active}, 需要{need}, 成功补充{imported}, 结果{pool_manager.count_active()}")


def cmd_refresh_and_clean() -> None:
    expiring = pool_manager.get_expiring_soon(days=2)
    refreshed = 0
    failed: list[str] = []

    for account in expiring:
        email = str(account.get("email", "")).strip()
        if not email:
            continue

        tokens = refresh_via_token(account)
        if not tokens:
            tokens = refresh_via_relogin(account)

        if tokens:
            pool_manager.update_tokens(email, tokens)
            reservoir.update_tokens(email, tokens)
            refreshed += 1
        else:
            failed.append(email)

    expired = pool_manager.get_expired()
    to_remove = {str(a.get('email', '')).strip() for a in expired}
    to_remove.update(failed)
    to_remove.discard("")

    for email in to_remove:
        pool_manager.remove_account(email)
        reservoir.mark_dead(email)

    print(f"refresh_and_clean: 过期待刷新{len(expiring)}, 刷新成功{refreshed}, 清理{len(to_remove)}")
    cmd_fill_pool()


def cmd_import(email: str, password: str, access_token: str, refresh_token: str = "", id_token: str = "") -> None:
    ok = reservoir.add_account(
        email=email,
        password=password,
        access_token=access_token,
        refresh_token=refresh_token,
        id_token=id_token,
    )
    print(f"import {'ok' if ok else 'skip'}: {email}")


def cmd_bulk_import() -> None:
    auth_accounts = pool_manager.get_all_accounts()
    pw_map = parse_password_map(REGISTERED_ACCOUNTS_TXT)

    imported = 0
    skipped = 0
    for account in auth_accounts:
        email = str(account.get("email", "")).strip()
        if not email:
            continue

        ok = reservoir.add_account(
            email=email,
            password=pw_map.get(email, ""),
            access_token=account.get("access_token", ""),
            refresh_token=account.get("refresh_token", ""),
            id_token=account.get("id_token", ""),
            account_id=account.get("account_id", ""),
            expired_at=account.get("expired", ""),
        )
        if ok:
            imported += 1
        else:
            skipped += 1

    print(
        f"bulk_import done: auth_json={len(auth_accounts)}, imported={imported}, skipped={skipped}, password_matched={len(pw_map)}"
    )


def main(argv: list[str]) -> int:
    setup_logging()
    reservoir.init_db()
    Path(CPA_AUTHS_DIR).mkdir(parents=True, exist_ok=True)

    if len(argv) < 2:
        print("usage: scheduler.py [status|fill_pool|refresh_and_clean|import|bulk_import]")
        return 1

    cmd = argv[1]
    if cmd == "status":
        cmd_status()
    elif cmd == "fill_pool":
        cmd_fill_pool()
    elif cmd == "refresh_and_clean":
        cmd_refresh_and_clean()
    elif cmd == "import":
        if len(argv) < 5:
            print("usage: scheduler.py import <email> <password> <access_token> [refresh_token] [id_token]")
            return 1
        cmd_import(
            argv[2],
            argv[3],
            argv[4],
            argv[5] if len(argv) > 5 else "",
            argv[6] if len(argv) > 6 else "",
        )
    elif cmd == "bulk_import":
        cmd_bulk_import()
    else:
        print(f"unknown command: {cmd}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
