# Hogak Stitching End-to-End Redesign Spec

## Summary
This document freezes the redesign direction for the project.

The project should remain a Windows + NVIDIA, dual-RTSP, real-time stitching system. The goal is not to re-platform the product or add experimental complexity for its own sake. The goal is to make the current product trustworthy: one canonical schema, one runtime owner, one prepare/run/validate boundary, one truthful geometry baseline, and one truthful output model.

The current design is problematic because the system has too many "almost source of truth" layers:

- Python, launcher, and native runtime do not share one canonical config contract.
- Validation mutates runtime state instead of remaining read-only.
- Runtime lifecycle is split across UI, CLI, and native runtime.
- Geometry is still described as homography-only even though the camera layout benefits from cylindrical reprojection.
- Output naming hides the real mode behind legacy aliases and fallback paths.

The replacement is better for this repo because it reduces ambiguity while preserving the project’s core strengths: in-process RTSP ingest, native hot path, and GPU-accelerated encode/output.

## Target End State
The target end state is:

1. A single schema version, `schema_version = 2`, with one canonical runtime contract.
2. A strict separation between `prepare-runtime`, `run-runtime`, and `validate-runtime`.
3. One runtime supervisor in Python that owns launch, stop, health, metrics, and cleanup.
4. A geometry baseline that uses undistort plus cylindrical reprojection, then residual affine alignment, dynamic seam selection, and exposure compensation.
5. A truthful transmit model that exposes the active path and its fallback behavior.
6. A reproducible build and deployment baseline that does not depend on incidental local environment state.

## Why The Current Choices Are Problematic
| Area | Current choice | Problem |
|---|---|---|
| Config contract | Flat alias-heavy fields like `output_*`, `production_output_*`, `probe_output_*` | The project has no single truth for launch, reload, and runtime behavior. |
| Validation | Validation can prepare or restore geometry | Validation is not read-only and therefore cannot be trusted as a stable acceptance tool. |
| Runtime ownership | UI and CLI both know too much about process lifecycle | Shutdown, restart, and cleanup guarantees are fragmented. |
| Geometry model | Single homography is the only first-class runtime model | It is too weak for a wide left/right camera layout with noticeable perspective stretch. |
| Output model | `gpu-direct` and `ffmpeg` are mixed as if they were equivalent baselines | Operators cannot tell what the runtime is actually doing. |
| Build model | Environment-dependent binary discovery and local folder assumptions | Reproducing the runtime on another machine is unnecessarily fragile. |

## Preferred Replacement
The preferred replacement is:

- Canonical schema with nested sections:
  - `inputs.left`, `inputs.right`
  - `geometry`
  - `timing.sync`
  - `outputs.probe`
  - `outputs.transmit`
  - `runtime`
- Real JSON parsing in native control-plane code.
- One supervisor abstraction in Python.
- Prepare-only geometry artifact generation.
- Cylindrical as the default reprojection model for this camera layout.
- Native NVENC as the preferred transmit path, with explicit fallback modes.
- Pinned build/bootstrap artifacts for reproducibility.

## Migration Order
1. Freeze the schema and runtime contract.
2. Remove validation-side mutation.
3. Introduce the supervisor boundary.
4. Add geometry artifact versioning and cylindrical baseline.
5. Make transmit mode explicit and truthful.
6. Remove legacy aliases after the migration window.

## Non-Goals
- No re-platforming to a different OS or vendor stack.
- No deep matcher or APAP in the first implementation wave.
- No attempt to make every legacy compatibility path permanent.

