#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_SRC="$ROOT_DIR/proxy/mihomo-codex.yaml"
CONFIG_DIR="$HOME/.config/mihomo-codex"
CONFIG_DST="$CONFIG_DIR/config.yaml"

if ! command -v mihomo >/dev/null 2>&1; then
  echo "mihomo 未安装，请先安装 mihomo 或使用包管理器安装。"
  echo "示例: sudo apt install mihomo (如仓库可用)"
  exit 1
fi

mkdir -p "$CONFIG_DIR"
cp "$CONFIG_SRC" "$CONFIG_DST"

SUB_URL="$(python3 - <<'PY'
import yaml
from pathlib import Path
cfg = yaml.safe_load(Path('config.yaml').read_text(encoding='utf-8')) or {}
print((cfg.get('proxy') or {}).get('subscription_url',''))
PY
)"

if [ -z "$SUB_URL" ]; then
  read -r -p "请输入订阅链接(可留空): " SUB_URL
fi

if [ -n "$SUB_URL" ]; then
  python3 - <<PY
from pathlib import Path
p = Path('$CONFIG_DST')
text = p.read_text(encoding='utf-8')
text = text.replace('url: ""', f'url: "{SUB_URL}"', 1)
p.write_text(text, encoding='utf-8')
PY
fi

sudo tee /etc/systemd/system/mihomo-codex.service >/dev/null <<EOF
[Unit]
Description=mihomo codex service
After=network.target

[Service]
Type=simple
ExecStart=$(command -v mihomo) -d $CONFIG_DIR -f $CONFIG_DST
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now mihomo-codex
sudo systemctl status mihomo-codex --no-pager || true
echo "mihomo 已配置，HTTP 代理默认 127.0.0.1:7894"
