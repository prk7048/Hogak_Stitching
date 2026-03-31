# Virtual-Center Rectilinear Candidate Spec

## Summary
This document defines `virtual-center-rectilinear` as a candidate geometry model for the GPU-only branch.

It is not a baseline replacement yet. The goal of this model is to test whether a same-center left/right camera rig is better represented by a virtual camera projection than by the current `left-anchor + right warp` structure.

## Why This Candidate Exists
The current runtime is biased toward a left-anchor coordinate system:

- the left image acts like the primary canvas
- the right image is warped into that canvas
- residual alignment freedom is concentrated on the right side

That is practical, but it is not the cleanest model when the two cameras are intended to approximate a shared optical center and the incoming images are already internally rectified by the camera.

In that case, the more principled question is:

1. what is the ray seen by the left camera?
2. what is the ray seen by the right camera?
3. how should both rays be reprojected into one virtual output camera?

## Why Cylindrical-Affine Is Not Automatically Wrong
`cylindrical-affine` still has real strengths:

- it is robust for wide horizontal coverage
- it makes panorama-style output easier to stabilize
- it tolerates a broader range of overlap and wide yaw layouts

That means this document does not replace the cylindrical baseline. It defines a bakeoff candidate that may be visually better for a same-center rig, especially when the current cylindrical output reintroduces a “curved lens” look that the user does not want.

## When Virtual-Center Rectilinear Is Realistic
This model is realistic when all of the following are mostly true:

- both cameras are mounted as a left/right pair at effectively the same position
- the input stream is already rectified or close to pinhole/rectilinear
- the goal is a natural monitor-like output, not a maximally wide panorama
- the desired final field of view is moderate enough that rectilinear edge stretch stays acceptable

If the final horizontal sweep becomes very wide, rectilinear can create strong edge stretching. In that case a virtual-center camera may still be correct, but the chosen output projection might need to be cylindrical or Panini instead of rectilinear.

## Proposed Geometry Model
The candidate model is:

1. interpret each input pixel as a camera ray
2. rotate that ray into a shared rig/world frame
3. reproject the ray into a single virtual center camera
4. blend overlap in the shared output canvas

The first version should assume:

- no translation between left and right cameras
- per-camera orientation only
- a shared virtual output camera
- feather blend only in the initial GPU-only path

## Minimum Artifact Shape
The runtime geometry artifact should remain the source of truth.

For `geometry.model = "virtual-center-rectilinear"`, the artifact should carry:

- `geometry.model`
- `projection.left` and `projection.right`
- `virtual_camera`
  - `model = rectilinear`
  - `focal_px`
  - `center`
  - `output_resolution`
  - `hfov_deg` when available
- per-side orientation metadata
  - `rotation_deg` or equivalent rotation representation
- optional residual alignment metadata

This keeps the contract artifact-based, which matches the current runtime prepare/run/validate structure.

## GPU-Only Branch Guidance
This branch should treat `virtual-center-rectilinear` as a candidate model with these rules:

- artifact/schema support may land before runtime support
- validation must clearly say when the model is not yet launch-ready
- runtime must not silently fall back to CPU if this model is selected
- preview/probe behavior must not distort the throughput measurements

## Acceptance Criteria
This candidate is worth promoting only if it beats or matches the current cylindrical baseline in the scenes that matter.

The minimum comparison set should include:

- static scene
- slow motion through overlap
- close object crossing overlap
- scenes that previously showed right-edge enlargement

Required metrics:

- overlap disagreement
- seam stability
- right-edge scale asymmetry
- usable canvas ratio
- stitch/transmit fps under GPU-only conditions

## Rollout
1. Keep `cylindrical-affine` as the working baseline.
2. Add artifact/schema support for `virtual-center-rectilinear`.
3. Add runtime validation messaging that marks it as candidate-only until the render path exists.
4. Implement the runtime geometry branch behind the artifact model.
5. Run side-by-side bakeoff against `cylindrical-affine`.
6. Promote only if quality and throughput both pass.

## Non-Goals
- This document does not declare rectilinear the new default.
- This document does not add Panini or spherical yet.
- This document does not solve seam/exposure GPU parity by itself.
