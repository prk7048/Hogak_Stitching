# GPU-Only Product Cleanup Ledger

Last audited: 2026-04-01  
Branch: `codex/gpu-only-throughput`

## Purpose

This ledger records the product cleanup performed on the branch without rewriting the broader `README.md` or `reports/*` set.

It intentionally captures only:

- removed product surfaces
- internal-only fallback paths that remain
- the official operator flow
- mesh refresh and rigid rollback handling
- acceptance checkpoints

## Official Product Truth

The official product geometry is:

- `virtual-center-rectilinear-mesh`

The only internal fallback geometry is:

- `virtual-center-rectilinear-rigid`

The official operator surface is now:

1. `/run`
2. `/validate`

Everything else is internal, compatibility-only, or debug-only.

## Removed Product Surfaces

The following are no longer part of the public product flow:

- bakeoff comparison flows
- winner selection and promotion flows
- legacy calibration UI
- Gradio runtime UI
- calibration React pages and helpers
- old preview-only product routes
- candidate selection UI for geometry families

Removed or hidden product-facing routes now redirect or stay internal:

- `/bakeoff`
- `/geometry-compare`
- `/calibration/*`
- `/outputs`
- `/artifacts`
- `/dashboard`
- `/validation`

Public UI entrypoints are:

- `/run`
- `/validate`

Compatibility redirects still send old links into the product flow:

- `/` -> `/run`
- `/dashboard` -> `/run`
- `/validation` -> `/validate`

## Removed or Hidden Product APIs

The public product surface now supports only:

- `GET /api/runtime/state`
- `POST /api/runtime/preview-align`
- `POST /api/runtime/start`
- `POST /api/runtime/stop`
- `POST /api/runtime/validate`
- `GET /api/runtime/preview-align/assets/{name}.jpg`
- `GET /api/artifacts/geometry`
- `GET /api/artifacts/geometry/{name}`

The following are no longer public product APIs:

- bakeoff APIs
- calibration APIs
- legacy calibration routes
- old start-preview endpoints

The only kept internal runtime preparation surface is:

- `POST /_internal/runtime/mesh-refresh`
- `GET /_internal/runtime/mesh-refresh/state`

## Official Operator Flow

The official operator workflow is:

1. Run internal `mesh-refresh` only when engineering or support needs to regenerate the active mesh artifact.
2. Open `/run`.
3. Check the active mesh artifact and launch readiness.
4. Trigger alignment preview.
5. Confirm the left, right, and stitched preview frames.
6. Start transmit.
7. Verify `udp://@:24000` from an external player.
8. Open `/validate`.
9. Confirm active model, checksum, fallback state, and launch readiness.

`Start` now means only one thing:

- start transmit

It is no longer overloaded with bakeoff, selection, or hidden prepare semantics.

## Internal Mesh Refresh

`mesh-refresh` replaces bakeoff as the only supported internal preparation path.

It exists to regenerate the active mesh artifact from the current cameras.

It is not part of the normal operator flow.

Current internal CLI:

```powershell
python -m stitching.cli mesh-refresh --left-rtsp "rtsp://LEFT" --right-rtsp "rtsp://RIGHT"
```

Current internal API:

```text
POST /_internal/runtime/mesh-refresh
```

The output of mesh refresh is expected to become the active mesh artifact used by runtime preparation and validation.

## Internal Rigid Rollback

The only intended rollback model is:

- `virtual-center-rectilinear-rigid`

Rigid is not part of the public UI.

It remains available only as an explicit internal rollback path when mesh launch is blocked or mesh artifacts are invalid.

Rollback must never be silent.

If rigid is active, the runtime state must show that a fallback is in use.

## Public Runtime Truth

The product-facing runtime truth must only expose:

- `runtime_active_model`
- `runtime_active_artifact_path`
- `runtime_artifact_checksum`
- `runtime_launch_ready`
- `runtime_launch_ready_reason`
- `gpu_path_mode`
- `gpu_path_ready`
- `geometry_residual_model`
- `fallback_used`

The branch no longer treats bakeoff selection or promotion state as public operator truth.

## Zero-Copy Notes

The target runtime path remains:

- `NVDEC -> GPU NV12 -> engine direct consume -> mesh remap and blend -> native-nvenc-direct`

At this audit point, zero-copy truth is exposed in runtime state, but final confirmation still depends on native runtime validation and build verification.

`gpu_path_mode` should settle on one of:

- `native-nvenc-direct`
- `native-nvenc-bridge`
- `unavailable`

The product goal is `native-nvenc-direct`.

## Acceptance Commands

### Start operator server

```powershell
python -m stitching.cli operator-server --host 127.0.0.1 --port 8088
```

### Internal mesh refresh

```powershell
python -m stitching.cli mesh-refresh --left-rtsp "rtsp://LEFT" --right-rtsp "rtsp://RIGHT"
```

### Runtime preview

```powershell
Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:8088/api/runtime/preview-align"
```

### Runtime start

```powershell
Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:8088/api/runtime/start"
```

### Runtime stop

```powershell
Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:8088/api/runtime/stop"
```

### Runtime validate

```powershell
Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:8088/api/runtime/validate"
```

### External player

```text
udp://@:24000
```

Example with `ffplay`:

```powershell
ffplay -fflags nobuffer -flags low_delay -framedrop -strict experimental "udp://127.0.0.1:24000?fifo_size=1000000&overrun_nonfatal=1"
```

## Acceptance Checklist

### Product surface

- [ ] `/run` is the main operator page
- [ ] `/validate` is the validation page
- [ ] old bakeoff, geometry compare, and calibration routes do not expose product flows
- [ ] public runtime APIs are limited to run and validate concerns

### Runtime truth

- [ ] `runtime_active_model` reports mesh when mesh is active
- [ ] `geometry_residual_model` reports `mesh`
- [ ] `fallback_used` is false for normal operation
- [ ] checksum and artifact path match the loaded runtime artifact

### Mesh flow

- [ ] `mesh-refresh` can regenerate the active mesh artifact
- [ ] `preview-align` uses the refreshed mesh artifact
- [ ] `start` launches transmit with the active mesh artifact
- [ ] `/validate` reports the same active model and artifact

### Rollback

- [ ] rigid rollback stays internal only
- [ ] fallback is explicit and visible when active

### Zero-copy

- [ ] runtime state exposes input, output, and zero-copy truth
- [ ] final native validation confirms `native-nvenc-direct` on the product path

## Documentation Drift To Watch

These files may still describe older stories and should be updated later:

- `README.md`
- `native_runtime/README.md`
- `reports/*`

This ledger is the temporary source of truth until those documents are refreshed.
