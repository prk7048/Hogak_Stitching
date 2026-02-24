# Pair01/Pair02 Stitching Validation Report

Date: 2026-02-24
Project: Dual Smartphone Stitching PoC (Image MVP)

## 1) Scope

- Compare image stitching outcomes for `pair01` and `pair02`
- Verify ghosting mitigation for unstable transforms without degrading stable cases

## 2) Input

- `input/images/pair01_left.jpg`
- `input/images/pair01_right.jpg`
- `input/images/pair02_left.jpg`
- `input/images/pair02_right.jpg`

## 3) Method Update

Ghosting mitigation was added with an adaptive blend policy:

- Stable transform: `feather` blend (existing behavior)
- Unstable transform (fallback affine or high overlap difference): `seam_cut` blend

Decision signal:

- `overlap_diff_mean >= 18.0` or `homography_unstable_fallback_affine` warning

## 4) Results

| Pair | Status | Matches | Inliers | Blend Mode | overlap_diff_mean | Output Resolution | Total Time (s) | Warnings |
|---|---|---:|---:|---|---:|---|---:|---|
| pair01 | succeeded | 115 | 24 | seam_cut | 31.884 | 7583x3847 | 4.6561 | homography_unstable_fallback_affine |
| pair02 | succeeded | 1185 | 1169 | feather | 2.202 | 7467x2834 | 3.6840 | (none) |

## 5) Interpretation

- `pair01`:
  - Transform stability is lower (fallback affine triggered).
  - Adaptive seam-cut was selected to reduce visible double edges (ghosting) from overlap averaging.
- `pair02`:
  - Transform quality is high and consistent.
  - Existing feather blending remained active; stable output behavior preserved.

## 6) Artifacts

- `output/images/pair01_stitched.png`
- `output/images/pair02_stitched.png`
- `output/images/pair01_report.json`
- `output/images/pair02_report.json`
- `output/debug/pair01/*`
- `output/debug/pair02/*`

## 7) Go/No-Go

- Go:
  - Pair-level adaptive blending works and does not regress the stable case (`pair02`).
  - Required metrics/report fields are present for both runs.
- No-Go (for production):
  - Dataset is too small (2 pairs). Need broader validation before claiming robustness.

## 8) Recommended Next Steps

1. Add 5+ additional difficult pairs (parallax, moving objects, exposure differences).
2. Add a simple quality gate in report: `quality_tier = stable / fallback / risky`.
3. Add a side-by-side debug mosaic output for faster manual review.

