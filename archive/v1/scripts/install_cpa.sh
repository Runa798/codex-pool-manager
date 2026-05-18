#!/bin/bash
set -euo pipefail

# 从 GitHub 下载 CLIProxyAPI 最新 release
REPO="router-for-me/CLIProxyAPI"
INSTALL_DIR="./cpa"
TMP_DIR="$(mktemp -d)"

cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

ARCH="$(uname -m)"
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"

case "$ARCH" in
  x86_64|amd64) ARCH_TAG="amd64" ;;
  aarch64|arm64) ARCH_TAG="arm64" ;;
  *) echo "Unsupported architecture: $ARCH"; exit 1 ;;
esac

ASSET_PATTERN="${OS}.*${ARCH_TAG}|${ARCH_TAG}.*${OS}"

echo "Fetching latest release metadata for ${REPO}..."
JSON="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest")"
URL="$(echo "$JSON" | grep -Eo '"browser_download_url":\s*"[^"]+"' | cut -d '"' -f4 | grep -Ei "$ASSET_PATTERN" | head -n1)"

if [ -z "$URL" ]; then
  echo "Could not find matching release asset for ${OS}/${ARCH_TAG}."
  exit 1
fi

mkdir -p "$INSTALL_DIR"
ASSET="$TMP_DIR/asset"

curl -fL "$URL" -o "$ASSET"

if file "$ASSET" | grep -qi 'zip archive'; then
  unzip -o "$ASSET" -d "$INSTALL_DIR" >/dev/null
elif file "$ASSET" | grep -qi 'gzip compressed'; then
  tar -xzf "$ASSET" -C "$INSTALL_DIR"
else
  chmod +x "$ASSET"
  cp "$ASSET" "$INSTALL_DIR/cliproxyapi"
fi

if [ ! -f "$INSTALL_DIR/config.yaml" ]; then
  cat > "$INSTALL_DIR/config.yaml" <<'YAML'
server:
  listen: 0.0.0.0:8317
runtime:
  auths_dir: ./runtime/auths
YAML
fi

mkdir -p "$INSTALL_DIR/runtime/auths"
echo "CPA installed into ${INSTALL_DIR}"
