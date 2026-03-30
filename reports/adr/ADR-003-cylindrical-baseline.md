# ADR-003: Cylindrical Geometry Baseline

## Current Choice
The current runtime baseline is single homography plus warpPerspective plus feather blending.

## Why This Is A Problem Here
That model is acceptable for a narrow or nearly planar scene, but the project’s camera layout is wider than that. With cameras looking left and right at roughly 45 degrees, a single homography stretches the whole world into one projective explanation. The result is visually brittle, and the seam is too exposed when brightness or perspective differs.

## Alternatives Considered
- Keep homography-only and improve blend heuristics.
- Move to a local warp or APAP immediately.
- Use cylindrical reprojection with residual affine correction.

## Chosen Best Option
Use cylindrical reprojection as the baseline geometry model, then apply residual affine correction, dynamic seam selection, and exposure compensation.

## Why This Is Best For This Repo
- It fits the current camera layout better than homography-only.
- It improves the panoramic look without jumping straight to a heavy local-warp solution.
- It still keeps the runtime explainable and maintainable.
- It preserves a clear path to later quality upgrades if needed.

## Cost
- It requires new calibration artifact shape and runtime geometry handling.
- It adds some implementation complexity compared with a single homography matrix.

## Proof Required
- Overlap agreement improves on the reference pair.
- Seam quality improves on static and slow-moving scenes.
- The runtime remains stable enough to keep the service target.

