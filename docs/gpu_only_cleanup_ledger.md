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

This ledger is the minimal active cleanup record for the current product path.
Top-level README files and `reports/*` should be treated as archival or drift-prone until they are explicitly rewritten.

## Official Product Truth

The official product geometry is:

- `virtual-center-rectilinear-rigid`

The only internal fallback geometry is:

- explicit rollback artifacts only

The official operator surface is now:

1. `/`

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
- `/run`
- `/validate`

Public UI entrypoint is:

- `/`

## Removed or Hidden Product APIs

The public product surface now supports only:

- `GET /api/project/state`
- `POST /api/project/start`
- `POST /api/project/stop`

The following are no longer public product APIs:

- bakeoff APIs
- calibration APIs
- legacy calibration routes
- old start-preview endpoints
- `native-runtime` / `run-runtime` public CLI entrypoint

The only kept internal runtime preparation surface is:

- `python -m stitching.cli mesh-refresh`

## Official Operator Flow

The official operator workflow is:

1. Open `/`.
2. Press `Start Project`.
3. Let the system recompute stitch geometry automatically during start.
4. Verify `udp://@:24000` from an external player.
5. Expand `Details` only when you need the active model, checksum, fallback, or GPU path truth.
6. Press `Stop Project` when finished.

`Start Project` now means only one thing:

- check inputs
- recompute stitch geometry
- prepare runtime
- wait for the first live output frame
- start transmit

## Internal Mesh Refresh

`mesh-refresh` remains as the only supported internal preparation path.

It exists to regenerate the active rigid runtime artifact from the current cameras.

It is not part of the normal operator flow.

Current internal CLI:

```powershell
python -m stitching.cli mesh-refresh --left-rtsp "rtsp://LEFT" --right-rtsp "rtsp://RIGHT"
```

The output of mesh refresh is expected to become the active rigid runtime artifact used by runtime preparation and validation.

## Internal Rigid Rollback

The only intended rollback path is:

- an explicit internal geometry artifact path

Rollback is not part of the public UI.

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

### Project start

```powershell
Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:8088/api/project/start"
```

### Project stop

```powershell
Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:8088/api/project/stop"
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

- [ ] `/` is the only operator page
- [ ] old bakeoff, geometry compare, validation, outputs, artifacts, and calibration routes redirect into `/`
- [ ] public APIs are limited to project start, stop, and state

### Runtime truth

- [ ] `runtime_active_model` reports rigid when the product path is active
- [ ] `geometry_residual_model` reports `rigid`
- [ ] `fallback_used` is false for normal operation
- [ ] checksum and artifact path match the loaded runtime artifact

### Stitch refresh flow

- [ ] `mesh-refresh` can regenerate the active rigid artifact
- [ ] `Start Project` launches transmit with the active rigid artifact
- [ ] `GET /api/project/state` reports the same active model and artifact

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
