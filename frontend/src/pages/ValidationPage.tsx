import { useState } from "react";

import { MetricCard } from "../components/MetricCard";
import { describeRuntimeActionResult, prepareRuntime, validateRuntime } from "../lib/api";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

export function ValidationPage() {
  const { state, refreshRuntime } = useRuntimeFeed();
  const preparedPlan = state.prepared_plan as Record<string, unknown> | undefined;
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [validationResult, setValidationResult] = useState("No validation run yet.");

  const runAction = async (label: string, action: () => Promise<unknown>) => {
    setBusyAction(label);
    setValidationResult(`Running ${label.toLowerCase()}...`);
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
      <div className="hero">
        <div>
          <div className="eyebrow">Validation</div>
          <h2>Read-only checks before runtime changes</h2>
          <p>
            This page is intentionally observability-first. The backend should use validate-only semantics and avoid mutating calibration state.
          </p>
        </div>
      </div>
      <div className="operator-actions">
        <button className="action-button" disabled={busyAction !== null} onClick={() => void runAction("Validate", () => validateRuntime())} type="button">
          Validate runtime
        </button>
        <button className="action-button secondary" disabled={busyAction !== null} onClick={() => void runAction("Prepare", () => prepareRuntime())} type="button">
          Prepare runtime
        </button>
      </div>
      <div className="metric-grid">
        <MetricCard label="Validation mode" value={String(state.validation_mode ?? "read-only")} />
        <MetricCard label="Artifact checksum" value={String(state.geometry_artifact_checksum ?? preparedPlan?.geometry_artifact_path ?? "pending")} tone="accent" />
        <MetricCard label="Launch gate" value={String(state.launch_ready ?? state.prepared ?? "unknown")} tone="warn" />
        <MetricCard label="Strict fresh" value={String(state.strict_fresh ?? state.running ?? "unknown")} />
      </div>
      <section className="panel">
        <div className="panel-title">Checks</div>
        <ul className="check-list">
          <li>schema v2 envelope accepted</li>
          <li>unknown fields rejected</li>
          <li>geometry artifact unchanged by validate</li>
          <li>runtime lifecycle remains idle</li>
        </ul>
      </section>
      <section className="panel">
        <div className="panel-title">Latest validation result</div>
        <pre className="action-output">{validationResult}</pre>
      </section>
    </section>
  );
}
