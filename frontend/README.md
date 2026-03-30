# Hogak Operator UI

Minimal Vite + React + TypeScript operator surface for the Hogak stitch runtime.

## Run

```bash
npm install
npm run dev
```

## Backend

The UI expects these endpoints to exist:

- `GET /api/runtime/state`
- `GET /api/runtime/events`
- `GET /api/runtime/preview.jpg`
- `GET /api/artifacts/geometry`
- `GET /api/artifacts/geometry/{name}`

If the backend is not available, the UI falls back to placeholder state so the shell still loads.

## Proxy

Set `HOGAK_API_PROXY_TARGET` to point the Vite dev server at a backend origin.

```bash
set HOGAK_API_PROXY_TARGET=http://127.0.0.1:8000
npm run dev
```
