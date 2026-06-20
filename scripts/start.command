#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
ROOT_DIR="${SCRIPT_DIR:h}"

cd "${ROOT_DIR}"

if command -v python3 >/dev/null 2>&1; then
  python3 src/start_sodamusic_export.py "$@"
elif command -v python >/dev/null 2>&1; then
  python src/start_sodamusic_export.py "$@"
else
  echo "未找到 Python 3，请先安装 Python。"
  if [ -t 0 ]; then
    echo
    read -r "?按回车关闭..."
  fi
  exit 1
fi

if [ -t 0 ]; then
  echo
  read -r "?按回车关闭..."
fi
