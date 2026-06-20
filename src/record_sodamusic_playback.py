#!/usr/bin/env python3
"""
Record currently playing SodaMusic audio into a normal MP4/AAC file.

This is a practical recovery path for CENC-protected cache entries: SodaMusic
itself performs authorized playback, and this helper records the decoded app
audio through macOS ScreenCaptureKit.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SWIFT_SOURCE = ROOT / "capture_sodamusic_audio.swift"
DEFAULT_BINARY = Path("/tmp/capture_sodamusic_audio")


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, capture_output=True, text=True)


def safe_filename(value: str, fallback: str = "sodamusic-recording") -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value or fallback


def compile_recorder(binary: Path) -> None:
    swiftc = shutil.which("swiftc")
    if not swiftc:
        raise SystemExit("swiftc not found; install Xcode Command Line Tools")
    command = [
        swiftc,
        "-framework",
        "ScreenCaptureKit",
        "-framework",
        "AVFoundation",
        "-framework",
        "AppKit",
        str(SWIFT_SOURCE),
        "-o",
        str(binary),
    ]
    result = run(command, check=False)
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout)


def activate_and_toggle_play() -> None:
    script = """
tell application "汽水音乐" to activate
delay 0.8
tell application "System Events" to tell process "汽水音乐" to set frontmost to true
delay 0.3
tell application "System Events" to keystroke space
"""
    result = run(["osascript", "-e", script], check=False)
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout)


def afinfo(path: Path) -> str:
    tool = shutil.which("afinfo")
    if not tool:
        return ""
    result = run([tool, str(path)], check=False)
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout)
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record decoded SodaMusic playback to a normal MP4/AAC file."
    )
    parser.add_argument("--seconds", type=int, required=True, help="Recording duration.")
    parser.add_argument("--title", default="", help="Output filename stem.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.home() / "Music/SodaMusic Playback Recordings",
    )
    parser.add_argument("--output", type=Path, help="Exact output file path.")
    parser.add_argument(
        "--no-toggle-play",
        action="store_true",
        help="Do not activate SodaMusic and press Space before recording.",
    )
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    args = parser.parse_args()

    if args.seconds <= 0:
        parser.error("--seconds must be greater than 0")

    output = args.output
    if output is None:
        stem = safe_filename(args.title)
        output = args.output_dir.expanduser() / f"{stem}.mp4"
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    compile_recorder(args.binary)
    if not args.no_toggle_play:
        activate_and_toggle_play()

    result = subprocess.run(
        [
            str(args.binary),
            "--seconds",
            str(args.seconds),
            "--output",
            str(output),
        ],
        text=True,
    )
    if result.returncode != 0:
        return result.returncode

    info = afinfo(output)
    print(f"Output: {output}")
    if info:
        for line in info.splitlines():
            if any(key in line for key in ("estimated duration", "bit rate", "audio bytes")):
                print(line.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
