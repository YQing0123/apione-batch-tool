#!/bin/zsh
# APIOne 便携式 Python 启动器：双击运行，不依赖当前终端目录。
set -e
SCRIPT_DIR="${0:A:h}"
ARCH="$(uname -m)"
if [[ "$ARCH" == "arm64" || "$ARCH" == "aarch64" ]]; then
  RUNTIME="$SCRIPT_DIR/runtime/macos-arm64/bin/python3"
elif [[ "$ARCH" == "x86_64" || "$ARCH" == "amd64" ]]; then
  RUNTIME="$SCRIPT_DIR/runtime/macos-x64/bin/python3"
else
  RUNTIME=""
fi
if [[ ! -x "$RUNTIME" ]]; then
  RUNTIME="$(command -v python3 || true)"
fi
if [[ -z "$RUNTIME" ]]; then
  echo "未找到内置 Python 运行时，也未找到系统 python3。"
  read -r "?按回车键退出..."
  exit 1
fi
cd "$SCRIPT_DIR"
exec "$RUNTIME" "$SCRIPT_DIR/apione_batch_gui.py"
