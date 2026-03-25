# Calibration And Matching Strategy

## Goal

Calibration's job is to estimate a reliable homography between the left and right RTSP cameras and save it for the native runtime.

The current project baseline is intentionally simple:

- keep a stable classic matcher
- preserve the auto baseline path
- allow assisted/manual seed points to improve results
- accept an assisted candidate only when it scores better than auto

## Current Principles

1. The auto path is always evaluated first.
2. Manual points are guidance, not forced ground truth.
3. Assisted refinement is optional and must beat the auto baseline to win.
4. Only the best candidate is saved to `data/runtime_homography.json`.

This keeps calibration predictable and avoids fragile one-off tuning.

## Current Flow

1. Capture representative left/right RTSP frames.
2. Show overlap hints in the assisted UI.
3. Let the operator place zero or more matching seed points.
4. Build the baseline auto candidate with the classic matcher.
5. If seed points exist, build an assisted candidate around those hints.
6. Score candidates on match quality, geometry, and visual overlap.
7. Show the inlier preview and stitched preview.
8. Save only the best candidate after review confirmation.

## Matching Backend

Calibration now uses the classic matcher only.

- descriptor extraction and matching stay in OpenCV/classic CV
- assisted mode still works by reinforcing matching around user seed points
- there is no deep-learning backend in the current calibration path

This matches the current runtime direction:

- fewer optional dependencies
- simpler setup on new machines
- less ambiguity during handoff and operations

## Candidate Selection

There are two practical candidates today:

- `auto`
- `assisted`

Selection rule:

1. Always compute `auto`.
2. Compute `assisted` only when the operator supplied seed points.
3. Pick the higher-quality candidate, with the existing score margin guard that protects the auto baseline from tiny, noisy wins.

## Why Seed Points Are Guidance Only

If a small set of manual points is treated as absolute truth, calibration can become unstable when:

- points are sparse
- points are clustered in one area
- the operator clicks slightly inconsistent locations

Treating them as guidance is safer:

- the operator can steer the overlap area
- the matcher still finds a broader set of correspondences
- the final homography is still chosen by candidate quality

## Practical Conclusion

The current calibration strategy is:

> preserve a strong classic auto baseline, use assisted seed points only as a controlled improvement path, and save the best validated candidate.
