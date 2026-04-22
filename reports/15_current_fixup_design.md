# Current Fixup Design

## Summary
This document defines the next corrective pass for the current project state.

The goal is not another broad redesign. The goal is to fix the places where the current branch is internally inconsistent:

- mesh-refresh now prefers native paired capture, but fallback labeling and capture-truth guidance still need to stay aligned with runtime launch
- the product-facing surface is narrower than the repo documentation suggests
- site config guidance, build guidance, and legacy tooling still expose invalid paths
- the current test suite does not protect the public surface strongly enough

This pass should make the existing `operator-server + mesh-refresh + native runtime` structure trustworthy before larger geometry or throughput work continues.

## Problem Statement

### 1. Mesh-refresh capture truth still needs explicit guardrails
Current `mesh-refresh` runs through the rigid-only single-path runner in [runtime_mesh_refresh_runner.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/domain/geometry/refresh_runner.py) and falls back to Python/OpenCV capture only when the native capture tool is unavailable.

The runtime hot path uses the native FFmpeg reader in [ffmpeg_rtsp_reader.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/input/ffmpeg_rtsp_reader.cpp), where arrival timestamps, source PTS, buffering, and pairing truth are established.

That means the branch has mostly closed the old capture-truth gap, but it still needs clear bundle-path safety, explicit fallback signaling, and docs/tests that treat the native capture artifact as the default truth.

### 2. The public surface is smaller than the repo implies
The actual supported control surface is:

- `python -m stitching.cli operator-server`
- `python -m stitching.cli mesh-refresh`
- `GET /api/project/state`
- `POST /api/project/start`
- `POST /api/project/stop`

This truth is defined in [cli.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/cli.py) and [runtime_backend.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/domain/runtime/backend/app.py).

Any additional user-facing command or API described elsewhere is drift unless it is still implemented and supported.

### 3. Operational guidance is still partly misleading
The project now expects placeholder values in `config/runtime.json` and site-local secrets in `config/runtime.local.json`, but not every message and document consistently reflects that.

Build instructions also still assume environment state that is not actually guaranteed on a fresh Windows machine.

### 4. Legacy tools still leak into the working tree
Some tools are no longer part of the supported product flow, but they remain in the default tree and look official. A tool that imports removed modules or expects outdated flows creates false signal during maintenance and onboarding.

### 5. Public-surface regression coverage is still too weak
The existing tests cover internal logic well, but they do not fully defend the current product boundary. That allows CLI drift, config guidance drift, and legacy tool breakage to slip through.

## Goals
This corrective pass must:

1. Make geometry generation consume the same capture truth as runtime launch.
2. Freeze one explicit public surface and label all other paths as internal or legacy.
3. Make config and build guidance operationally truthful.
4. Move unsupported tooling out of the happy path or fail explicitly.
5. Add regression coverage for the supported surface.

## Non-Goals
- No change to the main product goal: Windows, NVIDIA, dual RTSP, native runtime hot path.
- No projection-model reset in this pass.
- No new deep matcher or multi-model experiment pack in this pass.
- No attempt to preserve every historical command name as a compatibility alias.

## Target State

### A. One capture truth for geometry and runtime
Introduce a small native-owned capture artifact for mesh-refresh input.

The target flow is:

1. `mesh-refresh` asks the native side for a short paired capture session.
2. The native side records a clip or frame bundle together with pairing metadata.
3. Python geometry code consumes that native capture artifact rather than opening RTSP streams independently.
4. The chosen runtime geometry artifact stores the capture session identity used to create it.

The important rule is:

- geometry may still be solved in Python
- capture truth may not be invented in Python once runtime truth is native-owned

### B. One frozen product surface
The repo should treat the following as the only supported operator-facing boundary:

- CLI: `operator-server`, `mesh-refresh`
- API: `/api/project/state`, `/api/project/start`, `/api/project/stop`
- UI: single-page project state and start/stop flow

Everything else must be one of:

- internal-only
- legacy-only
- deleted

### C. Truthful site configuration guidance
All operator-facing messages and docs should agree on:

- `config/runtime.json` is a checked-in base file with placeholder values
- `config/runtime.local.json` is the preferred site-local override
- `config/profiles/<name>.json` is a structured override layer, not a secret store

### D. Truthful build boundary
The build contract should stop pretending that `pip install` and `cmake --preset` are sufficient on a fresh machine if FFmpeg/OpenCV/CUDA paths are still required.

The target state is either:

- a documented prerequisite contract plus a doctor/bootstrap script
- a reproducible bundled dependency story

The current branch should implement the first option before attempting the second.

### E. Explicit legacy-tool policy
Unsupported tools should not silently masquerade as maintained surfaces.

Each such tool should do exactly one of the following:

- be deleted from the default tree
- fail immediately with a precise explanation
- be brought back to supported status and tested

## Proposed Design

### Workstream 1. Native capture artifact for mesh-refresh

#### New concept
Add a native capture session artifact that represents paired input truth.

Suggested shape:

```json
{
  "schema_version": 1,
  "session_id": "20260403_123456",
  "left_stream": {"uri": "..."},
  "right_stream": {"uri": "..."},
  "pairing_mode": "native-reader",
  "timing_basis": "arrival+source-pts",
  "frames": [
    {
      "index": 0,
      "left_path": "left_0000.png",
      "right_path": "right_0000.png",
      "left_arrival_ns": 0,
      "right_arrival_ns": 0,
      "left_source_pts_ns": 0,
      "right_source_pts_ns": 0
    }
  ]
}
```

#### Ownership
- Native runtime owns capture session production.
- Python mesh-refresh owns geometry scoring and artifact selection.
- Runtime backend owns orchestration and visibility.

#### Integration boundary
Add one internal method for “capture paired clip for geometry refresh”.

It may be implemented in one of two ways:

1. extend the existing native runtime control protocol to expose a short capture action
2. add a small sibling native tool that shares the same FFmpeg reader code path

For this repo, option 2 is the lower-risk first move if protocol churn would destabilize runtime launch.

#### Artifact traceability
The runtime geometry artifact should record:

- `capture_session_id`
- `capture_pairing_mode`
- `capture_timing_basis`
- `capture_created_at`

That makes it possible to explain where a launch-ready artifact came from.

### Workstream 2. Public surface freeze

#### CLI
[cli.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/cli.py) remains the source of truth. No legacy alias should be advertised in root docs, frontend docs, or internal status pages unless it is still accepted by the parser.

#### API
[runtime_backend.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/domain/runtime/backend/app.py) remains the source of truth for operator-facing API.

Internal or debug endpoints should use a clearly separate namespace if they continue to exist later, for example:

- `/_internal/...`

They must not be described as part of the operator surface.

#### UI
The React app should continue to treat project lifecycle as:

1. state
2. start
3. stop

Mesh-refresh is an internal step within start, not a separate operator workflow unless the UI intentionally exposes it later.

### Workstream 3. Config and build truth cleanup

#### Config
Consolidate all user-facing config guidance around the layered merge order:

1. `config/runtime.json`
2. `config/runtime.local.json`
3. `config/profiles/<name>.json`

Runtime validation errors should mention only that model.

#### Build
Add a lightweight Windows preflight checklist to the maintained build docs which checks:

- Python package availability
- `OpenCV_DIR`
- `HOGAK_FFMPEG_DEV_ROOT`
- optional `CUDAToolkit_ROOT`
- presence of required native binaries after build

The root README should point to that script instead of pretending the build is self-contained.

### Workstream 4. Legacy-tool boundary
Current unsupported tools should be audited and assigned one of three statuses:

- supported
- internal
- legacy

If a tool is legacy, prefer deleting it from the default tree. If it must remain temporarily, make it fail immediately with a one-line explanation that it is not part of the maintained product flow.

### Workstream 5. Regression coverage

#### Required tests
Add and keep tests for:

- current CLI commands
- rejection of removed CLI names
- RTSP config guidance text
- import-time safety of legacy comparison tools
- project API surface shape

#### Next integration test
After the native capture artifact exists, add one integration-style test that validates this path:

1. produce paired capture artifact
2. run mesh-refresh against that artifact
3. emit runtime geometry artifact with capture provenance fields present

## Migration Plan

### Phase 1. Surface stabilization
- Freeze docs to the current supported CLI and API.
- Normalize config guidance to `runtime.local.json`.
- Mark legacy tools explicitly.
- Add public-surface smoke tests.

### Phase 2. Native capture handoff
- Create native paired-capture producer.
- Replace direct OpenCV RTSP capture in mesh-refresh with capture-artifact input.
- Stamp geometry artifacts with capture provenance.

### Phase 3. Build and operator hardening
- Add Windows doctor/bootstrap checks.
- Remove remaining misleading quick-start claims.
- Add acceptance notes for fresh-machine setup.

## Acceptance Criteria
This design is considered implemented when all of the following are true:

1. `mesh-refresh` no longer opens both RTSP streams directly through Python/OpenCV for production use.
2. The root README, config README, native runtime README, and frontend README all point to the same supported CLI and API surface.
3. Runtime config errors consistently direct operators to `config/runtime.local.json`.
4. Legacy comparison tooling does not fail at import time with missing-module errors.
5. The test suite contains explicit public-surface regression coverage.

## Risks

### Risk 1. Native capture integration can destabilize launch flow
Mitigation:
- keep capture production as a separate internal action first
- avoid coupling the first version to the start handshake

### Risk 2. Geometry quality may shift when capture truth changes
Mitigation:
- treat that as expected truth correction, not accidental regression
- compare old and new artifact quality metrics during rollout

### Risk 3. Removing legacy surface names can surprise existing habits
Mitigation:
- fail loudly and clearly
- document the supported replacement in the error path

## Recommended First Implementation Order
If this work starts now, the first concrete sequence should be:

1. finish public-surface document cleanup
2. add or expand public-surface smoke tests
3. introduce native paired-capture artifact
4. switch mesh-refresh to consume that artifact
5. add provenance fields to runtime geometry artifacts
6. add Windows doctor/build checks
