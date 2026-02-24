from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    NONE = "NONE"
    PROBE_FAIL = "PROBE_FAIL"
    OVERLAP_LOW = "OVERLAP_LOW"
    HOMOGRAPHY_FAIL = "HOMOGRAPHY_FAIL"
    SYNC_FAIL = "SYNC_FAIL"
    ENCODE_FAIL = "ENCODE_FAIL"
    INTERNAL_ERROR = "INTERNAL_ERROR"

