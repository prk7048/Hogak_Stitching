import { useEffect, useState } from "react";

import { MetricCard } from "../components/MetricCard";
import { describeRuntimeActionResult, prepareRuntime, validateRuntime } from "../lib/api";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

export function GeometryComparePage() {
  const { state, artifacts, refreshRuntime } = useRuntimeFeed();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [selectedArtifactPath, setSelectedArtifactPath] = useState("");
  const [actionSummary, setActionSummary] = useState("Choose a candidate artifact to prepare or validate.");

  useEffect(() => {
    if (!selectedArtifactPath && artifacts.length > 0) {
      setSelectedArtifactPath(artifacts[0]?.path || artifacts[0]?.name || "");
    }
  }, [artifacts, selectedArtifactPath]);

  const selectedArtifact = artifacts.find((artifact) => artifact.path === selectedArtifactPath || artifact.name === selectedArtifactPath) ?? artifacts[0];

  const runAction = async (label: string, action: () => Promise<unknown>) => {
    setBusyAction(label);
    setActionSummary(`Running ${label.toLowerCase()}...`);
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
      <div className="hero">
        <div>
          <div className="eyebrow">Geometry Compare</div>
          <h2>Planar baseline against cylindrical candidate</h2>
          <p>
            Dual-path rollout keeps the fallback visible while cylindrical-affine is measured and tuned.
          </p>
        </div>
      </div>
      <div className="operator-actions">
        <label className="field-group">
          <span className="field-label">Artifact</span>
          <select
            className="field-input"
            value={selectedArtifactPath}
            onChange={(event) => setSelectedArtifactPath(event.target.value)}
          >
            {artifacts.length === 0 ? <option value="">No artifacts available</option> : null}
            {artifacts.map((artifact) => (
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
            void runAction("Prepare", () =>
              prepareRuntime({
                geometry: {
                  artifact_path: selectedArtifact?.path || selectedArtifact?.name || "",
                },
              }),
            )
          }
          type="button"
        >
          Prepare selected geometry
        </button>
        <button
          className="action-button secondary"
          disabled={busyAction !== null || !selectedArtifact}
          onClick={() =>
            void runAction("Validate", () =>
              validateRuntime({
                geometry: {
                  artifact_path: selectedArtifact?.path || selectedArtifact?.name || "",
                },
              }),
            )
          }
          type="button"
        >
          Validate selected geometry
        </button>
      </div>
      <div className="metric-grid">
        <MetricCard label="Current mode" value={String(state.geometry_mode ?? "planar-homography")} tone="accent" />
        <MetricCard label="Seam mode" value={String(state.seam_mode ?? "feather")} />
        <MetricCard label="Exposure mode" value={String(state.exposure_mode ?? "none")} />
        <MetricCard label="Artifact" value={selectedArtifact?.name ?? "none"} detail={String(selectedArtifact?.model ?? selectedArtifact?.geometry_model ?? "no artifact")} />
      </div>
      <div className="panel-grid">
        <section className="panel">
          <div className="panel-title">Planar baseline</div>
          <p className="muted">Fallback path stays available until cylindrical acceptance passes.</p>
        </section>
        <section className="panel">
          <div className="panel-title">Cylindrical candidate</div>
          <p className="muted">Measure vertical misalignment, residual affine error, and seam jitter before flipping default transmit.</p>
        </section>
      </div>
      <section className="panel">
        <div className="panel-title">Action summary</div>
        <pre className="action-output">{actionSummary}</pre>
      </section>
    </section>
  );
}
