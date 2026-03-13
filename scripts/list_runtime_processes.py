from __future__ import annotations

import sys


def main() -> int:
    try:
        import psutil  # type: ignore
    except Exception as exc:
        print(f"psutil unavailable: {exc}")
        return 1

    names = {"stitch_runtime.exe", "ffmpeg.exe", "ffplay.exe", "vlc.exe", "python.exe"}
    matched: list[tuple[int, str, str]] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = proc.info
            name = str(info.get("name") or "")
            if name.lower() not in names:
                continue
            cmdline = info.get("cmdline") or []
            text = " ".join(str(part) for part in cmdline)
            if any(token in text for token in ("23000", "24000", "stitching.cli", "stitch_runtime.exe")):
                matched.append((int(info.get("pid") or 0), name, text))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    matched.sort(key=lambda item: (item[1].lower(), item[0]))
    for pid, name, text in matched:
        print(f"[{pid}] {name}")
        print(text)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
