#!/usr/bin/env python3
import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent


def load_config() -> dict:
    with (ROOT / "config.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def run_py(script: Path, args: list[str] | None = None, cwd: Path | None = None) -> int:
    cmd = [sys.executable, str(script)]
    if args:
        cmd.extend(args)
    proc = subprocess.run(cmd, cwd=str(cwd or ROOT))
    return proc.returncode


def run_sh(script: Path, args: list[str] | None = None) -> int:
    cmd = ["bash", str(script)]
    if args:
        cmd.extend(args)
    proc = subprocess.run(cmd, cwd=str(ROOT))
    return proc.returncode


def cmd_status(_: argparse.Namespace) -> int:
    cfg = load_config()
    cpa = cfg.get("cpa", {})
    capsolver = cfg.get("capsolver", {})
    if not cpa.get("api_key") or not capsolver.get("api_key"):
        print("⚠️  config.yaml 未配置，请先填写必填项后再运行。")
        print("    参考: config.yaml 中的注释说明")
        return 1
    return run_py(ROOT / "pool" / "scheduler.py", ["status"], cwd=ROOT / "pool")


def cmd_fill_pool(_: argparse.Namespace) -> int:
    return run_py(ROOT / "pool" / "scheduler.py", ["fill_pool"], cwd=ROOT / "pool")


def cmd_refresh(_: argparse.Namespace) -> int:
    return run_py(ROOT / "pool" / "scheduler.py", ["refresh_and_clean"], cwd=ROOT / "pool")


def cmd_check_quota(_: argparse.Namespace) -> int:
    cfg = load_config()
    cpa = cfg.get("cpa", {})
    if not cpa.get("url") or not cpa.get("api_key"):
        print("cpa.url / cpa.api_key 未配置")
        return 1
    return run_py(
        ROOT / "cleaner" / "clean_codex.py",
        ["--url", cpa["url"], "--key", cpa["api_key"], "check-quota"],
        cwd=ROOT,
    )


def cmd_restore_quota(_: argparse.Namespace) -> int:
    cfg = load_config()
    cpa = cfg.get("cpa", {})
    if not cpa.get("url") or not cpa.get("api_key"):
        print("cpa.url / cpa.api_key 未配置")
        return 1
    return run_py(
        ROOT / "cleaner" / "clean_codex.py",
        ["--url", cpa["url"], "--key", cpa["api_key"], "restore-quota"],
        cwd=ROOT,
    )


def cmd_clean(_: argparse.Namespace) -> int:
    cfg = load_config()
    cpa = cfg.get("cpa", {})
    if not cpa.get("url") or not cpa.get("api_key"):
        print("cpa.url / cpa.api_key 未配置")
        return 1
    rc1 = run_py(
        ROOT / "cleaner" / "clean_codex.py",
        ["--url", cpa["url"], "--key", cpa["api_key"], "check"],
        cwd=ROOT,
    )
    if rc1 != 0:
        return rc1
    return run_py(
        ROOT / "cleaner" / "clean_codex.py",
        ["--url", cpa["url"], "--key", cpa["api_key"], "delete"],
        cwd=ROOT,
    )


def _load_register_module():
    path = ROOT / "register" / "chatgpt_register.py"
    spec = importlib.util.spec_from_file_location("chatgpt_register", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def cmd_register(args: argparse.Namespace) -> int:
    cfg = load_config()
    register = cfg.get("register", {})
    mail = cfg.get("mail", {})
    proxy = cfg.get("proxy", {})
    capsolver = cfg.get("capsolver", {})

    domain = args.domain or (mail.get("domains", [""])[0] if mail.get("domains") else "")
    os.environ["TEMPMAIL_DOMAIN"] = domain
    os.environ["PROXY"] = proxy.get("http", "")
    os.environ.setdefault("CAPSOLVER_API_KEY", capsolver.get("api_key", ""))

    mod = _load_register_module()
    out = ROOT / "data" / "registered_accounts.txt"
    out.parent.mkdir(parents=True, exist_ok=True)

    total = int(register.get("batch_size", 100))
    workers = int(register.get("workers", 2))
    try:
        mod.run_batch(total_accounts=total, output_file=str(out), max_workers=workers, proxy=proxy.get("http", ""))
        return 0
    except Exception as exc:
        print(f"register failed: {exc}")
        return 1


def cmd_setup_cf(_: argparse.Namespace) -> int:
    return run_py(ROOT / "scripts" / "setup_cf_email.py", cwd=ROOT)


def cmd_install_cpa(_: argparse.Namespace) -> int:
    return run_sh(ROOT / "scripts" / "install_cpa.sh")


def cmd_setup_proxy(_: argparse.Namespace) -> int:
    return run_sh(ROOT / "scripts" / "setup_mihomo.sh")


def cmd_preset_setup(args: argparse.Namespace) -> int:
    profile = args.profile or str(ROOT / "preset" / "preset-profile.yaml")
    return run_py(ROOT / "preset" / "preset_setup.py", ["--profile", profile], cwd=ROOT)


def cmd_start_scheduler(_: argparse.Namespace) -> int:
    print("建议 crontab 守护以下任务：")
    print("*/5 * * * * cd /path/to/codex-pool-manager && python manage.py fill-pool >> logs/cron.log 2>&1")
    print("0 */6 * * * cd /path/to/codex-pool-manager && python manage.py refresh >> logs/cron.log 2>&1")
    print("0 2 * * * cd /path/to/codex-pool-manager && python manage.py clean >> logs/cron.log 2>&1")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="codex-pool-manager CLI")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status").set_defaults(func=cmd_status)
    r = sub.add_parser("register")
    r.add_argument("--domain", help="覆盖 mail.domains[0]")
    r.set_defaults(func=cmd_register)
    sub.add_parser("fill-pool").set_defaults(func=cmd_fill_pool)
    sub.add_parser("clean").set_defaults(func=cmd_clean)
    sub.add_parser("check-quota").set_defaults(func=cmd_check_quota)
    sub.add_parser("restore-quota").set_defaults(func=cmd_restore_quota)
    sub.add_parser("refresh").set_defaults(func=cmd_refresh)
    sub.add_parser("setup-cf").set_defaults(func=cmd_setup_cf)
    sub.add_parser("install-cpa").set_defaults(func=cmd_install_cpa)
    sub.add_parser("setup-proxy").set_defaults(func=cmd_setup_proxy)
    f = sub.add_parser("preset-setup")
    f.add_argument("--profile", help="预设配置 profile.yaml 路径")
    f.set_defaults(func=cmd_preset_setup)
    sub.add_parser("start-scheduler").set_defaults(func=cmd_start_scheduler)
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
