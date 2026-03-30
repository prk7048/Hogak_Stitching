import { useState } from "react";

import { MetricCard } from "../components/MetricCard";
import { describeRuntimeActionResult, startRuntime, stopRuntime } from "../lib/api";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

export function OutputsPage() {
  const { state, refreshRuntime } = useRuntimeFeed();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [outputStatus, setOutputStatus] = useState("No output action run yet.");

  const runAction = async (label: string, action: () => Promise<unknown>) => {
    setBusyAction(label);
    setOutputStatus(`Running ${label.toLowerCase()}...`);
    try {
      const result = await action();
      setOutputStatus(`${label}: ${describeRuntimeActionResult(result)}`);
      await refreshRuntime();
    } catch (error) {
      setOutputStatus(`${label} failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <section className="page">
      <div className="hero">
        <div>
          <div className="eyebrow">Outputs</div>
          <h2>Make the active writer path explicit</h2>
          <p>
            The operator UI should reflect the true transmit mode instead of assuming the fastest-looking path is active.
          </p>
        </div>
      </div>
      <div className="operator-actions">
        <button className="action-button" disabled={busyAction !== null} onClick={() => void runAction("Start", () => startRuntime())} type="button">
          Start runtime
        </button>
        <button className="action-button secondary" disabled={busyAction !== null} onClick={() => void runAction("Stop", () => stopRuntime())} type="button">
          Stop runtime
        </button>
      </div>
      <div className="metric-grid">
        <MetricCard label="Probe runtime" value={String(state.output_runtime_mode ?? "unknown")} tone="accent" />
        <MetricCard label="Transmit runtime" value={String(state.production_output_runtime_mode ?? "unknown")} />
        <MetricCard label="Probe target" value={String(state.output_target ?? "n/a")} />
        <MetricCard label="Transmit target" value={String(state.production_output_target ?? "n/a")} />
      </div>
      <section className="panel">
        <div className="panel-title">Output status</div>
        <pre className="action-output">{outputStatus}</pre>
      </section>
    </section>
  );
}
