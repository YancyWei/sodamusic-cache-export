#!/usr/bin/env python3
"""Cross-platform launcher for the SodaMusic cache export web UI."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from runtime_dependencies import ensure_runtime_dependencies, ensure_tool_path


SRC_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_ROOT.parent
WEB_SCRIPT = SRC_ROOT / "sodamusic_export_web.py"
REQUIRED_WEB_API_VERSION = 6


def default_log_file() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Logs/SodaMusicCacheExport/web.log"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        return base / "SodaMusicCacheExport/logs/web.log"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "sodamusic-cache-export/web.log"


def python_executable() -> str:
    return sys.executable or "python3"


def is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def listener_pid(port: int) -> int | None:
    if os.name == "nt":
        return None
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line)
    return None


def process_command(pid: int) -> str:
    if os.name == "nt":
        return ""
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def is_own_web_process(command: str, port: int) -> bool:
    normalized = command.replace("\\ ", " ")
    script_match = (
        str(WEB_SCRIPT) in normalized
        or "sodamusic_export_web.py" in normalized
    )
    port_match = f"--port {port}" in normalized or f"--port={port}" in normalized
    return script_match and port_match


def stop_process(pid: int, timeout: float = 5.0) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return True

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.2)

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return True
    time.sleep(0.2)
    try:
        os.kill(pid, 0)
    except OSError:
        return True
    return False


def fetch_text(url: str, timeout: float = 1.5) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        if response.status != 200:
            return ""
        return response.read(8192).decode("utf-8", errors="replace")


def fetch_json(url: str, timeout: float = 1.5) -> dict[str, Any]:
    try:
        payload = json.loads(fetch_text(url, timeout))
    except (json.JSONDecodeError, OSError, urllib.error.URLError):
        return {}
    return payload if isinstance(payload, dict) else {}


def is_ready_response(home: str, defaults: dict[str, Any]) -> bool:
    try:
        api_version = int(defaults.get("apiVersion") or 0)
    except (TypeError, ValueError):
        api_version = 0
    return (
        "SodaMusic Cache Export" in home
        and bool(defaults.get("cacheDir"))
        and isinstance(defaults.get("sources"), dict)
        and api_version >= REQUIRED_WEB_API_VERSION
    )


def wait_until_ready(url: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    preflight_url = f"{url}/api/preflight-status"
    while time.time() < deadline:
        try:
            request_timeout = max(1.5, min(10.0, deadline - time.time()))
            home = fetch_text(url)
            defaults = fetch_json(preflight_url, timeout=request_timeout)
            if is_ready_response(home, defaults):
                return True
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    return False


def start_server(host: str, port: int, log_file: Path) -> subprocess.Popen[bytes]:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_file.open("ab", buffering=0)
    env = os.environ.copy()
    command = [
        python_executable(),
        str(WEB_SCRIPT),
        "--host",
        host,
        "--port",
        str(port),
        "--no-open",
    ]
    return subprocess.Popen(
        command,
        cwd=SRC_ROOT,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the SodaMusic cache export web UI.")
    parser.add_argument("--host", default=os.environ.get("SODAMUSIC_EXPORT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SODAMUSIC_EXPORT_PORT", "8765")))
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser.")
    parser.add_argument("--wait", type=float, default=30.0, help="Seconds to wait for the web UI.")
    parser.add_argument("--log-file", type=Path, default=default_log_file())
    parser.add_argument(
        "--skip-dependency-install",
        action="store_true",
        help="Only check dependencies; do not install missing ones.",
    )
    args = parser.parse_args()

    if not WEB_SCRIPT.exists():
        print(f"未找到 Web 服务脚本: {WEB_SCRIPT}", file=sys.stderr)
        return 1

    url = f"http://{args.host}:{args.port}"
    print("SodaMusic 缓存导出一键启动", flush=True)
    print(f"项目目录: {PROJECT_ROOT}", flush=True)
    print(f"服务地址: {url}", flush=True)
    print(f"日志文件: {args.log_file}", flush=True)
    print(flush=True)

    ensure_tool_path()
    print("检查运行依赖...", flush=True)
    dependency_report = ensure_runtime_dependencies(
        auto_install=not args.skip_dependency_install,
        show_install_output=True,
    )
    for item in dependency_report.installed:
        print(f"已安装: {item}", flush=True)
    for warning in dependency_report.warnings:
        print(f"提示: {warning}", flush=True)
    if not dependency_report.ok:
        for error in dependency_report.errors:
            print(error, file=sys.stderr)
        if dependency_report.missing:
            print(f"缺失依赖: {', '.join(dependency_report.missing)}", file=sys.stderr)
        return 1
    print("依赖检查通过。", flush=True)
    print(flush=True)

    if is_port_open(args.host, args.port):
        if wait_until_ready(url, 3):
            print("服务已在运行。")
            if not args.no_open:
                webbrowser.open(url)
            return 0

        pid = listener_pid(args.port)
        command = process_command(pid) if pid is not None else ""
        if pid is not None and is_own_web_process(command, args.port):
            print(f"发现旧服务进程 {pid}，正在重启。")
            if not stop_process(pid):
                print(f"无法停止旧服务进程 {pid}。请手动关闭后重试。", file=sys.stderr)
                return 1
            if is_port_open(args.host, args.port):
                print(f"端口 {args.port} 仍被占用。请稍后重试或换端口。", file=sys.stderr)
                return 1
        else:
            print(f"端口 {args.port} 已被占用，但不是当前工具。请换端口或关闭占用进程。", file=sys.stderr)
            if pid is not None:
                print(f"占用进程: {pid} {command}", file=sys.stderr)
            return 1

    process = start_server(args.host, args.port, args.log_file)
    print(f"已启动服务进程: {process.pid}")
    print("等待服务就绪...")

    if wait_until_ready(url, args.wait):
        print("服务已启动。")
        if not args.no_open:
            webbrowser.open(url)
        print(f"打开地址: {url}")
        return 0

    print(f"{args.wait:.0f} 秒内未检测到服务就绪。", file=sys.stderr)
    print(f"请查看日志: {args.log_file}", file=sys.stderr)
    try:
        process.terminate()
    except OSError:
        pass
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
