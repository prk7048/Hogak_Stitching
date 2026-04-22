from __future__ import annotations

from html import escape
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from stitching.domain.runtime.site_config import repo_root


_FRONTEND_BLOCKED_PREFIXES = ("api/", "_internal/", "legacy/")


def _frontend_unavailable_html(frontend_path: Path) -> str:
    escaped_path = escape(str(frontend_path))
    return f"""<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Hogak 운영 화면 번들이 아직 준비되지 않았습니다</title>
    <style>
      :root {{
        color-scheme: light;
        font-family: "Aptos", "Segoe UI", sans-serif;
        background: linear-gradient(180deg, #f7fbf8 0%, #e8f0ee 100%);
        color: #1c2a2c;
      }}
      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        padding: 24px;
      }}
      main {{
        width: min(920px, 100%);
        border-radius: 24px;
        border: 1px solid rgba(28,42,44,0.08);
        background: rgba(255,255,255,0.92);
        box-shadow: 0 26px 90px rgba(61,84,80,0.12);
        padding: 28px;
      }}
      h1 {{
        margin: 0 0 10px;
        font-size: clamp(2rem, 4vw, 3rem);
      }}
      p, li {{
        color: #5f7474;
        line-height: 1.65;
      }}
      code, pre {{
        font-family: "Consolas", "Cascadia Code", monospace;
      }}
      pre {{
        margin: 14px 0;
        padding: 14px 16px;
        border-radius: 16px;
        background: rgba(28,42,44,0.06);
        border: 1px solid rgba(28,42,44,0.08);
        overflow-x: auto;
      }}
      .note {{
        margin-top: 18px;
        padding: 14px 16px;
        border-radius: 16px;
        background: rgba(89,157,255,0.12);
        border: 1px solid rgba(89,157,255,0.18);
      }}
      a {{
        color: #3069b1;
      }}
    </style>
  </head>
  <body>
    <main>
      <h1>Hogak 운영 화면 번들이 아직 준비되지 않았습니다</h1>
      <p>
        <code>operator-server</code> 는 실행 중이지만, React 번들을
        <code>{escaped_path}</code> 에서 찾지 못했습니다.
      </p>
      <p>프런트엔드를 한 번 빌드한 뒤 서버를 다시 시작하세요.</p>
      <pre>cd frontend
npm install
npm run build</pre>
      <p>빌드가 끝나면 아래 명령으로 다시 실행하면 됩니다.</p>
      <pre>python -m stitching.cli operator-server</pre>
      <p><code>HOGAK_FRONTEND_DIST_DIR</code> 환경변수로 다른 빌드 결과물을 지정할 수도 있습니다.</p>
      <div class="note">
        <strong>현재 백엔드 상태</strong>
        <ul>
          <li>제품용 public API 는 <code>/api/project/state</code>, <code>/api/project/start</code>, <code>/api/project/stop</code> 만 유지됩니다.</li>
          <li>runtime debug, artifact admin, calibration 경로는 public surface에서 제거되었고 내부 경로로만 유지됩니다.</li>
          <li>이 브랜치의 기본 truth 는 <code>virtual-center-rectilinear-rigid</code> 이며, launch-ready rigid artifact 가 준비되기 전에는 시작이 차단됩니다.</li>
          <li>React 번들이 준비되면 단일 페이지에서 <code>Project state</code>, <code>Start Project</code>, <code>Stop Project</code> 흐름만 노출됩니다.</li>
        </ul>
      </div>
    </main>
  </body>
</html>"""


def resolve_frontend_dist_dir(frontend_dist_dir: str | Path | None = None) -> Path:
    if frontend_dist_dir is None:
        frontend_env = os.environ.get("HOGAK_FRONTEND_DIST_DIR", "").strip()
        if frontend_env:
            return Path(frontend_env).expanduser()
        return repo_root() / "frontend" / "dist"
    return Path(frontend_dist_dir).expanduser()


def _normalized_frontend_path(full_path: str) -> str:
    return str(full_path or "").lstrip("/")


def _reject_blocked_frontend_prefix(normalized: str) -> None:
    if normalized.startswith(_FRONTEND_BLOCKED_PREFIXES):
        raise HTTPException(status_code=404, detail="not found")


def install_frontend_routes(app: FastAPI, *, frontend_dist_dir: str | Path | None = None) -> None:
    frontend_path = resolve_frontend_dist_dir(frontend_dist_dir)
    if frontend_path.is_dir():
        app.state.frontend_dist_dir = str(frontend_path)
        frontend_root = frontend_path.resolve()
        assets_dir = frontend_root / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def frontend_entrypoint(full_path: str):
            normalized = _normalized_frontend_path(full_path)
            _reject_blocked_frontend_prefix(normalized)

            candidate = (frontend_root / normalized).resolve()
            try:
                candidate.relative_to(frontend_root)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail="not found") from exc

            if candidate.is_file():
                return FileResponse(candidate)

            index_path = frontend_root / "index.html"
            if index_path.is_file():
                return FileResponse(index_path)
            raise HTTPException(status_code=404, detail="frontend unavailable")

        return

    app.state.frontend_dist_dir = ""
    app.state.frontend_dist_missing = True
    fallback_html = _frontend_unavailable_html(frontend_path.resolve())
    print(f"[operator-server] React bundle not found at {frontend_path}; serving backend-only fallback page.")

    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend_unavailable(full_path: str):
        normalized = _normalized_frontend_path(full_path)
        _reject_blocked_frontend_prefix(normalized)
        return HTMLResponse(content=fallback_html)
