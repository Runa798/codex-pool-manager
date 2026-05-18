import os

import yaml


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load() -> dict:
    base = _project_root()
    with open(os.path.join(base, "config.yaml"), encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


_CFG = _load()
_ROOT = _project_root()

CPA_AUTHS_DIR = os.path.expanduser(
    _CFG.get("cpa", {}).get("auths_dir", "~/cliproxyapi_runtime/auths")
)
RESERVOIR_DB = os.path.join(_ROOT, "data", "reservoir.db")
POOL_MAX = _CFG.get("pool", {}).get("max", 388)
POOL_MIN = _CFG.get("pool", {}).get("min", 350)
PROXY = _CFG.get("proxy", {}).get("http", "")
LOG_FILE = os.path.join(_ROOT, "logs", "scheduler.log")
CHATGPT_WEB_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
TOKEN_ENDPOINT = "https://auth.openai.com/oauth/token"
REGISTERED_ACCOUNTS_TXT = os.path.join(_ROOT, "data", "registered_accounts.txt")
REFRESH_PYTHON = os.environ.get("REFRESH_PYTHON", "python3")
