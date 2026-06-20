#!/usr/bin/env python3
"""Runtime dependency checks for the SodaMusic export tools."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"

EXTRA_PATHS = (
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    "/usr/local/sbin",
)


@dataclass
class DependencyReport:
    ok: bool = True
    installed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def ensure_tool_path() -> None:
    paths = [path for path in os.environ.get("PATH", "").split(os.pathsep) if path]
    changed = False
    for path in EXTRA_PATHS:
        if Path(path).is_dir() and path not in paths:
            paths.append(path)
            changed = True
    if changed:
        os.environ["PATH"] = os.pathsep.join(paths)


def which(command: str) -> str | None:
    ensure_tool_path()
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / command
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def has_python_package(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def install_python_requirements(
    *, capture_output: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(REQUIREMENTS_FILE),
        ],
        capture_output=capture_output,
        text=True,
        check=False,
    )


def brew_install(
    packages: list[str], *, capture_output: bool = True
) -> subprocess.CompletedProcess[str]:
    brew = which("brew")
    if not brew:
        raise FileNotFoundError("brew")
    env = os.environ.copy()
    env.setdefault("HOMEBREW_NO_AUTO_UPDATE", "1")
    return subprocess.run(
        [brew, "install", *packages],
        capture_output=capture_output,
        env=env,
        text=True,
        check=False,
    )


def compact_command_output(result: subprocess.CompletedProcess[str]) -> str:
    output = (result.stderr or result.stdout or "").strip()
    if len(output) <= 800:
        return output
    return f"{output[:797]}..."


def ensure_runtime_dependencies(
    *, auto_install: bool = True, show_install_output: bool = False
) -> DependencyReport:
    ensure_tool_path()
    report = DependencyReport()

    missing_python_packages: list[str] = []
    if not has_python_package("Crypto"):
        missing_python_packages.append("pycryptodome")
    if not has_python_package("mutagen"):
        missing_python_packages.append("mutagen")

    if missing_python_packages:
        if auto_install:
            result = install_python_requirements(capture_output=not show_install_output)
            still_missing = []
            if not has_python_package("Crypto"):
                still_missing.append("pycryptodome")
            if not has_python_package("mutagen"):
                still_missing.append("mutagen")
            if result.returncode == 0 and not still_missing:
                report.installed.append("Python packages from requirements.txt")
            else:
                report.ok = False
                report.missing.extend(still_missing or missing_python_packages)
                detail = compact_command_output(result)
                report.errors.append(
                    "无法安装 Python 依赖，请手动运行: "
                    f"{sys.executable} -m pip install -r {REQUIREMENTS_FILE}"
                    + (f"\n{detail}" if detail else "")
                )
        else:
            report.ok = False
            report.missing.extend(missing_python_packages)

    missing_required_brew_packages: list[str] = []
    if not which("node"):
        missing_required_brew_packages.append("node")

    missing_optional_brew_packages: list[str] = []
    if sys.platform == "darwin" and not which("ffmpeg"):
        missing_optional_brew_packages.append("ffmpeg")

    missing_brew_packages = missing_required_brew_packages + [
        package for package in missing_optional_brew_packages
        if package not in missing_required_brew_packages
    ]
    optional_install_failures: set[str] = set()
    if missing_brew_packages:
        if auto_install and which("brew"):
            result = brew_install(
                missing_brew_packages,
                capture_output=not show_install_output,
            )
            still_missing = [package for package in missing_brew_packages if not which(package)]
            installed_packages = [
                package for package in missing_brew_packages if package not in still_missing
            ]
            if installed_packages:
                report.installed.append(f"Homebrew packages: {', '.join(installed_packages)}")

            still_missing_required = [
                package for package in still_missing if package in missing_required_brew_packages
            ]
            if still_missing_required:
                report.ok = False
                report.missing.extend(still_missing_required)
                detail = compact_command_output(result)
                report.errors.append(
                    "无法通过 Homebrew 安装依赖，请手动运行: "
                    f"brew install {' '.join(still_missing_required)}"
                    + (f"\n{detail}" if detail else "")
                )

            still_missing_optional = [
                package for package in still_missing if package in missing_optional_brew_packages
            ]
            if still_missing_optional:
                optional_install_failures.update(still_missing_optional)
                detail = compact_command_output(result)
                report.warnings.append(
                    "无法通过 Homebrew 安装可选依赖，请手动运行: "
                    f"brew install {' '.join(still_missing_optional)}"
                    + (f"\n{detail}" if detail else "")
                )
        else:
            if missing_required_brew_packages:
                report.ok = False
                report.missing.extend(missing_required_brew_packages)
                if sys.platform == "darwin":
                    report.errors.append(
                        "缺少外部工具: "
                        f"{', '.join(missing_required_brew_packages)}。请安装 Homebrew 后运行: "
                        f"brew install {' '.join(missing_required_brew_packages)}"
                    )
                else:
                    report.errors.append(
                        "缺少外部工具: "
                        f"{', '.join(missing_required_brew_packages)}。请用系统包管理器安装。"
                    )

    if sys.platform == "darwin":
        if not which("osascript"):
            report.warnings.append("缺少 osascript，目录选择器将不可用。")
        if not which("ffmpeg") and "ffmpeg" not in optional_install_failures:
            report.warnings.append("缺少 ffmpeg，MP3/FLAC 导出不可用。")
        if not which("afconvert") and not which("ffmpeg"):
            report.warnings.append("缺少音频解码器，验证音频会失败。")
    else:
        if not which("ffmpeg"):
            report.warnings.append("缺少 ffmpeg，MP3/FLAC 导出不可用。")
        if not which("python3") and not which("python"):
            report.warnings.append("未在 PATH 中找到 python 命令。")

    return report
