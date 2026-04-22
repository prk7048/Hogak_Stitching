# Hogak Operator UI

Minimal Vite + React + TypeScript operator surface for the Hogak stitch runtime.

The maintained UI is a single page that exposes only:

1. project state
2. start project
3. stop project

## Dev

```cmd
npm install
set HOGAK_API_PROXY_TARGET=http://127.0.0.1:8088
npm run dev
```

## Build

```cmd
npm run build
```

`operator-server` expects the built bundle at `frontend/dist` unless `HOGAK_FRONTEND_DIST_DIR` is set.

## Backend Contract

Run the backend with:

```cmd
python -m stitching.cli operator-server
```

The UI expects only these product APIs:

- `GET /api/project/state`
- `POST /api/project/start`
- `POST /api/project/stop`

If the backend is unavailable, the UI falls back to a placeholder error state so the shell still loads.
