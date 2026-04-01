import { useState } from "react";

import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import {
  apiUrl,
  describeRuntimeActionResult,
  outputReceiveUri,
  previewAlignRuntime,
  startRuntime,
  stopRuntime,
} from "../lib/api";
import {
  displayGeometryMode,
  displayOutputRuntimeMode,
  displayRuntimeStatus,
  displayStreamState,
} from "../lib/display";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

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
  const { state, previewVersion, streamState, refreshRuntime } = useRuntimeFeed();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState(
    "If the active mesh artifact is ready, you can preview alignment and start transmit from here.",
  );

  const activeModel = text(state.runtime_active_model, "unknown");
  const activeResidual = text(state.runtime_active_residual_model, "-");
  const activeArtifactPath = text(state.runtime_active_artifact_path, "not prepared");
  const runtimeLaunchReady = Boolean(state.runtime_launch_ready);
  const runtimeLaunchReason = text(state.runtime_launch_ready_reason, "-");
  const gpuPathMode = text(state.gpu_path_mode, "unknown");
  const gpuPathReady = Boolean(state.gpu_path_ready);
  const fallbackUsed = Boolean(state.fallback_used);
  const transmitReceiveUri = outputReceiveUri(state.production_output_target);

  const leftPreviewUrl = text(state.preview_left_url, "");
  const rightPreviewUrl = text(state.preview_right_url, "");
  const stitchedPreviewUrl = text(state.preview_stitched_url, "");
  const previewReady = Boolean(state.preview_ready);
  const leftPreviewSrc = leftPreviewUrl ? apiUrl(`${leftPreviewUrl}?ts=${previewVersion}`) : "";
  const rightPreviewSrc = rightPreviewUrl ? apiUrl(`${rightPreviewUrl}?ts=${previewVersion}`) : "";
  const stitchedPreviewSrc = stitchedPreviewUrl ? apiUrl(`${stitchedPreviewUrl}?ts=${previewVersion}`) : "";

  const running = Boolean(state.running);
  const stage = running ? "Transmit live" : runtimeLaunchReady ? "Ready to start" : "Blocked";

  const nextAction = running
    ? `Open ${transmitReceiveUri || "udp://@:24000"} in an external player to confirm live stitched output.`
    : !runtimeLaunchReady
      ? "Launch is blocked. Open Validate to inspect the current blocker reason."
      : previewReady
        ? "Alignment preview is ready. Review the projected views, then start transmit."
        : "Preview alignment first to verify the virtual-center mesh projection before starting transmit.";

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
        eyebrow="Run"
        title="Run with the active mesh artifact"
        description="This product surface only exposes active runtime truth. Bakeoff, candidate choice, and legacy calibration are hidden from operators."
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
              disabled={busyAction !== null || running || !runtimeLaunchReady}
              onClick={() => void runAction("Preview alignment", () => previewAlignRuntime())}
              type="button"
            >
              Preview alignment
            </button>
            <button
              className="action-button"
              disabled={busyAction !== null || running || !runtimeLaunchReady}
              onClick={() => void runAction("Start transmit", () => startRuntime())}
              type="button"
            >
              Start transmit
            </button>
            <button
              className="action-button secondary"
              disabled={busyAction !== null || !running}
              onClick={() => void runAction("Stop transmit", () => stopRuntime())}
              type="button"
            >
              Stop transmit
            </button>
          </>
        }
      />

      <section className={`status-strip${!runtimeLaunchReady || !gpuPathReady ? " warn" : ""}`}>
        <div className="status-strip-title">Run summary</div>
        <div className="status-strip-body">{nextAction}</div>
        <div className="status-strip-footnote">
          {fallbackUsed
            ? "An internal fallback artifact is active. Refresh or inspect the active mesh artifact before normal operation."
            : runtimeLaunchReason}
        </div>
      </section>

      <div className="metric-grid metric-grid-compact">
        <MetricCard label="Run stage" value={stage} detail={displayRuntimeStatus(state.status ?? "unknown")} tone="accent" />
        <MetricCard label="Active model" value={displayGeometryMode(activeModel)} detail={`residual=${activeResidual}`} tone="accent" />
        <MetricCard label="Launch ready" value={runtimeLaunchReady ? "ready" : "blocked"} detail={runtimeLaunchReason} tone={runtimeLaunchReady ? "accent" : "warn"} />
        <MetricCard label="GPU path" value={gpuPathMode} detail={gpuPathReady ? "ready" : "not ready"} tone={gpuPathReady ? "accent" : "warn"} />
        <MetricCard
          label="Transmit"
          value={displayOutputRuntimeMode(state.production_output_runtime_mode ?? "unknown")}
          detail={transmitReceiveUri || "missing receive URI"}
          tone="accent"
        />
        <MetricCard
          label="Event stream"
          value={displayStreamState(streamState)}
          detail={`status=${displayRuntimeStatus(state.status ?? "unknown")}`}
          tone={streamState === "connected" ? "accent" : "warn"}
        />
      </div>

      <section className="panel">
        <div className="panel-title">Runtime truth</div>
        <div className="definition-list">
          <div className="definition-item">
            <span className="definition-label">Active model</span>
            <span className="definition-value">{displayGeometryMode(activeModel)}</span>
          </div>
          <div className="definition-item">
            <span className="definition-label">Residual</span>
            <span className="definition-value">{activeResidual}</span>
          </div>
          <div className="definition-item">
            <span className="definition-label">Artifact path</span>
            <span className="definition-value">{activeArtifactPath}</span>
          </div>
          <div className="definition-item">
            <span className="definition-label">Fallback used</span>
            <span className="definition-value">{fallbackUsed ? "yes" : "no"}</span>
          </div>
        </div>
      </section>

      <div className="panel-grid calibration-grid">
        <section className="panel">
          <div className="panel-title">Left aligned preview</div>
          {previewReady && leftPreviewSrc ? (
            <PreviewImage src={leftPreviewSrc} alt="Left aligned preview" />
          ) : (
            <div className="muted">Run alignment preview to populate the left projected view.</div>
          )}
        </section>
        <section className="panel">
          <div className="panel-title">Right aligned preview</div>
          {previewReady && rightPreviewSrc ? (
            <PreviewImage src={rightPreviewSrc} alt="Right aligned preview" />
          ) : (
            <div className="muted">Run alignment preview to populate the right projected view.</div>
          )}
        </section>
        <section className="panel">
          <div className="panel-title">Stitched preview</div>
          {previewReady && stitchedPreviewSrc ? (
            <PreviewImage src={stitchedPreviewSrc} alt="Stitched preview" />
          ) : (
            <div className="muted">After alignment preview, the stitched result appears here for a final visual check.</div>
          )}
        </section>
      </div>
    </section>
  );
}
