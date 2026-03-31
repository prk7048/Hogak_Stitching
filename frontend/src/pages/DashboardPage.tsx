import { useState } from "react";

import { MetricCard } from "../components/MetricCard";
import {
  describeRuntimeActionResult,
  outputReceiveUri,
  prepareRuntime,
  startRuntime,
  stopRuntime,
  validateRuntime,
} from "../lib/api";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

export function DashboardPage() {
  const { state, events, preview, streamState, refreshPreview, refreshRuntime } = useRuntimeFeed();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState("Ready.");
  const probeReceiveUri = outputReceiveUri(state.output_target);
  const transmitReceiveUri = outputReceiveUri(state.production_output_target);

  const runAction = async (label: string, action: () => Promise<unknown>) => {
    setBusyAction(label);
    setActionStatus(`Running ${label.toLowerCase()}...`);
    try {
      const result = await action();
      setActionStatus(`${label}: ${describeRuntimeActionResult(result)}`);
      await refreshRuntime();
      refreshPreview();
    } catch (error) {
      setActionStatus(`${label} failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <section className="page">
      <div className="hero">
        <div>
          <div className="eyebrow">Dashboard</div>
          <h2>Runtime truth at a glance</h2>
          <p>
            This surface reads runtime state, streams SSE events, and polls the probe-side preview JPEG. It does not
            display the live transmit stream.
          </p>
          <div className="action-status">{actionStatus}</div>
        </div>
        <div className="operator-actions">
          <button className="action-button" disabled={busyAction !== null} onClick={() => void runAction("Prepare", () => prepareRuntime())} type="button">
            Prepare
          </button>
          <button className="action-button" disabled={busyAction !== null} onClick={() => void runAction("Start", () => startRuntime())} type="button">
            Start
          </button>
          <button className="action-button" disabled={busyAction !== null} onClick={() => void runAction("Stop", () => stopRuntime())} type="button">
            Stop
          </button>
          <button className="action-button" disabled={busyAction !== null} onClick={() => void runAction("Validate", () => validateRuntime())} type="button">
            Validate
          </button>
          <button className="action-button secondary" disabled={busyAction !== null} onClick={refreshPreview} type="button">
            Refresh preview
          </button>
        </div>
      </div>
      <div className="metric-grid">
        <MetricCard
          label="State"
          value={String(state.status ?? "unknown")}
          detail={String(state.running ? "running" : state.prepared ? "prepared" : "idle")}
        />
        <MetricCard label="Geometry" value={String(state.geometry_mode ?? "planar-homography")} detail={String(state.seam_mode ?? "feather")} tone="accent" />
        <MetricCard label="Probe writer" value={String(state.output_runtime_mode ?? "unknown")} detail={probeReceiveUri || "probe receive URI unavailable"} />
        <MetricCard
          label="Transmit writer"
          value={String(state.production_output_runtime_mode ?? "unknown")}
          detail={transmitReceiveUri || "transmit receive URI unavailable"}
        />
        <MetricCard
          label="Transmit drops"
          value={String(state.production_output_frames_dropped ?? 0)}
          detail={`probe drops ${String(state.output_frames_dropped ?? 0)}`}
          tone={Number(state.production_output_frames_dropped ?? 0) > 0 ? "warn" : "accent"}
        />
        <MetricCard
          label="Freshness"
          value={`reuse ${String(state.reused_count ?? 0)}`}
          detail={`fresh waits ${String(state.wait_paired_fresh_count ?? 0)}`}
          tone={Number(state.reused_count ?? 0) > 0 ? "warn" : "accent"}
        />
        <MetricCard label="Preview surface" value="Probe JPEG" detail="Transmit is separate" />
        <MetricCard label="Stream" value={streamState} detail={`${events.length} recent events`} tone={streamState === "connected" ? "accent" : "warn"} />
      </div>
      <div className="panel-grid">
        <section className="panel wide">
          <div className="panel-title">Probe JPEG Preview</div>
          <img
            className="preview-image"
            src={preview}
            alt="probe jpeg preview"
            onError={(event) => {
              event.currentTarget.src =
                "data:image/svg+xml;utf8," +
                encodeURIComponent(
                  "<svg xmlns='http://www.w3.org/2000/svg' width='1280' height='720'><rect width='100%' height='100%' fill='#0b0f17'/><text x='50%' y='50%' fill='#d9d2c2' font-size='28' text-anchor='middle'>preview unavailable</text></svg>",
                );
            }}
          />
          <p className="muted">
            This image comes from the probe preview JPEG endpoint. Use the Outputs page or an external player to inspect
            the actual transmit stream separately.
          </p>
        </section>
        <section className="panel">
          <div className="panel-title">Event Log</div>
          <div className="event-list">
            {events.length === 0 ? <div className="muted">Waiting for events...</div> : null}
            {events.map((event, index) => (
              <div className="event-item" key={`${event.type}-${event.seq ?? index}`}>
                <span className="event-type">{event.type}</span>
                <span className="event-body">{JSON.stringify(event.payload ?? event.raw ?? {}, null, 0)}</span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </section>
  );
}
