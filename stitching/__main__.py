from __future__ import annotations

import sys


def _safe_input(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _interactive_desktop() -> int:
    try:
        from stitching.desktop_app import DesktopConfig, run_desktop
    except ModuleNotFoundError as exc:
        if exc.name == "cv2":
            print("Missing dependency: opencv-python. Install requirements in your venv first.")
            return 2
        raise

    print("[Desktop Mode] Enter RTSP URLs. Leave blank to skip a side.")
    left = _safe_input("Left RTSP URL: ")
    right = _safe_input("Right RTSP URL: ")
    gpu = _safe_input("GPU mode [on/auto/off] (default: on): ").lower() or "on"
    if gpu not in {"on", "auto", "off"}:
        print(f"Invalid gpu mode '{gpu}', fallback to 'on'")
        gpu = "on"

    cfg = DesktopConfig(
        left_rtsp=left,
        right_rtsp=right,
        gpu_mode=gpu,
    )
    return int(run_desktop(cfg))


def main() -> int:
    if len(sys.argv) > 1:
        from stitching.cli import main as cli_main

        return int(cli_main())
    return _interactive_desktop()


if __name__ == "__main__":
    raise SystemExit(main())
