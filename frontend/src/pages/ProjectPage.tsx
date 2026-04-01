import { useState } from "react";

import { describeProjectActionResult, startProject, stopProject } from "../lib/api";
import { useProjectState } from "../lib/useProjectState";

const STATUS_LABELS: Record<string, string> = {
  idle: "Idle",
  starting: "Starting",
  running: "Running",
  blocked: "Blocked",
  error: "Error",
};

const PHASE_LABELS: Record<string, string> = {
  idle: "Ready",
  checking_inputs: "Checking inputs",
  refreshing_mesh: "Refreshing mesh",
  preparing_runtime: "Preparing runtime",
  starting_runtime: "Starting runtime",
  running: "Running",
  blocked: "Blocked",
  error: "Error",
};

function text(value: unknown, fallback = "-"): string {
  const normalized = String(value ?? "").trim();
  return normalized || fallback;
}

function toneForStatus(status: string): string {
  switch (status) {
    case "running":
      return "success";
    case "starting":
      return "accent";
    case "blocked":
    case "error":
      return "warn";
    default:
      return "neutral";
  }
}

export function ProjectPage() {
  const { state, loading, refresh } = useProjectState();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState("");

  const status = String(state.status || "idle").trim().toLowerCase() || "idle";
  const startPhase = String(state.start_phase || status).trim().toLowerCase() || status;
  const showDetails = status === "blocked" || status === "error";
  const statusLabel = STATUS_LABELS[status] || text(state.status, "Unknown");
  const phaseLabel = PHASE_LABELS[startPhase] || text(state.start_phase, "Ready");
  const statusMessage =
    text(state.status_message, "") ||
    (state.running
      ? "Project is running. Open the external player to confirm the panorama output."
      : "Start Project will refresh the mesh artifact automatically when needed.");

  const runAction = async (label: string, action: () => Promise<unknown>) => {
    setBusyAction(label);
    setActionStatus(`${label} in progress...`);
    try {
      const result = await action();
      setActionStatus(describeProjectActionResult(result));
      await refresh();
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
      await refresh();
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <main className="project-shell">
      <section className="project-card">
        <div className="project-header">
          <div className="project-copy">
            <span className="project-eyebrow">Hogak Panorama</span>
            <h1>Start Project</h1>
            <p>This is the whole product surface. Start or stop the live mesh panorama and inspect the current truth only when you need it.</p>
          </div>
          <div className={`status-badge ${toneForStatus(status)}`}>
            <span className="status-badge-label">Status</span>
            <strong>{loading ? "Loading" : statusLabel}</strong>
          </div>
        </div>

        <div className="project-body">
          <section className="project-main">
            <div className="phase-panel">
              <span className="phase-label">Current phase</span>
              <strong>{phaseLabel}</strong>
              <p>{statusMessage}</p>
              {actionStatus ? <div className="action-note">{actionStatus}</div> : null}
            </div>

            <div className="cta-row">
              <button
                className="primary-cta"
                disabled={busyAction !== null || !state.can_start}
                onClick={() => void runAction("Start Project", () => startProject())}
                type="button"
              >
                Start Project
              </button>
              <button
                className="secondary-cta"
                disabled={busyAction !== null || !state.can_stop}
                onClick={() => void runAction("Stop Project", () => stopProject())}
                type="button"
              >
                Stop Project
              </button>
            </div>

            <div className="output-panel">
              <span className="output-label">External player URI</span>
              <code>{text(state.output_receive_uri, "udp://@:24000")}</code>
              <p>Open this address in the external player once the project reaches the running state.</p>
            </div>
          </section>

          <details className="details-panel" open={showDetails}>
            <summary>Details</summary>
            <dl className="details-grid">
              <div>
                <dt>Active model</dt>
                <dd>{text(state.runtime_active_model, "not ready")}</dd>
              </div>
              <div>
                <dt>Residual</dt>
                <dd>{text(state.geometry_residual_model, "not ready")}</dd>
              </div>
              <div>
                <dt>Artifact path</dt>
                <dd>{text(state.runtime_active_artifact_path, "not ready")}</dd>
              </div>
              <div>
                <dt>Checksum</dt>
                <dd>{text(state.runtime_artifact_checksum, "not ready")}</dd>
              </div>
              <div>
                <dt>Launch ready</dt>
                <dd>{state.runtime_launch_ready ? "Yes" : "No"}</dd>
              </div>
              <div>
                <dt>Launch reason</dt>
                <dd>{text(state.runtime_launch_ready_reason, "not ready")}</dd>
              </div>
              <div>
                <dt>GPU path</dt>
                <dd>{text(state.gpu_path_mode, "unknown")}</dd>
              </div>
              <div>
                <dt>GPU ready</dt>
                <dd>{state.gpu_path_ready ? "Yes" : "No"}</dd>
              </div>
              <div>
                <dt>Fallback used</dt>
                <dd>{state.fallback_used ? "Yes" : "No"}</dd>
              </div>
              <div>
                <dt>Blocker</dt>
                <dd>{text(state.blocker_reason, "none")}</dd>
              </div>
            </dl>
          </details>
        </div>
      </section>
    </main>
  );
}
