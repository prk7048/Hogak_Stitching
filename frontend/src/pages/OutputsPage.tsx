import { useState } from "react";

import { MetricCard } from "../components/MetricCard";
import {
  describeRuntimeActionResult,
  outputReachabilityHint,
  outputReceiveUri,
  startRuntime,
  stopRuntime,
} from "../lib/api";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

export function OutputsPage() {
  const { state, refreshRuntime } = useRuntimeFeed();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [outputStatus, setOutputStatus] = useState("No output action run yet.");
  const probeTarget = String(state.output_target ?? "");
  const transmitTarget = String(state.production_output_target ?? "");
  const probeReceiveUri = outputReceiveUri(probeTarget);
  const transmitReceiveUri = outputReceiveUri(transmitTarget);
  const probeReachability = outputReachabilityHint(probeTarget);
  const transmitReachability = outputReachabilityHint(transmitTarget);

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
        <MetricCard label="Probe target" value={probeTarget || "n/a"} />
        <MetricCard label="Transmit target" value={transmitTarget || "n/a"} />
        <MetricCard label="Probe receive URI" value={probeReceiveUri || "n/a"} />
        <MetricCard label="Transmit receive URI" value={transmitReceiveUri || "n/a"} />
      </div>
      <section className="panel">
        <div className="panel-title">External player notes</div>
        <pre className="action-output">
          {[
            probeReachability ? `Probe: ${probeReachability}` : "Probe: target reachable from current host rules looks normal.",
            transmitReachability ? `Transmit: ${transmitReachability}` : "Transmit: target reachable from current host rules looks normal.",
            probeReceiveUri ? `Probe receive example: ffplay -fflags nobuffer -flags low_delay -f mpegts -i ${probeReceiveUri}` : "",
            transmitReceiveUri ? `Transmit receive example: ffplay -fflags nobuffer -flags low_delay -f mpegts -i ${transmitReceiveUri}` : "",
          ]
            .filter(Boolean)
            .join("\n")}
        </pre>
      </section>
      <section className="panel">
        <div className="panel-title">Output status</div>
        <pre className="action-output">{outputStatus}</pre>
      </section>
    </section>
  );
}
