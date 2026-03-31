import { useState } from "react";

import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { describeRuntimeActionResult, validateRuntime } from "../lib/api";
import { displayBooleanState, displayRuntimeStatus } from "../lib/display";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

function text(value: unknown, fallback = "-"): string {
  const normalized = String(value ?? "").trim();
  return normalized || fallback;
}

export function ValidationPage() {
  const { state, refreshRuntime } = useRuntimeFeed();
  const preparedPlan = state.prepared_plan as Record<string, unknown> | undefined;
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [validationResult, setValidationResult] = useState(
    "Validation has not run yet. Read-only checks will report the active artifact, geometry rollout status, and launch readiness.",
  );

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
        eyebrow="Diagnostics / Validation"
        title="Read-only Runtime Validation"
        description="Validation should confirm which geometry artifact is active, whether it is the default launch-ready model, and whether a rollback-only fallback is in use."
        status={
          <>
            <strong>{text(state.geometry_artifact_model ?? state.geometry_mode, "unknown geometry")}</strong>
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
        <MetricCard label="Validation mode" value={text(state.validation_mode, "read-only")} />
        <MetricCard
          label="Geometry model"
          value={text(state.geometry_artifact_model ?? state.geometry_mode, "unknown")}
          detail={text(state.geometry_rollout_status, "unknown")}
          tone="accent"
        />
        <MetricCard
          label="Launch ready"
          value={displayBooleanState(state.launch_ready ?? state.prepared)}
          detail={text(state.launch_ready_reason)}
          tone={Boolean(state.launch_ready ?? state.prepared) ? "accent" : "warn"}
        />
        <MetricCard
          label="Strict fresh"
          value={displayBooleanState(state.strict_fresh ?? state.running)}
          detail={`runtime=${displayRuntimeStatus(state.status ?? "idle")}`}
        />
      </div>

      <section className="panel">
        <div className="panel-title">Artifact truth</div>
        <div className="definition-list">
          <div className="definition-item">
            <span className="definition-label">Artifact path</span>
            <span className="definition-value">
              {text(state.geometry_artifact_path ?? preparedPlan?.geometry_artifact_path, "not prepared")}
            </span>
          </div>
          <div className="definition-item">
            <span className="definition-label">Artifact checksum</span>
            <span className="definition-value">{text(state.geometry_artifact_checksum, "not computed")}</span>
          </div>
          <div className="definition-item">
            <span className="definition-label">Fallback only</span>
            <span className="definition-value">{displayBooleanState(state.geometry_fallback_only)}</span>
          </div>
          <div className="definition-item">
            <span className="definition-label">Operator-visible default</span>
            <span className="definition-value">{displayBooleanState(state.geometry_operator_visible)}</span>
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
