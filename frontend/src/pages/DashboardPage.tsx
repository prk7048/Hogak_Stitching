import { useState } from "react";

import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import {
  apiUrl,
  describeRuntimeActionResult,
  outputReceiveUri,
  startRuntime,
  stopRuntime,
} from "../lib/api";
import {
  displayEventType,
  displayGeometryMode,
  displayGpuOnlyState,
  displayOutputRuntimeMode,
  displayRuntimeStatus,
  displaySeamMode,
  displayStreamState,
} from "../lib/display";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

function asBlockers(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => String(item ?? "").trim())
    .filter((item) => item.length > 0);
}

function text(value: unknown, fallback = "-"): string {
  const normalized = String(value ?? "").trim();
  return normalized || fallback;
}

function previewFallbackSvg(label: string): string {
  return (
    "data:image/svg+xml;utf8," +
    encodeURIComponent(
      `<svg xmlns='http://www.w3.org/2000/svg' width='1280' height='720'><rect width='100%' height='100%' fill='#0b0f17'/><text x='50%' y='50%' fill='#d9d2c2' font-size='28' text-anchor='middle'>${label}</text></svg>`,
    )
  );
}

function PreviewImage(props: { src: string; alt: string }) {
  return (
    <img
      className="preview-image"
      src={props.src}
      alt={props.alt}
      onError={(event) => {
        event.currentTarget.src = previewFallbackSvg("Preview unavailable");
      }}
    />
  );
}

export function DashboardPage() {
  const { state, events, preview, previewVersion, streamState, refreshRuntime } = useRuntimeFeed();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState(
    "Geometry Bakeoff에서 winner를 freeze/promote 한 뒤 Start를 누르세요. 첫 클릭은 좌/우 정렬 미리보기, 두 번째 클릭은 실제 stitched 송출입니다.",
  );

  const probeReceiveUri = outputReceiveUri(state.output_target);
  const transmitReceiveUri = outputReceiveUri(state.production_output_target);
  const outputDropCount = Number(state.production_output_frames_dropped ?? 0);
  const reuseCount = Number(state.reused_count ?? 0);
  const freshnessWaits = Number(state.wait_paired_fresh_count ?? 0);
  const gpuOnlyMode = Boolean(state.gpu_only_mode);
  const gpuOnlyReady = Boolean(state.gpu_only_ready);
  const gpuOnlyBlockers = asBlockers(state.gpu_only_blockers);
  const previewDisabled = String(state.output_runtime_mode ?? "").trim() === "none";
  const geometryModel = text(state.geometry_artifact_model ?? state.geometry_mode, "unknown");
  const geometryRolloutStatus = text(state.geometry_rollout_status, "unknown");
  const geometryArtifactPath = text(state.geometry_artifact_path, "not prepared");
  const geometryLaunchReason = text(state.launch_ready_reason, "-");
  const geometryFallbackOnly = Boolean(state.geometry_fallback_only);
  const startPreviewReady = Boolean(state.start_preview_ready);
  const startPreviewPending = Boolean(state.start_preview_pending_confirmation);

  const startPreviewLeftUrl = text(state.start_preview_left_url, "");
  const startPreviewRightUrl = text(state.start_preview_right_url, "");
  const startPreviewStitchedUrl = text(state.start_preview_stitched_url, "");

  const leftPreviewSrc = startPreviewLeftUrl ? apiUrl(`${startPreviewLeftUrl}?ts=${previewVersion}`) : "";
  const rightPreviewSrc = startPreviewRightUrl ? apiUrl(`${startPreviewRightUrl}?ts=${previewVersion}`) : "";
  const stitchedPreviewSrc = startPreviewStitchedUrl ? apiUrl(`${startPreviewStitchedUrl}?ts=${previewVersion}`) : "";

  const showAlignmentPreview = startPreviewReady && startPreviewPending && !Boolean(state.running);
  const showStitchedPreview = startPreviewReady && !startPreviewPending && Boolean(stitchedPreviewSrc);
  const runtimeArtifactReady = geometryArtifactPath !== "not prepared" && geometryArtifactPath !== "-";

  const nextAction = state.running
    ? "런타임이 실행 중입니다. 외부 플레이어에서 transmit URI를 확인하세요."
    : startPreviewPending
      ? "정렬 미리보기가 준비됐습니다. Start를 한 번 더 누르면 stitched 송출을 시작합니다."
      : runtimeArtifactReady
        ? "첫 Start는 좌/우 정렬을 따로 보여주고, 두 번째 Start가 실제 stitched 송출을 시작합니다."
        : "아직 runtime winner가 promote되지 않았습니다. Geometry Bakeoff에서 winner를 promote한 뒤 다시 시도하세요.";

  const healthMessage =
    gpuOnlyMode && !gpuOnlyReady
      ? "GPU-only launch가 차단되어 있습니다. blocker를 먼저 해결하세요."
      : !runtimeArtifactReady
        ? "현재 브랜치는 자동 캘리브레이션으로 runtime geometry를 바꾸지 않습니다. Bakeoff winner를 promote해야만 Start가 가능합니다."
      : outputDropCount > 0
        ? `Transmit에서 ${outputDropCount} 프레임이 드롭되었습니다. geometry보다 먼저 출력 처리량을 확인하세요.`
        : reuseCount > 0 || freshnessWaits > 0
          ? `Pair reuse=${reuseCount}, fresh waits=${freshnessWaits}. 타이밍 압력이 보입니다.`
          : "운영 화면 기준으로 runtime, geometry, output 상태가 안정적으로 보입니다.";

  const runAction = async (label: string, action: () => Promise<unknown>) => {
    setBusyAction(label);
    setActionStatus(`${label} in progress...`);
    try {
      const result = await action();
      setActionStatus(`${label}: ${describeRuntimeActionResult(result)}`);
      await refreshRuntime();
    } catch (error) {
      setActionStatus(`${label} failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <section className="page">
      <PageHeader
        eyebrow="운영"
        title="Winner 기반 런타임 시작"
        description="이 화면은 Bakeoff에서 promote된 winner geometry만 사용합니다. Start 첫 클릭은 virtual-center 정렬 미리보기, 두 번째 클릭은 실제 stitched runtime 시작입니다."
        status={
          <>
            <strong>{nextAction}</strong>
            <span>{actionStatus}</span>
          </>
        }
        actions={
          <>
            <button
              className="action-button"
              disabled={busyAction !== null}
              onClick={() => void runAction("Start", () => startRuntime())}
              type="button"
            >
              시작
            </button>
            <button
              className="action-button secondary"
              disabled={busyAction !== null}
              onClick={() => void runAction("Stop", () => stopRuntime())}
              type="button"
            >
              중지
            </button>
          </>
        }
      />

      <section className={`status-strip${outputDropCount > 0 || (gpuOnlyMode && !gpuOnlyReady) ? " warn" : ""}`}>
        <div className="status-strip-title">Run summary</div>
        <div className="status-strip-body">
          {showAlignmentPreview
            ? "좌/우 입력이 virtual-center plane에 각각 정렬된 모습을 먼저 보여줍니다. 이 정렬이 괜찮을 때만 Start를 다시 누르세요."
            : showStitchedPreview
              ? "이제 stitched 결과 스냅샷을 보여줍니다. 실제 송출 확인은 아래 transmit URI로 하세요."
              : previewDisabled
                ? "이 브랜치는 GPU-only 모드에서 probe preview를 기본 비활성화합니다. 실제 화면은 외부 플레이어의 transmit URI로 확인하세요."
                : "미리보기는 빠른 확인용입니다. 실제 출력은 외부 플레이어의 transmit URI로 확인하세요."}
        </div>
        <div className="status-strip-footnote">{healthMessage}</div>
      </section>

      <div className="metric-grid metric-grid-compact">
        <MetricCard
          label="Runtime"
          value={displayRuntimeStatus(state.status ?? "unknown")}
          detail={displayRuntimeStatus(state.running ? "running" : startPreviewPending ? "preview_ready" : state.prepared ? "prepared" : "idle")}
          tone="accent"
        />
        <MetricCard
          label="Geometry"
          value={displayGeometryMode(geometryModel)}
          detail={`${geometryRolloutStatus} / ${displaySeamMode(state.seam_mode ?? "feather")}`}
          tone={geometryFallbackOnly ? "warn" : "accent"}
        />
        <MetricCard
          label="GPU-only readiness"
          value={displayGpuOnlyState(state.gpu_only_ready)}
          detail={gpuOnlyMode ? "gpu-only enforced" : "gpu-only disabled"}
          tone={gpuOnlyMode && !gpuOnlyReady ? "warn" : "accent"}
        />
        <MetricCard
          label="Transmit"
          value={displayOutputRuntimeMode(state.production_output_runtime_mode ?? "unknown")}
          detail={transmitReceiveUri || "missing receive URI"}
          tone={outputDropCount > 0 ? "warn" : "accent"}
        />
        <MetricCard
          label="Pair pressure"
          value={`reuse=${String(reuseCount)}`}
          detail={`fresh waits=${String(freshnessWaits)}`}
          tone={reuseCount > 0 || freshnessWaits > 0 ? "warn" : "calm"}
        />
        <MetricCard
          label="Event stream"
          value={displayStreamState(streamState)}
          detail={`events=${events.length}`}
          tone={streamState === "connected" ? "accent" : "warn"}
        />
      </div>

      {gpuOnlyMode && gpuOnlyBlockers.length > 0 ? (
        <section className="panel">
          <div className="panel-title">GPU-only blockers</div>
          <div className="definition-list">
            {gpuOnlyBlockers.map((blocker, index) => (
              <div className="definition-item" key={`${blocker}-${index}`}>
                <span className="definition-label">Blocker {index + 1}</span>
                <span className="definition-value">{blocker}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <div className="panel-grid panel-grid-main">
        <section className="panel panel-preview">
          <div className="panel-title">
            {showAlignmentPreview ? "Virtual alignment preview" : showStitchedPreview ? "Stitched preview" : "Preview"}
          </div>
          <div className="panel-subtitle">
            {showAlignmentPreview
              ? "Inspect each side before launching the actual stitched runtime."
              : showStitchedPreview
                ? "This snapshot is generated from the same virtual-camera alignment step."
                : previewDisabled
                  ? "Probe preview is disabled on this GPU-only branch."
                  : "Live runtime preview."}
          </div>

          {showAlignmentPreview ? (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: "12px" }}>
              <div>
                <div className="muted" style={{ marginBottom: "8px" }}>
                  Left aligned
                </div>
                {leftPreviewSrc ? <PreviewImage src={leftPreviewSrc} alt="Left virtual-camera alignment preview" /> : <div className="muted">Left preview unavailable.</div>}
              </div>
              <div>
                <div className="muted" style={{ marginBottom: "8px" }}>
                  Right aligned
                </div>
                {rightPreviewSrc ? <PreviewImage src={rightPreviewSrc} alt="Right virtual-camera alignment preview" /> : <div className="muted">Right preview unavailable.</div>}
              </div>
            </div>
          ) : showStitchedPreview ? (
            <PreviewImage src={stitchedPreviewSrc} alt="Stitched preview after virtual-camera alignment" />
          ) : previewDisabled ? (
            <div className="muted">
              외부 플레이어에서 <code>{transmitReceiveUri || "missing receive URI"}</code> 를 열어 실제 runtime 출력을 확인하세요.
            </div>
          ) : (
            <PreviewImage src={preview} alt="Runtime preview" />
          )}
        </section>

        <section className="panel panel-stack">
          <div className="panel-title">Geometry status</div>
          <div className="definition-list">
            <div className="definition-item">
              <span className="definition-label">Artifact path</span>
              <span className="definition-value">{geometryArtifactPath}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">Rollout status</span>
              <span className="definition-value">{geometryRolloutStatus}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">Fallback only</span>
              <span className="definition-value">{geometryFallbackOnly ? "yes" : "no"}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">Launch reason</span>
              <span className="definition-value">{geometryLaunchReason}</span>
            </div>
          </div>
          {!runtimeArtifactReady ? (
            <div className="muted" style={{ marginTop: "12px" }}>
              먼저 Geometry Bakeoff에서 winner를 freeze 한 뒤 Runtime promote를 눌러 active runtime artifact를 만들어 주세요.
            </div>
          ) : null}
        </section>

        <section className="panel panel-stack">
          <div className="panel-title">Output check</div>
          <div className="definition-list">
            <div className="definition-item">
              <span className="definition-label">External player URI</span>
              <span className="definition-value">{transmitReceiveUri || "unavailable"}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">Dropped frames</span>
              <span className="definition-value">{String(outputDropCount)}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">Probe path</span>
              <span className="definition-value">
                {previewDisabled ? "disabled (gpu-only)" : probeReceiveUri || "missing receive URI"}
              </span>
            </div>
            <div className="definition-item">
              <span className="definition-label">GPU reason</span>
              <span className="definition-value">{text(state.gpu_reason)}</span>
            </div>
          </div>
        </section>
      </div>

      <details className="details-panel">
        <summary className="details-summary">Event log</summary>
        <div className="event-list">
          {events.length === 0 ? <div className="muted">Waiting for runtime events...</div> : null}
          {events.map((event, index) => (
            <div className="event-item" key={`${event.type}-${event.seq ?? index}`}>
              <span className="event-type">{displayEventType(event.type)}</span>
              <span className="event-body">{JSON.stringify(event.payload ?? event.raw ?? {}, null, 0)}</span>
            </div>
          ))}
        </div>
      </details>
    </section>
  );
}
