#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
case "$(uname -s):$(uname -m)" in
  Darwin:arm64|Darwin:aarch64) RUNTIME_DIR="macos-arm64" ;;
  Darwin:x86_64|Darwin:amd64) RUNTIME_DIR="macos-x64" ;;
  Linux:x86_64|Linux:amd64) RUNTIME_DIR="linux-x64" ;;
  *) RUNTIME_DIR="" ;;
esac
PYTHON="$SCRIPT_DIR/runtime/$RUNTIME_DIR/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3 || true)"
fi
if [[ -z "$PYTHON" ]]; then
  echo "未找到匹配的内置 Python 运行时，也未找到系统 python3。"
  exit 1
fi
exec "$PYTHON" "$SCRIPT_DIR/apione_batch_gui.py"
