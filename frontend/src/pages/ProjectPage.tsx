import { useState } from "react";

import { describeProjectActionResult, startProject, stopProject } from "../lib/api";
import { displayPhaseLabel, normalizeDisplayPhase, START_FLOW } from "../lib/projectPhase";
import { useProjectState } from "../lib/useProjectState";

const STATUS_LABELS: Record<string, string> = {
  idle: "Idle",
  starting: "Starting",
  running: "Running",
  blocked: "Blocked",
  error: "Error",
};

function debugTone(state: string): string {
  const normalized = String(state || "").trim().toLowerCase();
  if (normalized === "done") {
    return "done";
  }
  if (normalized === "current") {
    return "current";
  }
  if (normalized === "failed") {
    return "failed";
  }
  return "pending";
}

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

function directnessLabel(output: ReturnType<typeof useProjectState>["state"]["output"]): string {
  if (output?.direct) {
    return "Direct output path";
  }
  if (output?.bridge) {
    return "Bridge output path";
  }
  if (output?.mode) {
    return text(output.mode);
  }
  return "Unknown output path";
}

function formatLogTime(timestampSec: number | undefined): string {
  if (!timestampSec || !Number.isFinite(timestampSec)) {
    return "--:--:--";
  }
  return new Date(timestampSec * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function ProjectPage() {
  const { state, loading, refresh } = useProjectState();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState("");
  const geometry = state.geometry || {};
  const runtime = state.runtime || {};
  const output = state.output || {};
  const zeroCopy = state.zero_copy || {};
  const debug = state.debug || {};

  const status = String(state.lifecycle_state || "idle").trim().toLowerCase() || "idle";
  const displayPhase = normalizeDisplayPhase(state.phase, status);
  const viewMode = viewModeForStatus(status);
  const statusLabel = STATUS_LABELS[status] || text(state.lifecycle_state, "Unknown");
  const phaseLabel = displayPhaseLabel(state.phase, status);
  const receiveUri = text(output.receive_uri, "");
  const receiveTarget = receiveUri || text(output.target, "");
  const outputFailure = text(output.last_error, "");
  const outputBridgeReason = text(output.bridge_reason, "");
  const activeModel = text(runtime.active_model || geometry.model, "Not active");
  const activeResidual = text(runtime.active_residual_model || geometry.residual_model, "Unknown");
  const activeArtifactPath = text(geometry.artifact_path, "Not available");
  const activeChecksum = text(geometry.artifact_checksum, "Not available");
  const readyReason = text(geometry.launch_ready_reason, "Not available");
  const directness = directnessLabel(output);
  const zeroCopyReason = text(zeroCopy.reason, "Not available");
  const zeroCopyBlockers = Array.isArray(zeroCopy.blockers)
    ? zeroCopy.blockers.map((item) => String(item ?? "").trim()).filter(Boolean)
    : [];
  const projectLog = Array.isArray(state.recent_events) ? state.recent_events : [];
  const debugSteps = Array.isArray(debug.steps) ? debug.steps : [];
  const debugCurrentStage = String(debug.current_stage || "").trim();

  const statusMessage =
    text(state.status_message, "") ||
    (state.running
      ? "The project is running. This page reflects the current stitched runtime output."
      : "Start Project reuses the active stitch geometry when possible and regenerates it only if needed.");

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
                  <p>It checks inputs, reuses the active rigid geometry when it is launch-ready, prepares the stitched runtime, and starts output.</p>
                  <ul className="stage-list">
                    <li>The active runtime model and artifact shown below are the source of truth after start.</li>
                    <li>The live output follows the rigid virtual-center stitch pipeline.</li>
                    <li>The external player address appears only for live runtime output.</li>
                  </ul>
                </div>
              ) : null}

              {viewMode === "starting" ? (
                <div className="stage-copy">
                  <h2>Automatic startup progress</h2>
                  <div className="progress-list" role="list" aria-label="Project start progress">
                    {START_FLOW.map((step, index) => {
                      const currentIndex = START_FLOW.findIndex((item) => item.id === displayPhase);
                      const isDone = currentIndex > index || displayPhase === "running";
                      const isCurrent = step.id === displayPhase || (displayPhase === "running" && step.id === "running");
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
                  {outputFailure ? <p className="stage-detail">Writer: {outputFailure}</p> : null}
                  {!outputFailure && outputBridgeReason ? <p className="stage-detail">Bridge reason: {outputBridgeReason}</p> : null}
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

            <section className="project-log-panel" aria-label="Project log">
              <div className="project-log-header">
                <span className="output-label">Project log</span>
                <strong>Latest progress</strong>
              </div>
              {projectLog.length > 0 ? (
                <div className="project-log-list">
                  {projectLog.map((entry, index) => (
                    <div key={`${entry.id ?? entry.timestamp_sec ?? index}`} className={`project-log-item ${entry.level || "info"}`}>
                      <span className="project-log-time">{formatLogTime(entry.timestamp_sec)}</span>
                      <div className="project-log-copy">
                        <strong>{text(entry.phase, "Info")}</strong>
                        <p>{text(entry.message, "No message.")}</p>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="project-log-empty">Start Project to see the live startup progress and runtime logs here.</p>
              )}
            </section>
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
                <dd>{geometry.fallback_used ? "Yes" : "No"}</dd>
              </div>
              <div>
                <dt>Output path</dt>
                <dd>{directness}</dd>
              </div>
              <div>
                <dt>Bridge reason</dt>
                <dd>{text(output.bridge_reason, "Not available")}</dd>
              </div>
              <div>
                <dt>Writer error</dt>
                <dd>{text(output.last_error, "Not available")}</dd>
              </div>
              <div>
                <dt>Zero-copy</dt>
                <dd>{zeroCopy.ready ? "Ready" : "Not ready"}</dd>
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
                <dd>{geometry.launch_ready ? "Yes" : "No"}</dd>
              </div>
              <div>
                <dt>Ready reason</dt>
                <dd>{readyReason}</dd>
              </div>
              <div>
                <dt>GPU path</dt>
                <dd>{text(runtime.gpu_path_mode, "Unknown")}</dd>
              </div>
              <div>
                <dt>GPU path ready</dt>
                <dd>{runtime.gpu_path_ready ? "Yes" : "No"}</dd>
              </div>
            </dl>
            {zeroCopyBlockers.length > 0 ? (
              <div className="action-note">
                Zero-copy blockers: {zeroCopyBlockers.join(", ")}
              </div>
            ) : null}
          </details>

          {debug.enabled ? (
            <details className="debug-panel" open={viewMode === "starting" || viewMode === "blocked" || viewMode === "error"}>
              <summary>Debug progress</summary>
              <div className="debug-panel-copy">
                <p>
                  This debug-only panel shows the internal startup stages so we can inspect where Start Project is slowing down or failing.
                </p>
              </div>
              <div className="debug-stage-list" role="list" aria-label="Debug startup stages">
                {debugSteps.map((step, index) => {
                  const tone = debugTone(step.state || "pending");
                  const stepId = String(step.id || step.label || index);
                  const isCurrent = debugCurrentStage && step.id === debugCurrentStage;
                  return (
                    <div key={stepId} className={`debug-stage-item ${tone} ${isCurrent ? "active" : ""}`} role="listitem">
                      <div className="debug-stage-marker" aria-hidden="true" />
                      <div className="debug-stage-copy">
                        <div className="debug-stage-heading">
                          <strong>{text(step.label, "Unnamed step")}</strong>
                          <span>{text(step.state, "pending")}</span>
                        </div>
                        <p>{text(step.message, tone === "current" ? "In progress." : tone === "done" ? "Completed." : tone === "failed" ? "Failed." : "Pending.")}</p>
                      </div>
                    </div>
                  );
                })}
              </div>
            </details>
          ) : null}
        </div>
      </section>
    </main>
  );
}
