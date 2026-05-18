#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_yaml(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def apply_profile(cfg: dict, profile: dict) -> dict:
    assets = profile.get("assets", {})
    cfg.setdefault("mail", {})
    cfg.setdefault("capsolver", {})
    cfg.setdefault("proxy", {})
    cfg.setdefault("cpa", {})
    cfg.setdefault("register", {})

    cfg["mail"]["provider"] = "cf_worker"
    cfg["mail"]["cf_worker_url"] = assets.get("cf_worker_url", "")
    cfg["mail"]["domains"] = assets.get("domains", [])
    cfg["capsolver"]["api_key"] = assets.get("capsolver_api_key", "")
    cfg["proxy"]["http"] = assets.get("proxy_http", "")
    cfg["cpa"]["url"] = assets.get("cpa_url", cfg["cpa"].get("url", ""))
    cfg["cpa"]["api_key"] = assets.get("cpa_api_key", "")

    cfg["register"]["daily_limit_per_domain"] = 50
    cfg["register"]["workers"] = 2

    return cfg


def upload_one(cpa_url: str, cpa_key: str, account: dict, filename: str) -> bool:
    headers = {"Authorization": f"Bearer {cpa_key}"}
    payload = {
        "name": filename,
        "provider": "codex",
        "content": json.dumps(account, ensure_ascii=False),
    }
    endpoints = [
        f"{cpa_url.rstrip('/')}/v0/management/auth-files/upload",
        f"{cpa_url.rstrip('/')}/v0/management/auth-files",
    ]
    for ep in endpoints:
        try:
            r = requests.post(ep, headers=headers, json=payload, timeout=20)
            if r.status_code < 300:
                return True
        except Exception:
            continue
    return False


def import_seed_accounts(cfg: dict, seed_dir: Path) -> tuple[int, int]:
    cpa_url = cfg.get("cpa", {}).get("url", "")
    cpa_key = cfg.get("cpa", {}).get("api_key", "")
    if not cpa_url or not cpa_key:
        return 0, 0

    files = sorted(seed_dir.glob("*.json"))
    total = len(files)
    ok = 0
    for fp in files:
        try:
            account = json.loads(fp.read_text(encoding="utf-8"))
            if upload_one(cpa_url, cpa_key, account, fp.stem):
                ok += 1
        except Exception:
            continue
    return total, ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Preset one-click setup")
    parser.add_argument("--profile", required=True, help="preset-profile.yaml path")
    args = parser.parse_args()

    profile_path = Path(args.profile)
    if not profile_path.is_absolute():
        profile_path = (ROOT / profile_path).resolve()
    if not profile_path.exists():
        print(f"profile not found: {profile_path}")
        return 1

    profile = load_yaml(profile_path)
    cfg_path = ROOT / "config.yaml"
    cfg = load_yaml(cfg_path)
    cfg = apply_profile(cfg, profile)
    save_yaml(cfg_path, cfg)

    seed_dir = profile.get("seed_accounts_dir", "./preset/seed_accounts/")
    seed_path = Path(seed_dir)
    if not seed_path.is_absolute():
        seed_path = (ROOT / seed_path).resolve()
    total, ok = import_seed_accounts(cfg, seed_path)

    print("=== Preset Setup Summary ===")
    print(f"profile: {profile_path}")
    print(f"domains: {cfg.get('mail', {}).get('domains', [])}")
    print("forced register.daily_limit_per_domain: 50")
    print("forced register.workers: 2")
    print(f"seed_accounts: imported {ok}/{total}")
    print("config.yaml updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
