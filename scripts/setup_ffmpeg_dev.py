from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen


DEFAULT_ARCHIVE_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-full-shared.7z"
DEFAULT_SHA256_URL = DEFAULT_ARCHIVE_URL + ".sha256"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def _compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_expected_sha256(text: str) -> str:
    for token in text.replace("\n", " ").split():
        token = token.strip().lower()
        if len(token) == 64 and all(ch in "0123456789abcdef" for ch in token):
            return token
    raise RuntimeError("could not parse sha256 file")


def _extract_7z(archive: Path, output_dir: Path) -> None:
    try:
        import py7zr  # type: ignore
    except ModuleNotFoundError:
        py7zr = None

    if py7zr is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        with py7zr.SevenZipFile(archive, mode="r") as archive_file:
            archive_file.extractall(path=output_dir)
        return

    extractor_candidates = [
        shutil.which("7z"),
        shutil.which("7zr"),
        str((_repo_root() / ".third_party" / "downloads" / "7zr.exe").resolve()),
    ]
    for candidate in extractor_candidates:
        if not candidate:
            continue
        candidate_path = Path(candidate)
        if not candidate_path.exists():
            continue
        output_dir.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [str(candidate_path), "x", str(archive), f"-o{output_dir}", "-y"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode == 0:
            return
        raise RuntimeError(
            f"7z extractor failed: returncode={completed.returncode} stderr={completed.stderr.strip()}"
        )

    raise RuntimeError(
        "No usable 7z extractor found. Install py7zr or place 7zr.exe in .third_party/downloads."
    )


def _find_extracted_root(temp_dir: Path) -> Path:
    include_candidates = list(temp_dir.rglob("include/libavcodec/avcodec.h"))
    for candidate in include_candidates:
        root = candidate.parents[2]
        if (root / "lib").exists():
            return root
    raise RuntimeError("failed to locate extracted FFmpeg dev root")


def _sync_tree(source_root: Path, target_root: Path) -> None:
    if target_root.exists():
        shutil.rmtree(target_root)
    shutil.copytree(source_root, target_root)


def _print_ffmpeg_version(ffmpeg_bin: Path) -> None:
    completed = subprocess.run(
        [str(ffmpeg_bin), "-hide_banner", "-version"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    first_line = completed.stdout.strip().splitlines()
    if first_line:
        print(first_line[0])


def main() -> int:
    parser = argparse.ArgumentParser(description="Download and install FFmpeg shared dev package for gpu-direct work")
    parser.add_argument("--archive-url", default=DEFAULT_ARCHIVE_URL)
    parser.add_argument("--sha256-url", default=DEFAULT_SHA256_URL)
    parser.add_argument("--skip-sha256", action="store_true")
    parser.add_argument(
        "--target-root",
        default=str(_repo_root() / ".third_party" / "ffmpeg-dev" / "current"),
        help="Final extracted root containing include/ and lib/",
    )
    parser.add_argument(
        "--downloads-dir",
        default=str(_repo_root() / ".third_party" / "downloads"),
        help="Directory for downloaded archives",
    )
    args = parser.parse_args()

    target_root = Path(args.target_root).resolve()
    downloads_dir = Path(args.downloads_dir).resolve()
    archive_path = downloads_dir / "ffmpeg-dev-current.7z"
    sha_path = downloads_dir / "ffmpeg-dev-current.7z.sha256"

    print(f"download_archive={args.archive_url}")
    _download(args.archive_url, archive_path)
    print(f"archive_path={archive_path}")

    if not args.skip_sha256:
        print(f"download_sha256={args.sha256_url}")
        _download(args.sha256_url, sha_path)
        expected_sha = _read_expected_sha256(sha_path.read_text(encoding="utf-8", errors="ignore"))
        actual_sha = _compute_sha256(archive_path)
        print(f"expected_sha256={expected_sha}")
        print(f"actual_sha256={actual_sha}")
        if expected_sha != actual_sha:
            raise RuntimeError("sha256 mismatch for downloaded FFmpeg dev archive")

    with tempfile.TemporaryDirectory(prefix="hogak_ffmpeg_dev_") as temp_dir_text:
        temp_dir = Path(temp_dir_text)
        print(f"extract_temp={temp_dir}")
        _extract_7z(archive_path, temp_dir)
        extracted_root = _find_extracted_root(temp_dir)
        print(f"extracted_root={extracted_root}")
        _sync_tree(extracted_root, target_root)

    ffmpeg_bin = target_root / "bin" / "ffmpeg.exe"
    print(f"installed_root={target_root}")
    if ffmpeg_bin.exists():
        _print_ffmpeg_version(ffmpeg_bin)
    print(f"include_ready={(target_root / 'include' / 'libavcodec' / 'avcodec.h').exists()}")
    print(f"lib_ready={(target_root / 'lib' / 'avcodec.lib').exists()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
