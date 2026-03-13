from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from stitching.runtime_launcher import query_gpu_direct_status, resolve_ffmpeg_binary, resolve_runtime_binary


def _runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    path_entries = [
        str(REPO_ROOT / ".third_party" / "ffmpeg" / "current" / "bin"),
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1\bin\x64",
    ]
    env["PATH"] = os.pathsep.join(path_entries + [env.get("PATH", "")])
    return env


def _run_text(command: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_runtime_env(),
    )
    return int(completed.returncode), completed.stdout.strip(), completed.stderr.strip()


def _query_runtime_gpu_direct_status(runtime_bin: Path) -> dict[str, object]:
    payload = query_gpu_direct_status()
    payload.setdefault("runtime_bin", str(runtime_bin))
    return payload


def _query_ffmpeg_capabilities(ffmpeg_bin: Path) -> dict[str, object]:
    _, encoders_out, encoders_err = _run_text([str(ffmpeg_bin), "-hide_banner", "-encoders"])
    _, hwaccels_out, hwaccels_err = _run_text([str(ffmpeg_bin), "-hide_banner", "-hwaccels"])
    combined_encoder_text = "\n".join(part for part in (encoders_out, encoders_err) if part)
    combined_hwaccel_text = "\n".join(part for part in (hwaccels_out, hwaccels_err) if part)
    nvenc_encoders = [
        line.strip()
        for line in combined_encoder_text.splitlines()
        if "nvenc" in line.lower()
    ]
    hwaccels = [
        line.strip()
        for line in combined_hwaccel_text.splitlines()
        if line.strip() and not line.lower().startswith("hardware acceleration methods")
    ]
    return {
        "ffmpeg_bin": str(ffmpeg_bin),
        "nvenc_encoders": nvenc_encoders,
        "hwaccels": hwaccels,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect current gpu-direct dependency track readiness")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit non-zero if gpu-direct dependency track is not ready",
    )
    args = parser.parse_args()

    runtime_bin = resolve_runtime_binary()
    ffmpeg_bin = resolve_ffmpeg_binary()

    runtime_status = _query_runtime_gpu_direct_status(runtime_bin)
    ffmpeg_status = _query_ffmpeg_capabilities(ffmpeg_bin)

    status_payload = runtime_status
    dependency_ready = False
    if isinstance(status_payload, dict):
        dependency_ready = bool(status_payload.get("dependency_ready"))

    result = {
        "runtime_bin": str(runtime_bin),
        "ffmpeg": ffmpeg_status,
        "gpu_direct_runtime": runtime_status,
        "dependency_ready": dependency_ready,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"runtime_bin={runtime_bin}")
        print(f"ffmpeg_bin={ffmpeg_bin}")
        print(f"nvenc_encoders={len(ffmpeg_status['nvenc_encoders'])}")
        for line in ffmpeg_status["nvenc_encoders"]:
            print(f"  {line}")
        print(f"hwaccels={','.join(ffmpeg_status['hwaccels'])}")
        if isinstance(status_payload, dict):
            print(f"gpu_direct_provider={status_payload.get('provider', '-')}")
            print(f"gpu_direct_dependency_ready={status_payload.get('dependency_ready', False)}")
            print(f"gpu_direct_status={status_payload.get('status', '-')}")
            print(f"gpu_direct_ffmpeg_dev_root={status_payload.get('ffmpeg_dev_root', '')}")
        else:
            print(f"gpu_direct_status_raw={status_payload}")
        if runtime_status.get("stderr"):
            print(f"gpu_direct_runtime_stderr={runtime_status['stderr']}")

    if args.require_ready and not dependency_ready:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
