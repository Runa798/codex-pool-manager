#!/usr/bin/env python3
import json
import subprocess
import tempfile
import textwrap
from typing import Any

from config import CHATGPT_WEB_CLIENT_ID, PROXY, REFRESH_PYTHON, TOKEN_ENDPOINT


def refresh_via_token(account: dict[str, Any]) -> dict[str, Any] | None:
    """Run curl_cffi in an external python process (nohup) to avoid WSL TLS issues."""
    refresh_token = str(account.get("refresh_token", "")).strip()
    if not refresh_token:
        return None

    script = textwrap.dedent(
        f"""
        import json
        import sys
        from curl_cffi import requests as cf_requests

        proxy = sys.argv[1]
        refresh_token = sys.argv[2]

        s = cf_requests.Session(
            impersonate="chrome131",
            proxies={{"https": proxy, "http": proxy}},
        )
        r = s.post(
            {TOKEN_ENDPOINT!r},
            data={{
                "grant_type": "refresh_token",
                "client_id": {CHATGPT_WEB_CLIENT_ID!r},
                "refresh_token": refresh_token,
            }},
            headers={{"Content-Type": "application/x-www-form-urlencoded"}},
            timeout=20,
        )

        out = {{"status": r.status_code, "body": r.text[:500]}}
        try:
            data = r.json()
            if isinstance(data, dict):
                out["json"] = data
        except Exception:
            pass
        print(json.dumps(out, ensure_ascii=False))
        """
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f_script:
        f_script.write(script)
        script_path = f_script.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f_out:
        output_path = f_out.name

    cmd = (
        f"nohup {REFRESH_PYTHON} {script_path} {PROXY} {refresh_token} > {output_path} 2>/dev/null"
    )
    try:
        proc = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=35)
        if proc.returncode != 0:
            return None

        raw = ""
        with open(output_path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read().strip()
        if not raw:
            return None

        parsed = json.loads(raw)
        body = str(parsed.get("body", ""))
        data = parsed.get("json")
        if isinstance(data, dict) and data.get("access_token"):
            return {
                "access_token": data.get("access_token", ""),
                "refresh_token": data.get("refresh_token", ""),
                "id_token": data.get("id_token", ""),
                "expired_at": data.get("expires_at") or data.get("expired") or "",
            }
        if "access_token" in body:
            return None
        return None
    except Exception:
        return None


def refresh_via_relogin(account: dict[str, Any]) -> dict[str, Any] | None:
    """Fallback placeholder for email+password relogin flow."""
    if not account.get("password"):
        return None
    return None
