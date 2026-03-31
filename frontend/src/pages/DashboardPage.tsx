import { useState } from "react";

import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import {
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

export function DashboardPage() {
  const { state, events, preview, streamState, refreshRuntime } = useRuntimeFeed();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState("시작 버튼을 누르면 자동 준비와 실행이 한 번에 진행됩니다.");

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

  const nextAction = state.running
    ? "런타임이 실행 중입니다. 외부 플레이어에서 transmit 수신 주소로 확인하세요."
    : "Start 버튼 한 번으로 자동 캘리브레이션(필요 시), 준비, 실행까지 처리합니다.";

  const healthMessage =
    gpuOnlyMode && !gpuOnlyReady
      ? "GPU-only launch is blocked. Resolve the blockers before starting."
      : outputDropCount > 0
        ? `Transmit has dropped ${outputDropCount} frames. Check the output path before blaming geometry.`
        : reuseCount > 0 || freshnessWaits > 0
          ? `Pair reuse=${reuseCount}, fresh waits=${freshnessWaits}. Timing pressure is visible.`
          : "Runtime, geometry, and output state look stable from the operator surface.";

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
        eyebrow="Operate"
        title="원클릭 실행"
        description="다른 옵션은 숨기고 시작 버튼 한 번으로 바로 송출하는 흐름으로 단순화했습니다. 준비가 안 된 경우에는 자동 준비를 먼저 수행하고, geometry artifact 가 없으면 자동 캘리브레이션을 시도합니다."
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
              Start
            </button>
            <button
              className="action-button secondary"
              disabled={busyAction !== null}
              onClick={() => void runAction("Stop", () => stopRuntime())}
              type="button"
            >
              Stop
            </button>
          </>
        }
      />

      <section className={`status-strip${outputDropCount > 0 || (gpuOnlyMode && !gpuOnlyReady) ? " warn" : ""}`}>
        <div className="status-strip-title">실행 요약</div>
        <div className="status-strip-body">
          {previewDisabled
            ? "GPU-only 모드에서는 probe preview 를 끄고 transmit 만 사용합니다. 외부 플레이어에서는 transmit 수신 주소만 보면 됩니다."
            : "preview 는 참고용이고, 실제 출력 확인은 transmit 수신 주소 기준으로 하세요."}
        </div>
        <div className="status-strip-footnote">{healthMessage}</div>
      </section>

      <div className="metric-grid metric-grid-compact">
        <MetricCard
          label="Runtime"
          value={displayRuntimeStatus(state.status ?? "unknown")}
          detail={displayRuntimeStatus(state.running ? "running" : state.prepared ? "prepared" : "idle")}
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
          <div className="panel-title">미리보기</div>
          <div className="panel-subtitle">
            {previewDisabled
              ? "이 브랜치에서는 probe 를 끄고 transmit 경로에 집중합니다."
              : "preview 는 참고용이며 최종 출력 확인은 transmit 주소로 하세요."}
          </div>
          {previewDisabled ? (
            <div className="muted">
              외부 플레이어에서 <code>{transmitReceiveUri || "missing receive URI"}</code> 를 열어 실제 송출 화면을 확인하세요.
            </div>
          ) : (
            <img
              className="preview-image"
              src={preview}
              alt="Operator preview"
              onError={(event) => {
                event.currentTarget.src =
                  "data:image/svg+xml;utf8," +
                  encodeURIComponent(
                    "<svg xmlns='http://www.w3.org/2000/svg' width='1280' height='720'><rect width='100%' height='100%' fill='#0b0f17'/><text x='50%' y='50%' fill='#d9d2c2' font-size='28' text-anchor='middle'>Preview unavailable</text></svg>",
                  );
              }}
            />
          )}
        </section>

        <section className="panel panel-stack">
          <div className="panel-title">Geometry 상태</div>
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
        </section>

        <section className="panel panel-stack">
          <div className="panel-title">출력 확인</div>
          <div className="definition-list">
            <div className="definition-item">
              <span className="definition-label">외부 플레이어 수신 주소</span>
              <span className="definition-value">{transmitReceiveUri || "unavailable"}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">프레임 드롭</span>
              <span className="definition-value">{String(outputDropCount)}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">Probe 경로</span>
              <span className="definition-value">
                {previewDisabled ? "disabled (gpu-only)" : probeReceiveUri || "missing receive URI"}
              </span>
            </div>
            <div className="definition-item">
              <span className="definition-label">GPU 상태 사유</span>
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
