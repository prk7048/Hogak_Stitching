from __future__ import annotations

import cv2


def main() -> int:
    print("has_cudacodec_attr", hasattr(cv2, "cudacodec"))
    build_info = cv2.getBuildInformation()
    print("build_mentions_cudacodec", "cudacodec" in build_info.lower())
    print("build_mentions_nvcuvenc", "nvcuvenc" in build_info.lower())
    print("build_mentions_nvcuvid", "nvcuvid" in build_info.lower())
    if hasattr(cv2, "cudacodec"):
        names = sorted(name for name in dir(cv2.cudacodec) if "Writer" in name or "Encoder" in name or "create" in name)
        print("cudacodec_symbols", names[:40])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
