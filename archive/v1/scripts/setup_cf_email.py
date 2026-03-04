#!/usr/bin/env python3
import sys
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    with (ROOT / "config.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def cf_headers(cfg: dict) -> dict:
    cf = cfg.get("cloudflare", {})
    return {
        "X-Auth-Email": cf.get("email", ""),
        "X-Auth-Key": cf.get("api_key", ""),
        "Content-Type": "application/json",
    }


def get_zone_id(domain: str, headers: dict) -> str:
    r = requests.get(
        "https://api.cloudflare.com/client/v4/zones",
        params={"name": domain},
        headers=headers,
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    result = data.get("result", [])
    if not result:
        raise RuntimeError(f"zone not found: {domain}")
    return result[0]["id"]


def enable_routing(zone_id: str, headers: dict) -> None:
    r = requests.post(
        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/email/routing/enable",
        headers=headers,
        timeout=20,
    )
    r.raise_for_status()


def setup_catch_all(zone_id: str, worker_url: str, headers: dict) -> None:
    payload = {
        "name": "catch-all",
        "enabled": True,
        "matchers": [],
        "actions": [{"type": "worker", "value": worker_url}],
    }
    r = requests.put(
        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/email/routing/rules/catch_all",
        headers=headers,
        json=payload,
        timeout=20,
    )
    r.raise_for_status()


def main() -> int:
    cfg = load_config()
    domains = [d for d in (cfg.get("mail", {}).get("domains") or []) if d]
    worker_url = cfg.get("mail", {}).get("cf_worker_url", "")
    headers = cf_headers(cfg)

    if not domains:
        print("mail.domains 为空")
        return 1
    if not worker_url:
        print("mail.cf_worker_url 为空")
        return 1
    if not headers["X-Auth-Email"] or not headers["X-Auth-Key"]:
        print("cloudflare.email/api_key 未配置")
        return 1

    for domain in domains:
        try:
            zone_id = get_zone_id(domain, headers)
            enable_routing(zone_id, headers)
            setup_catch_all(zone_id, worker_url, headers)
            print(f"[OK] {domain} -> {worker_url}")
        except Exception as exc:
            print(f"[FAIL] {domain}: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
