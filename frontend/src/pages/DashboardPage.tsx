import { useState } from "react";

import { MetricCard } from "../components/MetricCard";
import {
  describeRuntimeActionResult,
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
            This surface reads the current runtime state, streams events over SSE, and polls the stitched preview as a JPEG.
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
        <MetricCard label="Output" value={String(state.output_runtime_mode ?? "unknown")} detail={String(state.production_output_runtime_mode ?? "unknown")} />
        <MetricCard label="Stream" value={streamState} detail={`${events.length} recent events`} tone={streamState === "connected" ? "accent" : "warn"} />
      </div>
      <div className="panel-grid">
        <section className="panel wide">
          <div className="panel-title">Preview</div>
          <img
            className="preview-image"
            src={preview}
            alt="runtime stitched preview"
            onError={(event) => {
              event.currentTarget.src =
                "data:image/svg+xml;utf8," +
                encodeURIComponent(
                  "<svg xmlns='http://www.w3.org/2000/svg' width='1280' height='720'><rect width='100%' height='100%' fill='#0b0f17'/><text x='50%' y='50%' fill='#d9d2c2' font-size='28' text-anchor='middle'>preview unavailable</text></svg>",
                );
            }}
          />
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
