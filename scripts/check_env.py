#!/usr/bin/env python3
"""Report the development environment.

Run this on the founder's actual machine — the container this repository was built in
is ephemeral and is not representative. Its output belongs in the pilot record, because
"which machine produced this model" is part of reproducibility.

Deliberately dependency-free so it runs before anything is installed:

    python3 scripts/check_env.py
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys

#: RDD2022 is ~13.3 GB zipped and roughly doubles when extracted, so a machine with
#: less than this cannot ingest it whole and must use partial/streaming download.
RDD2022_ZIPPED_GB = 13.3
RECOMMENDED_FREE_GB = 60.0


def _run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
        return (
            (out.stdout or out.stderr).strip().splitlines()[0] if out.stdout or out.stderr else None
        )
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def collect() -> dict[str, object]:
    info: dict[str, object] = {
        "python": platform.python_version(),
        "python_ok": sys.version_info >= (3, 11),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": _cpu_count(),
    }

    for tool, cmd in (
        ("git", ["git", "--version"]),
        ("node", ["node", "--version"]),
        ("npm", ["npm", "--version"]),
        ("ffmpeg", ["ffmpeg", "-version"]),
        ("docker", ["docker", "--version"]),
    ):
        info[tool] = _run(cmd) if shutil.which(tool) else "NOT FOUND"

    gpu = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
    info["gpu"] = gpu or "none detected (CPU-only; training should use Kaggle/Colab)"

    total, _, free = shutil.disk_usage(".")
    info["disk_free_gb"] = round(free / 1e9, 1)
    info["disk_total_gb"] = round(total / 1e9, 1)

    for module in ("pydantic", "numpy", "torch", "torchvision", "av", "pytest"):
        try:
            mod = __import__(module)
            info[module] = getattr(mod, "__version__", "installed")
        except ImportError:
            info[module] = "not installed"

    return info


def _cpu_count() -> int | None:
    try:
        return len(__import__("os").sched_getaffinity(0))
    except (AttributeError, OSError):
        import os

        return os.cpu_count()


def warnings_for(info: dict[str, object]) -> list[str]:
    out: list[str] = []
    if not info["python_ok"]:
        out.append(f"Python {info['python']} is below the required 3.11.")
    if info["ffmpeg"] == "NOT FOUND":
        out.append(
            "ffmpeg not found — real video decoding is unavailable. The core pipeline "
            "and full test suite still run; install the 'video' extra plus ffmpeg when "
            "you need to process actual footage."
        )
    if info["node"] == "NOT FOUND":
        out.append("node not found — the Expo collector cannot be built here.")
    if str(info["gpu"]).startswith("none"):
        out.append(
            "No GPU detected. Inference and the test suite are fine; train on Kaggle "
            "(~30 GPU-h/week) or Colab, and checkpoint often since neither is guaranteed."
        )
    free = float(info["disk_free_gb"])  # type: ignore[arg-type]
    if free < RDD2022_ZIPPED_GB * 2:
        out.append(
            f"Only {free} GB free. RDD2022 is ~{RDD2022_ZIPPED_GB} GB zipped and roughly "
            "doubles when extracted — use per-country partial download rather than "
            "fetching the whole archive."
        )
    elif free < RECOMMENDED_FREE_GB:
        out.append(
            f"{free} GB free. Survey video runs ~100 MB/min, so a 30-minute drive is "
            "~3 GB. Plan external storage before collecting many surveys."
        )
    return out


def main() -> int:
    info = collect()
    problems = warnings_for(info)

    if "--json" in sys.argv:
        print(json.dumps({"environment": info, "warnings": problems}, indent=2))
        return 0

    print("RoadEye environment check")
    print("=" * 60)
    width = max(len(k) for k in info)
    for key, value in info.items():
        print(f"  {key:<{width}}  {value}")

    if problems:
        print("\nNotes:")
        for note in problems:
            print(f"  - {note}")
    else:
        print("\nNo issues found.")

    # Informational only: a missing GPU or ffmpeg is expected and must not fail CI.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
