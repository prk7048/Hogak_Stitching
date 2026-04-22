from __future__ import annotations

from typing import Any

import numpy as np

from stitching.core.config import StitchingFailure
from stitching.errors import ErrorCode


class AssistedCalibrationUi:
    _LEFT_PANEL = "left"
    _RIGHT_PANEL = "right"
    _BUTTON_COMPLETE = "complete"
    _BUTTON_UNDO = "undo"
    _BUTTON_RESET = "reset"

    def __init__(
        self,
        left: np.ndarray,
        right: np.ndarray,
        *,
        status: str = "",
        left_overlap_hint: tuple[int, int, int, int] | None = None,
        right_overlap_hint: tuple[int, int, int, int] | None = None,
        cv2_module: Any,
    ) -> None:
        self._left = left
        self._right = right
        self._left_points: list[tuple[float, float]] = []
        self._right_points: list[tuple[float, float]] = []
        self._status = status or "Click matching points in left/right images, then press COMPLETE."
        self._left_overlap_hint = left_overlap_hint
        self._right_overlap_hint = right_overlap_hint
        self._window_name = "Native Calibration Assisted Mode"
        self._done = False
        self._cancelled = False
        self._layout = self._build_layout()
        self._cv2 = cv2_module

    def run(self) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        self._cv2.namedWindow(self._window_name, self._cv2.WINDOW_NORMAL)
        self._cv2.setMouseCallback(self._window_name, self._on_mouse)
        try:
            while True:
                canvas = self._render()
                self._cv2.imshow(self._window_name, canvas)
                key = self._cv2.waitKey(20) & 0xFF
                if key in (27, ord("q")):
                    self._cancelled = True
                    break
                if key in (13, 32):
                    if self._can_complete():
                        self._done = True
                        break
                if key in (8, ord("z")):
                    self._undo_last()
                if key == ord("r"):
                    self._reset()
                if self._done:
                    break
        finally:
            self._cv2.destroyWindow(self._window_name)
        if self._cancelled:
            raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, "assisted calibration cancelled by user")
        return list(self._left_points), list(self._right_points)

    def _build_layout(self) -> dict[str, tuple[int, int, int, int]]:
        left_h, left_w = self._left.shape[:2]
        right_h, right_w = self._right.shape[:2]
        panel_h = 540
        left_panel_w = max(320, int(round(left_w * (panel_h / float(max(1, left_h))))))
        right_panel_w = max(320, int(round(right_w * (panel_h / float(max(1, right_h))))))
        gap = 24
        header_h = 92
        footer_h = 96
        width = left_panel_w + right_panel_w + gap * 3
        height = header_h + panel_h + footer_h + gap * 2
        left_rect = (gap, header_h, left_panel_w, panel_h)
        right_rect = (gap * 2 + left_panel_w, header_h, right_panel_w, panel_h)
        button_y = header_h + panel_h + 22
        complete_rect = (width - 220, button_y, 180, 44)
        undo_rect = (40, button_y, 140, 44)
        reset_rect = (200, button_y, 140, 44)
        return {
            "canvas": (0, 0, width, height),
            self._LEFT_PANEL: left_rect,
            self._RIGHT_PANEL: right_rect,
            self._BUTTON_COMPLETE: complete_rect,
            self._BUTTON_UNDO: undo_rect,
            self._BUTTON_RESET: reset_rect,
        }

    def _render(self) -> np.ndarray:
        _, _, width, height = self._layout["canvas"]
        canvas = np.full((height, width, 3), 18, dtype=np.uint8)
        title = "Assisted calibration: click matching points in order. COMPLETE finishes immediately."
        subtitle = (
            f"left={len(self._left_points)} right={len(self._right_points)}  "
            "highlighted boxes = likely overlap area"
        )
        self._cv2.putText(canvas, title, (24, 34), self._cv2.FONT_HERSHEY_SIMPLEX, 0.72, (230, 230, 230), 2, self._cv2.LINE_AA)
        self._cv2.putText(canvas, subtitle, (24, 64), self._cv2.FONT_HERSHEY_SIMPLEX, 0.62, (180, 210, 255), 1, self._cv2.LINE_AA)
        self._cv2.putText(canvas, self._status, (24, 86), self._cv2.FONT_HERSHEY_SIMPLEX, 0.58, (200, 200, 200), 1, self._cv2.LINE_AA)

        left_panel = self._draw_panel(self._left, self._LEFT_PANEL, "LEFT")
        right_panel = self._draw_panel(self._right, self._RIGHT_PANEL, "RIGHT")
        lx, ly, lw, lh = self._layout[self._LEFT_PANEL]
        rx, ry, rw, rh = self._layout[self._RIGHT_PANEL]
        canvas[ly : ly + lh, lx : lx + lw] = left_panel
        canvas[ry : ry + rh, rx : rx + rw] = right_panel

        self._draw_button(canvas, self._BUTTON_UNDO, "UNDO", (70, 70, 70))
        self._draw_button(canvas, self._BUTTON_RESET, "RESET", (70, 70, 70))
        self._draw_button(canvas, self._BUTTON_COMPLETE, "COMPLETE", (60, 140, 70))
        return canvas

    def _draw_panel(self, frame: np.ndarray, panel_key: str, label: str) -> np.ndarray:
        px, py, pw, ph = self._layout[panel_key]
        resized = self._cv2.resize(frame, (pw, ph), interpolation=self._cv2.INTER_AREA)
        self._cv2.rectangle(resized, (0, 0), (pw - 1, ph - 1), (90, 90, 90), 1)
        self._cv2.putText(resized, label, (12, 24), self._cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2, self._cv2.LINE_AA)
        points = self._left_points if panel_key == self._LEFT_PANEL else self._right_points
        overlap_hint = self._left_overlap_hint if panel_key == self._LEFT_PANEL else self._right_overlap_hint
        src_h, src_w = frame.shape[:2]
        if overlap_hint is not None:
            hx, hy, hw, hh = overlap_hint
            ox1 = int(round((hx / float(max(1, src_w))) * pw))
            ox2 = int(round(((hx + hw) / float(max(1, src_w))) * pw))
            ox1 = max(0, min(pw - 1, ox1))
            ox2 = max(0, min(pw - 1, ox2))
            band_margin = max(0, int(round(ph * 0.04)))
            oy1 = band_margin
            oy2 = max(oy1 + 12, ph - band_margin - 1)
            min_box_width = max(18, int(round(pw * 0.12)))
            if ox2 - ox1 < min_box_width:
                center_x = int(round((ox1 + ox2) * 0.5))
                half_w = max(1, min_box_width // 2)
                ox1 = max(0, center_x - half_w)
                ox2 = min(pw - 1, center_x + half_w)
            if ox2 > ox1 and oy2 > oy1:
                self._cv2.rectangle(resized, (ox1, oy1), (ox2, oy2), (0, 220, 255), 1)
                self._cv2.putText(
                    resized,
                    "suggested overlap",
                    (max(8, ox1 + 8), max(26, oy1 + 22)),
                    self._cv2.FONT_HERSHEY_SIMPLEX,
                    0.52,
                    (0, 220, 255),
                    2,
                    self._cv2.LINE_AA,
                )
        colors = [
            (0, 0, 255),
            (0, 255, 0),
            (255, 0, 0),
            (0, 255, 255),
            (255, 0, 255),
            (255, 255, 0),
        ]
        for idx, (x, y) in enumerate(points, start=1):
            dx = int(round((x / float(max(1, src_w))) * pw))
            dy = int(round((y / float(max(1, src_h))) * ph))
            color = colors[(idx - 1) % len(colors)]
            self._cv2.circle(resized, (dx, dy), 5, color, -1, self._cv2.LINE_AA)
            self._cv2.putText(resized, str(idx), (dx + 7, dy - 7), self._cv2.FONT_HERSHEY_SIMPLEX, 0.52, color, 2, self._cv2.LINE_AA)
        return resized

    def _draw_button(
        self,
        canvas: np.ndarray,
        key: str,
        text: str,
        color: tuple[int, int, int],
    ) -> None:
        x, y, w, h = self._layout[key]
        self._cv2.rectangle(canvas, (x, y), (x + w, y + h), color, -1)
        self._cv2.rectangle(canvas, (x, y), (x + w, y + h), (230, 230, 230), 1)
        self._cv2.putText(
            canvas,
            text,
            (x + 16, y + 29),
            self._cv2.FONT_HERSHEY_SIMPLEX,
            0.72,
            (245, 245, 245),
            2,
            self._cv2.LINE_AA,
        )

    def _on_mouse(self, event: int, x: int, y: int, _flags: int, _userdata: object | None = None) -> None:
        if event != self._cv2.EVENT_LBUTTONDOWN:
            return
        button = self._hit_test_button(x, y)
        if button == self._BUTTON_COMPLETE:
            if self._can_complete():
                self._done = True
            return
        if button == self._BUTTON_UNDO:
            self._undo_last()
            return
        if button == self._BUTTON_RESET:
            self._reset()
            return
        panel = self._hit_test_panel(x, y)
        if panel is None:
            return
        self._append_point(panel, x, y)

    def _hit_test_button(self, x: int, y: int) -> str | None:
        for key in (self._BUTTON_COMPLETE, self._BUTTON_UNDO, self._BUTTON_RESET):
            bx, by, bw, bh = self._layout[key]
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return key
        return None

    def _hit_test_panel(self, x: int, y: int) -> str | None:
        for key in (self._LEFT_PANEL, self._RIGHT_PANEL):
            px, py, pw, ph = self._layout[key]
            if px <= x < px + pw and py <= y < py + ph:
                return key
        return None

    def _append_point(self, panel: str, x: int, y: int) -> None:
        px, py, pw, ph = self._layout[panel]
        src = self._left if panel == self._LEFT_PANEL else self._right
        src_h, src_w = src.shape[:2]
        rx = min(max(0.0, (x - px) * (src_w / float(max(1, pw)))), float(max(0, src_w - 1)))
        ry = min(max(0.0, (y - py) * (src_h / float(max(1, ph)))), float(max(0, src_h - 1)))
        if panel == self._LEFT_PANEL:
            self._left_points.append((rx, ry))
            self._status = f"Added LEFT point #{len(self._left_points)}"
        else:
            self._right_points.append((rx, ry))
            self._status = f"Added RIGHT point #{len(self._right_points)}"

    def _undo_last(self) -> None:
        if len(self._left_points) > len(self._right_points):
            self._left_points.pop()
        elif len(self._right_points) > len(self._left_points):
            self._right_points.pop()
        elif self._left_points and self._right_points:
            self._left_points.pop()
            self._right_points.pop()
        self._status = "Undid last point input"

    def _reset(self) -> None:
        self._left_points.clear()
        self._right_points.clear()
        self._status = "Reset all picked points"

    def _can_complete(self) -> bool:
        if len(self._left_points) != len(self._right_points):
            self._status = "Left/right point counts must match before COMPLETE."
            return False
        self._status = "Completing assisted calibration"
        return True


class CalibrationReviewUi:
    _BUTTON_CONFIRM = "confirm"
    _BUTTON_CANCEL = "cancel"

    def __init__(
        self,
        *,
        inlier_preview: np.ndarray,
        stitched_preview: np.ndarray,
        summary_lines: list[str],
        cv2_module: Any,
    ) -> None:
        self._inlier_preview = inlier_preview
        self._stitched_preview = stitched_preview
        self._summary_lines = summary_lines
        self._window_name = "Native Calibration Review"
        self._confirmed = False
        self._cancelled = False
        self._layout = self._build_layout()
        self._cv2 = cv2_module

    def run(self) -> bool:
        self._cv2.namedWindow(self._window_name, self._cv2.WINDOW_NORMAL)
        self._cv2.setMouseCallback(self._window_name, self._on_mouse)
        try:
            while True:
                canvas = self._render()
                self._cv2.imshow(self._window_name, canvas)
                key = self._cv2.waitKey(20) & 0xFF
                if key in (13, 32):
                    self._confirmed = True
                    break
                if key in (27, ord("q"), ord("c")):
                    self._cancelled = True
                    break
                if self._confirmed or self._cancelled:
                    break
        finally:
            self._cv2.destroyWindow(self._window_name)
        return bool(self._confirmed and not self._cancelled)

    def _build_layout(self) -> dict[str, tuple[int, int, int, int]]:
        panel_w = 780
        panel_h = 420
        gap = 20
        header_h = 120
        footer_h = 88
        width = panel_w * 2 + gap * 3
        height = header_h + panel_h + footer_h + gap * 2
        return {
            "canvas": (0, 0, width, height),
            "inliers": (gap, header_h, panel_w, panel_h),
            "stitched": (gap * 2 + panel_w, header_h, panel_w, panel_h),
            self._BUTTON_CANCEL: (40, header_h + panel_h + 22, 180, 44),
            self._BUTTON_CONFIRM: (width - 220, header_h + panel_h + 22, 180, 44),
        }

    def _render(self) -> np.ndarray:
        _, _, width, height = self._layout["canvas"]
        canvas = np.full((height, width, 3), 18, dtype=np.uint8)
        self._cv2.putText(canvas, "Review calibration result before launch", (24, 34), self._cv2.FONT_HERSHEY_SIMPLEX, 0.82, (235, 235, 235), 2, self._cv2.LINE_AA)
        for idx, line in enumerate(self._summary_lines, start=1):
            self._cv2.putText(canvas, line, (24, 34 + idx * 24), self._cv2.FONT_HERSHEY_SIMPLEX, 0.58, (210, 210, 210), 1, self._cv2.LINE_AA)
        self._draw_panel(canvas, "inliers", self._inlier_preview, "Inlier Matches")
        self._draw_panel(canvas, "stitched", self._stitched_preview, "Stitched Preview")
        self._draw_button(canvas, self._BUTTON_CANCEL, "CANCEL", (80, 80, 80))
        self._draw_button(canvas, self._BUTTON_CONFIRM, "CONFIRM", (60, 140, 70))
        return canvas

    def _draw_panel(self, canvas: np.ndarray, key: str, frame: np.ndarray, label: str) -> None:
        x, y, w, h = self._layout[key]
        panel = self._cv2.resize(frame, (w, h), interpolation=self._cv2.INTER_AREA)
        canvas[y : y + h, x : x + w] = panel
        self._cv2.rectangle(canvas, (x, y), (x + w, y + h), (120, 120, 120), 1)
        self._cv2.putText(canvas, label, (x + 12, y + 24), self._cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2, self._cv2.LINE_AA)

    def _draw_button(self, canvas: np.ndarray, key: str, text: str, color: tuple[int, int, int]) -> None:
        x, y, w, h = self._layout[key]
        self._cv2.rectangle(canvas, (x, y), (x + w, y + h), color, -1)
        self._cv2.rectangle(canvas, (x, y), (x + w, y + h), (230, 230, 230), 1)
        self._cv2.putText(canvas, text, (x + 18, y + 29), self._cv2.FONT_HERSHEY_SIMPLEX, 0.72, (245, 245, 245), 2, self._cv2.LINE_AA)

    def _on_mouse(self, event: int, x: int, y: int, _flags: int, _userdata: object | None = None) -> None:
        if event != self._cv2.EVENT_LBUTTONDOWN:
            return
        for key, attr in ((self._BUTTON_CONFIRM, "_confirmed"), (self._BUTTON_CANCEL, "_cancelled")):
            bx, by, bw, bh = self._layout[key]
            if bx <= x <= bx + bw and by <= y <= by + bh:
                setattr(self, attr, True)
                return
