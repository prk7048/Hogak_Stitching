import { useState } from "react";

import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { describeRuntimeActionResult, validateRuntime } from "../lib/api";
import { displayBooleanState, displayGeometryMode, displayRuntimeStatus } from "../lib/display";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

function text(value: unknown, fallback = "-"): string {
  const normalized = String(value ?? "").trim();
  return normalized || fallback;
}

export function ValidationPage() {
  const { state, refreshRuntime } = useRuntimeFeed();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [validationResult, setValidationResult] = useState(
    "Validation has not been run yet. Use this page to inspect active mesh runtime truth and launch readiness.",
  );

  const activeModel = text(state.runtime_active_model, "unknown");
  const activeResidual = text(state.runtime_active_residual_model, "-");
  const artifactPath = text(state.runtime_active_artifact_path, "not prepared");
  const checksum = text(state.runtime_artifact_checksum, "not computed");
  const launchReady = Boolean(state.runtime_launch_ready);
  const launchReason = text(state.runtime_launch_ready_reason, "-");
  const fallbackUsed = Boolean(state.fallback_used);
  const gpuPathMode = text(state.gpu_path_mode, "unknown");
  const gpuPathReady = Boolean(state.gpu_path_ready);

  const runAction = async (label: string, action: () => Promise<unknown>) => {
    setBusyAction(label);
    setValidationResult(`${label} in progress...`);
    try {
      const result = await action();
      setValidationResult(`${label}: ${describeRuntimeActionResult(result)}\n${JSON.stringify(result, null, 2)}`);
      await refreshRuntime();
    } catch (error) {
      setValidationResult(`${label} failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <section className="page">
      <PageHeader
        eyebrow="Validate"
        title="Validate active runtime truth"
        description="Use this page to confirm the active mesh artifact, launch readiness, fallback state, and GPU path truth."
        status={
          <>
            <strong>{displayGeometryMode(activeModel)}</strong>
            <span>{validationResult.split("\n", 1)[0]}</span>
          </>
        }
        actions={
          <button
            className="action-button"
            disabled={busyAction !== null}
            onClick={() => void runAction("Validate runtime", () => validateRuntime())}
            type="button"
          >
            Validate runtime
          </button>
        }
      />

      <div className="metric-grid metric-grid-compact">
        <MetricCard label="Runtime status" value={displayRuntimeStatus(state.status ?? "idle")} detail={text(state.validation_mode, "read-only")} />
        <MetricCard label="Active model" value={displayGeometryMode(activeModel)} detail={`residual=${activeResidual}`} tone="accent" />
        <MetricCard label="Launch ready" value={displayBooleanState(launchReady)} detail={launchReason} tone={launchReady ? "accent" : "warn"} />
        <MetricCard label="GPU path" value={gpuPathMode} detail={displayBooleanState(gpuPathReady)} tone={gpuPathReady ? "accent" : "warn"} />
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
            <span className="definition-value">{artifactPath}</span>
          </div>
          <div className="definition-item">
            <span className="definition-label">Artifact checksum</span>
            <span className="definition-value">{checksum}</span>
          </div>
          <div className="definition-item">
            <span className="definition-label">Fallback used</span>
            <span className="definition-value">{displayBooleanState(fallbackUsed)}</span>
          </div>
          <div className="definition-item">
            <span className="definition-label">Launch reason</span>
            <span className="definition-value">{launchReason}</span>
          </div>
        </div>
      </section>

      <details className="details-panel" open>
        <summary className="details-summary">Latest validation payload</summary>
        <pre className="action-output">{validationResult}</pre>
      </details>
    </section>
  );
}
