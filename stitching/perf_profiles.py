from __future__ import annotations

from typing import Final


PERF_PROFILES: Final[dict[str, tuple[float, int]]] = {
    "quality": (1.0, 4000),
    "balanced": (0.75, 2800),
    "fast": (0.5, 1800),
}


def resolve_perf_profile(perf_mode: str, process_scale: float | None) -> tuple[float, int]:
    """
    성능 모드를 실제 처리 파라미터로 변환한다.
    반환값: (process_scale, max_features)
    """

    mode = (perf_mode or "quality").lower()
    scale, max_features = PERF_PROFILES.get(mode, PERF_PROFILES["quality"])
    if process_scale is not None:
        scale = float(process_scale)
    if scale <= 0:
        raise ValueError("process_scale must be > 0")
    return scale, int(max_features)
