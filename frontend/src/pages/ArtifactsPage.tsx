import { useState } from "react";

import { useRuntimeFeed } from "../lib/useRuntimeFeed";

export function ArtifactsPage() {
  const { artifacts, refreshRuntime } = useRuntimeFeed();
  const [refreshMessage, setRefreshMessage] = useState("Artifact list loads from the backend every few seconds.");

  const refreshArtifacts = async () => {
    setRefreshMessage("Refreshing artifacts...");
    await refreshRuntime();
    setRefreshMessage("Artifact list refreshed.");
  };

  return (
    <section className="page">
      <div className="hero">
        <div>
          <div className="eyebrow">Artifacts</div>
          <h2>Geometry and runtime artifacts</h2>
          <p>
            This page surfaces runtime geometry artifacts and their canonical metadata.
          </p>
        </div>
        <button className="action-button" onClick={() => void refreshArtifacts()} type="button">
          Refresh artifacts
        </button>
      </div>
      <section className="panel">
        <div className="panel-title">Status</div>
        <div className="action-output">{refreshMessage}</div>
      </section>
      <section className="panel">
        <div className="panel-title">Geometry artifacts</div>
        <div className="artifact-list">
          {artifacts.length === 0 ? <div className="muted">No artifacts found yet.</div> : null}
          {artifacts.map((artifact) => (
            <article className="artifact-item" key={artifact.name}>
              <div className="artifact-name">{artifact.name}</div>
              <pre>{JSON.stringify(artifact, null, 2)}</pre>
            </article>
          ))}
        </div>
      </section>
    </section>
  );
}
