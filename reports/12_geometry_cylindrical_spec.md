# Cylindrical Geometry Baseline Spec

## Summary
This document defines the geometry baseline for the project.

The current homography-only design is acceptable as a prototype, but it is not the best choice for this camera layout. With two cameras looking left and right by roughly 45 degrees, a planar homography forces the system to explain a wide field of view using a single projective transform. That is workable for a narrow overlap and far-field scenes, but it tends to stretch edges and make the stitched scene look mechanically flat or bent in the wrong places.

The better replacement for this repo is a cylindrical baseline with residual affine correction and content-aware seam/exposure handling.

## Why Homography-Only Is Not The Best Choice
Homography-only is problematic here because:

- It assumes the scene can be represented well by a single plane-like transform.
- It breaks down visually when the cameras cover a wide horizontal sweep.
- It does not solve parallax, so near objects still ghost.
- It often pushes the seam decision into a simple feather blend, which exposes brightness differences and edge discontinuities.

That makes homography-only a good baseline for a narrow, simple scene, but not the best long-term baseline for this project.

## Why Cylindrical Is Better Here
Cylindrical reprojection is a better fit because:

- It matches a wide panorama-style camera arrangement more naturally.
- It reduces the visual penalty of wide yaw coverage.
- It gives a more stable basis for seam placement in the overlap zone.
- It lets residual affine alignment handle the remaining mismatch without forcing the whole scene into one planar transform.

This is especially appropriate for a left/right camera pair that is intentionally pointed outward rather than both cameras looking forward at nearly the same axis.

## Proposed Geometry Pipeline
The target pipeline is:

1. Lens correction or undistortion if a lens profile exists.
2. Cylindrical reprojection for each input stream.
3. Residual affine alignment for the right stream.
4. Dynamic seam selection in the overlap region.
5. Exposure compensation across the overlap.
6. Final encode/output.

## Artifact Shape
The geometry artifact should be versioned and should store:

- `schema_version`
- left and right lens correction references
- projection model and projection parameters
- residual alignment model and parameters
- seam defaults
- calibration metadata
- residual quality metrics

This is better than a single raw homography file because it describes the true runtime geometry stack instead of hiding everything inside one matrix.

## Why The Current Choices Are Problematic
| Area | Current choice | Problem |
|---|---|---|
| Geometry | Single homography only | Too weak for the project’s wide left/right camera layout. |
| Seam | Feather blending only | Makes exposure differences and overlap artifacts too visible. |
| Calibration | ORB-only matching | It is fast, but not the most robust classical choice for calibration. |
| Artifact model | One homography file | It cannot describe the full geometry state cleanly. |

## Preferred Replacement
The replacement should be:

- Cylindrical reprojection as the default geometry baseline.
- Residual affine correction as the first alignment refinement.
- Dynamic seam selection and exposure compensation in the main runtime path.
- SIFT primary with ORB fallback for calibration matching.

## Validation Criteria
- The overlap should show lower luma disagreement than the homography baseline.
- The seam should not jump erratically across similar frames.
- The final scene should look natural across the full width, not only in the center.
- The geometry artifact should be reproducible from the same source inputs.

## Non-Goals
- No APAP/local warp in the first implementation wave.
- No deep matcher dependency in the first implementation wave.
- No attempt to make cylindrical solve parallax completely; it only improves the reprojection model.

