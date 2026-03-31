import { useEffect, useMemo, useState } from "react";

import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { describeRuntimeActionResult, prepareRuntime, validateRuntime } from "../lib/api";
import { displayExposureMode, displayGeometryMode, displaySeamMode } from "../lib/display";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

function text(value: unknown, fallback = "-"): string {
  const normalized = String(value ?? "").trim();
  return normalized || fallback;
}

export function GeometryComparePage() {
  const { state, artifacts, refreshRuntime } = useRuntimeFeed();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [selectedArtifactPath, setSelectedArtifactPath] = useState("");
  const [actionSummary, setActionSummary] = useState(
    "Select a launch-ready virtual-center-rectilinear artifact, then prepare or validate it.",
  );

  const visibleArtifacts = useMemo(
    () => artifacts.filter((artifact) => artifact.operator_visible !== false),
    [artifacts],
  );
  const rollbackArtifacts = useMemo(
    () => artifacts.filter((artifact) => Boolean(artifact.fallback_only) || Boolean(artifact.compat_only)),
    [artifacts],
  );

  useEffect(() => {
    const hasSelectedVisibleArtifact = visibleArtifacts.some(
      (artifact) => artifact.path === selectedArtifactPath || artifact.name === selectedArtifactPath,
    );
    if (!hasSelectedVisibleArtifact) {
      setSelectedArtifactPath(visibleArtifacts[0]?.path || visibleArtifacts[0]?.name || "");
    }
  }, [selectedArtifactPath, visibleArtifacts]);

  const selectedArtifact =
    visibleArtifacts.find(
      (artifact) => artifact.path === selectedArtifactPath || artifact.name === selectedArtifactPath,
    ) ?? visibleArtifacts[0];
  const activeArtifactPath = text(
    state.geometry_artifact_path ??
      ((state.prepared_plan as Record<string, unknown> | undefined)?.geometry_artifact_path as string | undefined),
    "",
  );
  const activeArtifact =
    artifacts.find((artifact) => artifact.path === activeArtifactPath || artifact.name === activeArtifactPath) ?? null;
  const activeModel = text(state.geometry_artifact_model ?? state.geometry_mode, "unknown");
  const rolloutStatus = text(state.geometry_rollout_status ?? activeArtifact?.geometry_rollout_status, "unknown");
  const launchReadyReason = text(state.launch_ready_reason ?? activeArtifact?.launch_ready_reason, "-");
  const activeFallbackOnly = Boolean(state.geometry_fallback_only ?? activeArtifact?.fallback_only);

  const runAction = async (label: string, action: () => Promise<unknown>) => {
    setBusyAction(label);
    setActionSummary(`${label} in progress...`);
    try {
      const result = await action();
      setActionSummary(`${label}: ${describeRuntimeActionResult(result)}`);
      await refreshRuntime();
    } catch (error) {
      setActionSummary(`${label} failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <section className="page">
      <PageHeader
        eyebrow="Diagnostics / Geometry"
        title="Default Geometry Candidate"
        description="The operator surface only promotes virtual-center-rectilinear artifacts. Legacy artifacts remain available for explicit rollback by artifact path."
        status={
          <>
            <strong>{selectedArtifact?.name ?? "No launch-ready virtual-center-rectilinear artifact found."}</strong>
            <span>{actionSummary}</span>
          </>
        }
        actions={
          <>
            <label className="field-group field-group-compact">
              <span className="field-label">Candidate artifact</span>
              <select
                className="field-input"
                value={selectedArtifactPath}
                onChange={(event) => setSelectedArtifactPath(event.target.value)}
              >
                {visibleArtifacts.length === 0 ? (
                  <option value="">No operator-visible candidate artifacts</option>
                ) : null}
                {visibleArtifacts.map((artifact) => (
                  <option key={artifact.path || artifact.name} value={artifact.path || artifact.name}>
                    {artifact.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="action-button"
              disabled={busyAction !== null || !selectedArtifact}
              onClick={() =>
                void runAction("Prepare candidate", () =>
                  prepareRuntime({
                    geometry: {
                      artifact_path: selectedArtifact?.path || selectedArtifact?.name || "",
                    },
                  }),
                )
              }
              type="button"
            >
              Prepare candidate
            </button>
            <button
              className="action-button secondary"
              disabled={busyAction !== null || !selectedArtifact}
              onClick={() =>
                void runAction("Validate candidate", () =>
                  validateRuntime({
                    geometry: {
                      artifact_path: selectedArtifact?.path || selectedArtifact?.name || "",
                    },
                  }),
                )
              }
              type="button"
            >
              Validate candidate
            </button>
          </>
        }
      />

      <div className="metric-grid metric-grid-compact">
        <MetricCard label="Active geometry" value={displayGeometryMode(activeModel)} detail={rolloutStatus} tone="accent" />
        <MetricCard
          label="Active artifact"
          value={activeArtifact?.name ?? activeArtifactPath ?? "none"}
          detail={activeArtifactPath || "-"}
        />
        <MetricCard label="Seam mode" value={displaySeamMode(state.seam_mode ?? "feather")} />
        <MetricCard label="Exposure mode" value={displayExposureMode(state.exposure_mode ?? "none")} />
      </div>

      <div className="panel-grid">
        <section className="panel">
          <div className="panel-title">Operator-visible default</div>
          <p className="muted">
            Virtual-center-rectilinear is the only geometry candidate shown here as a normal launch-ready choice.
          </p>
          <div className="definition-list">
            <div className="definition-item">
              <span className="definition-label">Selected model</span>
              <span className="definition-value">{text(selectedArtifact?.geometry_model, "none")}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">Launch readiness</span>
              <span className="definition-value">{text(selectedArtifact?.launch_ready ? "ready" : "not ready")}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">Reason</span>
              <span className="definition-value">{text(selectedArtifact?.launch_ready_reason)}</span>
            </div>
          </div>
        </section>
        <section className="panel">
          <div className="panel-title">Rollback-only artifacts</div>
          <p className="muted">
            Cylindrical-affine and other legacy artifacts stay off the normal chooser. Use their artifact path only for explicit rollback.
          </p>
          <div className="definition-list">
            <div className="definition-item">
              <span className="definition-label">Active fallback</span>
              <span className="definition-value">{activeFallbackOnly ? "yes" : "no"}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">Current reason</span>
              <span className="definition-value">{launchReadyReason}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">Rollback count</span>
              <span className="definition-value">{String(rollbackArtifacts.length)}</span>
            </div>
          </div>
          {rollbackArtifacts.length > 0 ? (
            <ul className="check-list">
              {rollbackArtifacts.map((artifact) => (
                <li key={artifact.path || artifact.name}>
                  {artifact.name} [{text(artifact.geometry_model)}] {text(artifact.path)}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No rollback-only artifacts were found in the current artifact inventory.</p>
          )}
        </section>
      </div>

      <details className="details-panel">
        <summary className="details-summary">Action summary</summary>
        <pre className="action-output">{actionSummary}</pre>
      </details>
    </section>
  );
}
