# Hogak Stitching

This project ingests two RTSP cameras and publishes one stitched live output.

The maintained architecture is `Python control plane + C++ native runtime`.

- Python: config/profile loading, rigid geometry refresh, runtime launch, operator API/UI
- C++: RTSP ingest, pair/sync, stitch, encode, output

## Public Surface

The only supported operator-facing Python entrypoints are:

```cmd
python -m stitching.cli operator-server
python -m stitching.cli mesh-refresh
```

- `operator-server`: unified FastAPI + React operator surface, default `http://127.0.0.1:8088`
- `mesh-refresh`: internal preparation command that regenerates the active launch-ready rigid runtime artifact

The only supported operator-facing HTTP APIs are:

- `GET /api/project/state`
- `POST /api/project/start`
- `POST /api/project/stop`

Everything else is internal, compatibility-only, or removed from the maintained product path.

## Quick Start

Install Python requirements:

```cmd
python -m pip install -r requirements.txt
```

Check native prerequisites on Windows before configuring CMake:

```cmd
native_runtime\bootstrap_native_runtime.ps1
copy native_runtime\CMakeUserPresets.example.json native_runtime\CMakeUserPresets.json
```

Build the native runtime:

```cmd
cmake --preset windows-release
cmake --build --preset build-windows-release
```

Build the operator UI once:

```cmd
cd frontend
npm install
npm run build
cd ..
```

Run the operator surface:

```cmd
python -m stitching.cli operator-server
```

Run the internal rigid geometry refresh directly:

```cmd
python -m stitching.cli mesh-refresh
```

## Config

- `config/runtime.json` is the checked-in base config and keeps placeholder RTSP values.
- `config/runtime.local.json` is the preferred site-local override for real camera URLs and machine-specific values.
- `config/profiles/<name>.json` is an override layer for named operating modes, not a secret store.
- The effective merge order is `runtime.json -> runtime.local.json -> profiles/<name>.json`.

See [config/README.md](/C:/Users/Pixellot/Hogak_Stitching/config/README.md) for the current config contract.

## Verification

Run these checks before treating a branch as stable:

```cmd
python -m unittest discover -s tests -v
python -m compileall stitching
cd frontend && npm run build
```

Manual operator acceptance is documented in [docs/operator_acceptance.md](/C:/Users/Pixellot/Hogak_Stitching/docs/operator_acceptance.md).

## Repository Layout

- `config`: site config and profile overrides
- `data`: runtime artifacts and generated geometry/debug data
- `stitching`: Python control plane
- `native_runtime`: C++ native runtime
- `frontend`: React operator UI
- `docs`: current product-path docs and acceptance notes
- `reports`: minimal internal design notes
