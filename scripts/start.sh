#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)

cd "${ROOT_DIR}"

if command -v python3 >/dev/null 2>&1; then
  exec python3 src/start_sodamusic_export.py "$@"
elif command -v python >/dev/null 2>&1; then
  exec python src/start_sodamusic_export.py "$@"
else
  echo "未找到 Python 3，请先安装 Python。" >&2
  exit 1
fi
