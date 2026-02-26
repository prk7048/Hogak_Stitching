# Progress Report Since Commit `999d3b1`

Date: 2026-02-26  
Base commit: `999d3b1` (`Improve ghosting handling and add pair01/pair02 validation report`)

## 1) Scope

This report summarizes code and pipeline updates after the last pushed commit, including:

- quality-focused stitching improvements
- video sync/blending stabilization changes
- simplified CLI presets for video runs (`10s`, `30s`, `full`)
- validation runs and outcomes

## 2) Work Log (Chronological)

1. Fixed debug draw pipeline stability (OpenCV `matchesMask` type handling).
2. Added geometry safety checks to prevent invalid huge canvases.
3. Added affine fallback when homography is unstable.
4. Added adaptive seam-cut logic for ghost-prone overlap.
5. Added sync refinement for videos (coarse offset + local search refinement).
6. Added exposure compensation (gain/bias) from overlap statistics.
7. Upgraded seam from fixed vertical cut to row-wise minimal-cost seam path.
8. Added simplified CLI presets:
   - `python -m stitching video-10s --pair <name>`
   - `python -m stitching video-30s --pair <name>`
   - `python -m stitching video-full --pair <name>`
9. Added automatic input/output naming for preset commands.
10. Added full-mode behavior (`max_duration_sec <= 0` means process full available range).

## 3) Major Technical Changes

### A. Image/Video Quality

- Overlap-based exposure compensation (`exposure_gain`, `exposure_bias` in report)
- Seam-cut now uses a row-wise dynamic seam path to reduce visible central boundary artifacts
- Seam blending is kept adaptive by overlap risk metrics

### B. Video Sync

- Coarse sync from luma cross-correlation
- Local refinement window around coarse offset using quick alignment score
- Report now includes:
  - `coarse_sync_offset_ms`
  - `estimated_sync_offset_ms`
  - `sync_refine_score`

### C. CLI Usability

- Added preset commands with minimal input:
  - `video-10s`, `video-30s`, `video-full`
- Supports:
  - `--pair video04` (recommended)
  - or explicit `--left/--right`
  - if omitted, latest valid pair in `input/videos` is auto-selected
- Output filenames are auto-generated:
  - `output/videos/{pair}_{preset}_stitched.mp4`
  - `output/videos/{pair}_{preset}_report.json`
  - `output/debug/{pair}_{preset}/`

## 4) Validation Runs

### Preset Command Validation

- `python -m stitching video-10s --pair video04` -> succeeded
- `python -m stitching video-30s --pair video01` -> succeeded
- `python -m stitching video-full --pair video01` -> succeeded

### Key Result Snapshots

1. `video04_10s_report.json`
   - status: succeeded
   - sync: `541.67 ms`
   - blend: `seam_cut`
   - overlap diff: `45.439`
   - exposure: `gain=1.1176`, `bias=-10.38`

2. `video01_30s_report.json`
   - status: succeeded
   - sync coarse -> refined: `-866.67 ms -> -733.33 ms`
   - blend: `seam_cut`
   - overlap diff: `9.848`
   - exposure: `gain=1.0406`, `bias=-7.0963`

3. `video01_full_report.json`
   - status: succeeded
   - same refined sync/blend profile as above
   - full range processing confirmed

## 5) Dataset Scope Update

- `video2` and `video3` are excluded from ongoing project validation by user decision.
- Current active validation set is based on retained video pairs (e.g., `video01`, `video04`).

## 6) Known Limitations (Still Present)

- Large parallax / near 3D object motion still causes visible artifacts despite seam improvements.
- Single global transform remains a structural quality limit for difficult motion/depth scenes.
- Full-quality production target likely requires local warp (mesh/APAP-style) and multiband blending.

## 7) Next Recommended Actions

1. Add multiband blending mode on top of current seam path.
2. Add quality tier metric (`stable/fallback/risky`) using existing report metrics.
3. Add side-by-side debug mosaic output per run (`left/right/stitched/overlap diff`) for faster review.

