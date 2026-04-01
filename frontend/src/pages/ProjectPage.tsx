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
  refreshing_mesh: "Refreshing runtime mesh",
  preparing_runtime: "Preparing runtime",
  starting_runtime: "Starting output",
  running: "Running",
  blocked: "Blocked",
  error: "Error",
};

const START_FLOW = [
  { id: "checking_inputs", label: "Check inputs" },
  { id: "refreshing_mesh", label: "Refresh runtime mesh" },
  { id: "preparing_runtime", label: "Prepare runtime" },
  { id: "starting_runtime", label: "Start output" },
  { id: "running", label: "Running" },
] as const;

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

function viewModeForStatus(status: string): "ready" | "starting" | "running" | "blocked" | "error" {
  if (status === "starting") {
    return "starting";
  }
  if (status === "running") {
    return "running";
  }
  if (status === "blocked") {
    return "blocked";
  }
  if (status === "error") {
    return "error";
  }
  return "ready";
}

function directnessLabel(state: ReturnType<typeof useProjectState>["state"]): string {
  if (state.output_path_direct) {
    return "Direct output path";
  }
  if (state.output_path_bridge) {
    return "Bridge output path";
  }
  if (state.output_path_mode) {
    return text(state.output_path_mode);
  }
  return "Unknown output path";
}

export function ProjectPage() {
  const { state, loading, refresh } = useProjectState();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState("");

  const status = String(state.status || "idle").trim().toLowerCase() || "idle";
  const startPhase = String(state.start_phase || status).trim().toLowerCase() || status;
  const viewMode = viewModeForStatus(status);
  const statusLabel = STATUS_LABELS[status] || text(state.status, "Unknown");
  const phaseLabel = PHASE_LABELS[startPhase] || text(state.start_phase, "Ready");
  const receiveUri = text(state.output_receive_uri, "");
  const receiveTarget = receiveUri || text(state.production_output_target, "");
  const activeModel = text(state.runtime_active_model, "Not active");
  const activeResidual = text(state.runtime_active_residual_model, "Unknown");
  const activeArtifactPath = text(state.runtime_active_artifact_path, "Not available");
  const activeChecksum = text(state.runtime_artifact_checksum, "Not available");
  const readyReason = text(state.runtime_launch_ready_reason, "Not available");
  const directness = directnessLabel(state);
  const zeroCopyReason = text(state.zero_copy_reason, "Not available");
  const zeroCopyBlockers = Array.isArray(state.zero_copy_blockers)
    ? state.zero_copy_blockers.map((item) => String(item ?? "").trim()).filter(Boolean)
    : [];

  const statusMessage =
    text(state.status_message, "") ||
    (state.running
      ? "The project is running. This page reflects the current stitched runtime output."
      : "Start Project recalculates stitch geometry and starts the stitched runtime automatically.");

  const heading =
    viewMode === "running"
      ? "Project is running"
      : viewMode === "starting"
        ? "Project is starting"
        : viewMode === "blocked"
          ? "Project start is blocked"
          : viewMode === "error"
            ? "Project start failed"
            : "Project is ready to start";

  const lead =
    viewMode === "running"
      ? "Use the external player to inspect the current stitched runtime output."
      : viewMode === "starting"
        ? "The page shows the current startup progress. It will switch to the running state when the stitched runtime is ready."
        : viewMode === "blocked"
          ? "Review the blocker and runtime truth below before trying again."
          : viewMode === "error"
            ? "Review the error and runtime truth below, then try Start Project again."
            : "This page starts the project directly and shows the active stitched runtime state.";

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
            <h1>{heading}</h1>
            <p>{lead}</p>
          </div>
          <div className={`status-badge ${toneForStatus(status)}`}>
            <span className="status-badge-label">Status</span>
            <strong>{loading ? "Loading..." : statusLabel}</strong>
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

            <div className={`stage-panel ${viewMode}`}>
              {viewMode === "ready" ? (
                <div className="stage-copy">
                  <h2>Start Project runs the stitched runtime</h2>
                  <p>It checks inputs, recalculates stitch geometry, prepares the stitched runtime, and starts output.</p>
                  <ul className="stage-list">
                    <li>The active runtime model and artifact shown below are the source of truth after start.</li>
                    <li>The live output follows the stitched runtime pipeline.</li>
                    <li>The external player address appears only for live runtime output.</li>
                  </ul>
                </div>
              ) : null}

              {viewMode === "starting" ? (
                <div className="stage-copy">
                  <h2>Automatic startup progress</h2>
                  <div className="progress-list" role="list" aria-label="Project start progress">
                    {START_FLOW.map((step, index) => {
                      const currentIndex = START_FLOW.findIndex((item) => item.id === startPhase);
                      const isDone = currentIndex > index || startPhase === "running";
                      const isCurrent = step.id === startPhase || (startPhase === "running" && step.id === "running");
                      return (
                        <div
                          key={step.id}
                          className={`progress-item ${isDone ? "done" : ""} ${isCurrent ? "current" : ""}`}
                          role="listitem"
                        >
                          <span className="progress-dot" aria-hidden="true" />
                          <div>
                            <strong>{step.label}</strong>
                            <p>{isCurrent ? "In progress." : isDone ? "Done." : "Pending."}</p>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : null}

              {viewMode === "running" ? (
                <div className="stage-copy">
                  <h2>Inspect the stitched runtime output</h2>
                  <p>Open the external player with the address below to inspect the current stitched runtime output.</p>
                </div>
              ) : null}

              {viewMode === "blocked" || viewMode === "error" ? (
                <div className="stage-copy">
                  <h2>{viewMode === "blocked" ? "Current blocker" : "Current error"}</h2>
                  <p>{text(state.blocker_reason || state.status_message, "No reason was provided.")}</p>
                </div>
              ) : null}
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
              <span className="output-label">Live output address</span>
              <code>{receiveTarget || "Not available until the live runtime is ready."}</code>
              <p>This address is the current stitched runtime output target.</p>
            </div>
          </section>

          <details className="details-panel" open>
            <summary>Runtime details</summary>
            <dl className="details-grid">
              <div>
                <dt>Active model</dt>
                <dd>{activeModel}</dd>
              </div>
              <div>
                <dt>Residual</dt>
                <dd>{activeResidual}</dd>
              </div>
              <div>
                <dt>Fallback used</dt>
                <dd>{state.fallback_used ? "Yes" : "No"}</dd>
              </div>
              <div>
                <dt>Output path</dt>
                <dd>{directness}</dd>
              </div>
              <div>
                <dt>Zero-copy</dt>
                <dd>{state.zero_copy_ready ? "Ready" : "Not ready"}</dd>
              </div>
              <div>
                <dt>Zero-copy reason</dt>
                <dd>{zeroCopyReason}</dd>
              </div>
              <div>
                <dt>Artifact path</dt>
                <dd>{activeArtifactPath}</dd>
              </div>
              <div>
                <dt>Artifact checksum</dt>
                <dd>{activeChecksum}</dd>
              </div>
              <div>
                <dt>Launch ready</dt>
                <dd>{state.runtime_launch_ready ? "Yes" : "No"}</dd>
              </div>
              <div>
                <dt>Ready reason</dt>
                <dd>{readyReason}</dd>
              </div>
              <div>
                <dt>GPU path</dt>
                <dd>{text(state.gpu_path_mode, "Unknown")}</dd>
              </div>
              <div>
                <dt>GPU path ready</dt>
                <dd>{state.gpu_path_ready ? "Yes" : "No"}</dd>
              </div>
            </dl>
            {zeroCopyBlockers.length > 0 ? (
              <div className="action-note">
                Zero-copy blockers: {zeroCopyBlockers.join(", ")}
              </div>
            ) : null}
          </details>
        </div>
      </section>
    </main>
  );
}
