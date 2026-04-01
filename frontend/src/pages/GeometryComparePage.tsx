import { useEffect, useState } from "react";

import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import {
  apiUrl,
  fetchGeometryBakeoffState,
  promoteGeometryBakeoffWinner,
  runGeometryBakeoff,
  selectGeometryBakeoffWinner,
  type GeometryBakeoffCandidate,
  type GeometryBakeoffState,
} from "../lib/api";

function metric(value: number, digits = 3): string {
  if (!Number.isFinite(value)) {
    return "-";
  }
  return Number(value).toFixed(digits);
}

function candidatePreview(url?: string): string {
  return url ? apiUrl(url) : "";
}

function emptyState(): GeometryBakeoffState {
  return {
    status: "idle",
    session_id: "",
    bundle_dir: "",
    selected_candidate_model: "",
    promoted_candidate_model: "",
    runtime_active_artifact_path: "",
    candidates: [],
  };
}

function CandidateCard({
  candidate,
  busy,
  onSelect,
  onPromote,
}: {
  candidate: GeometryBakeoffCandidate;
  busy: boolean;
  onSelect: (model: string) => void;
  onPromote: (model: string) => void;
}) {
  return (
    <section className="panel wide">
      <div className="panel-title">{candidate.model}</div>
      <div className="panel-subtitle">
        global={candidate.global_model} / residual={candidate.residual_model} / projection={candidate.projection_model}
      </div>
      <div className="metric-grid metric-grid-compact">
        <MetricCard label="Reproj" value={metric(candidate.mean_reprojection_error_px)} detail="mean px" tone="accent" />
        <MetricCard label="Vertical p90" value={metric(candidate.vertical_misalignment_p90_px)} detail="px" />
        <MetricCard label="Right-edge drift" value={metric(candidate.right_edge_scale_drift)} detail="1.0 is better" />
        <MetricCard label="Seam visibility" value={metric(candidate.seam_visibility_score)} detail={candidate.status} tone={candidate.fallback_used ? "warn" : "calm"} />
      </div>
      <div className="metric-grid metric-grid-compact">
        <MetricCard label="Overlap luma" value={metric(candidate.overlap_luma_diff)} detail="lower is better" />
        <MetricCard label="Crop ratio" value={metric(candidate.crop_ratio)} detail="cropped/full" />
        <MetricCard label="Mesh disp" value={metric(candidate.mesh_max_displacement_px)} detail="px" />
        <MetricCard label="Mesh scale drift" value={metric(candidate.mesh_max_local_scale_drift)} detail={candidate.fallback_used ? "degraded" : "mesh active"} tone={candidate.fallback_used ? "warn" : "calm"} />
      </div>
      <div className="panel-grid calibration-grid">
        <section className="panel">
          <div className="panel-title">1-minute video</div>
          {candidate.stitched_video_url ? (
            <video
              className="calibration-image"
              controls
              muted
              loop
              playsInline
              preload="metadata"
              src={candidatePreview(candidate.stitched_video_url)}
            />
          ) : (
            <div className="muted">No comparison video yet</div>
          )}
          <div className="muted" style={{ marginTop: "8px" }}>
            {candidate.video_duration_sec ? `${metric(candidate.video_duration_sec, 1)}s / ${metric(candidate.video_fps ?? 0, 0)} fps` : "video unavailable"}
          </div>
        </section>
        <section className="panel">
          <div className="panel-title">Stitched preview</div>
          {candidate.stitched_preview_url ? <img className="calibration-image" alt={`${candidate.model} stitched preview`} src={candidatePreview(candidate.stitched_preview_url)} /> : <div className="muted">No preview</div>}
        </section>
        <section className="panel">
          <div className="panel-title">Overlap crop</div>
          {candidate.overlap_crop_url ? <img className="calibration-image" alt={`${candidate.model} overlap crop`} src={candidatePreview(candidate.overlap_crop_url)} /> : <div className="muted">No overlap crop</div>}
        </section>
      </div>
      <div className="panel-grid">
        <section className="panel">
          <div className="panel-title">Seam debug</div>
          {candidate.seam_debug_url ? <img className="calibration-image" alt={`${candidate.model} seam debug`} src={candidatePreview(candidate.seam_debug_url)} /> : <div className="muted">No seam debug</div>}
        </section>
        <section className="panel">
          <div className="panel-title">Actions</div>
          <div className="action-output">
            <div>Selected: {candidate.selected ? "yes" : "no"}</div>
            <div>Runtime artifact: {candidate.runtime_artifact_path || "not launch-ready yet"}</div>
            <div>Exposure: {candidate.exposure_model}</div>
            <div>Blend: {candidate.blend_model}</div>
            <div>Crop: {candidate.crop_model}</div>
          </div>
          <div className="operator-actions operator-actions-inline">
            <button className="action-button secondary" disabled={busy} onClick={() => onSelect(candidate.model)} type="button">
              Winner freeze
            </button>
            <button className="action-button" disabled={busy || !candidate.runtime_artifact_path} onClick={() => onPromote(candidate.model)} type="button">
              Runtime promote
            </button>
          </div>
        </section>
      </div>
    </section>
  );
}

export function GeometryComparePage() {
  const [state, setState] = useState<GeometryBakeoffState>(emptyState());
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionSummary, setActionSummary] = useState(
    "Run the offline bakeoff to generate the four auto-only geometry candidates. The active runtime artifact stays unchanged until a winner is promoted.",
  );

  const refresh = async () => {
    setState(await fetchGeometryBakeoffState());
  };

  useEffect(() => {
    void refresh();
  }, []);

  const runAction = async (label: string, action: () => Promise<GeometryBakeoffState>) => {
    setBusyAction(label);
    setActionSummary(`${label} in progress...`);
    try {
      const nextState = await action();
      setState(nextState);
      setActionSummary(`${label} completed.`);
    } catch (error) {
      setActionSummary(`${label} failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <section className="page">
      <PageHeader
        eyebrow="Calibration / Bakeoff"
        title="Offline Geometry Bakeoff"
        description="수동 점 선택 없이 auto-only bakeoff로 4개 후보를 같은 rectified clip에서 비교합니다. winner를 freeze/promote 하기 전까지 runtime artifact는 바뀌지 않습니다."
        status={
          <>
            <strong>{state.session_id ? `Session ${state.session_id}` : "No bakeoff bundle yet."}</strong>
            <span>{actionSummary}</span>
          </>
        }
        actions={
          <>
            <button
              className="action-button"
              disabled={busyAction !== null}
              onClick={() => void runAction("Run bakeoff", () => runGeometryBakeoff({ video_duration_sec: 60, video_fps: 15 }))}
              type="button"
            >
              Run 1-minute bakeoff
            </button>
            <button className="action-button secondary" disabled={busyAction !== null} onClick={() => void refresh()} type="button">
              Refresh
            </button>
          </>
        }
      />

      <div className="metric-grid metric-grid-compact">
        <MetricCard label="Bundle" value={state.session_id || "-"} detail={state.bundle_dir || "no bundle"} tone="accent" />
        <MetricCard label="Winner" value={state.selected_candidate_model || "-"} detail="freeze only" />
        <MetricCard label="Promoted" value={state.promoted_candidate_model || "-"} detail={state.runtime_active_artifact_path || "runtime unchanged"} />
        <MetricCard label="Candidates" value={String(state.candidates.length)} detail="fixed set of four" />
      </div>

      <div className="status-strip">
        <div className="status-strip-title">Bakeoff policy</div>
        <div className="status-strip-body">
          SIFT primary, ORB fallback, BFMatcher + ratio test, and RANSAC homography are only the front-end for stable
          inliers and a coarse model. Mesh is residual-only, cylindrical is excluded from the normal bakeoff set, and
          each candidate now saves a 1-minute stitched comparison video.
        </div>
      </div>

      {state.candidates.length === 0 ? (
        <section className="panel">
          <div className="panel-title">No bundle yet</div>
          <p className="muted">
            This page is not the legacy runtime artifact chooser. Click <strong>Run 1-minute bakeoff</strong> to capture a representative clip, solve the four candidates, and then render one stitched comparison video per candidate:
            <code>left-anchor-homography</code>, <code>left-anchor-homography-mesh</code>, <code>virtual-center-rectilinear-rigid</code>, and <code>virtual-center-rectilinear-mesh</code>.
          </p>
        </section>
      ) : null}

      <div className="panel-grid calibration-grid">
        {state.candidates.map((candidate) => (
          <CandidateCard
            key={candidate.model}
            candidate={candidate}
            busy={busyAction !== null}
            onSelect={(model) =>
              void runAction("Freeze winner", () =>
                selectGeometryBakeoffWinner({
                  bundle_dir: state.bundle_dir,
                  model,
                }),
              )
            }
            onPromote={(model) =>
              void runAction("Promote winner", () =>
                promoteGeometryBakeoffWinner({
                  bundle_dir: state.bundle_dir,
                  model,
                }),
              )
            }
          />
        ))}
      </div>
    </section>
  );
}
