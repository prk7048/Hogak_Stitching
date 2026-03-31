import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { CalibrationImage } from "../components/CalibrationImage";
import { MetricCard } from "../components/MetricCard";
import {
  acceptCalibrationReview,
  cancelCalibrationReview,
  fetchCalibrationReview,
  type CalibrationState,
} from "../lib/api";
import { useCalibrationState } from "../lib/useCalibrationState";

export function CalibrationReviewPage() {
  const navigate = useNavigate();
  const { state, loading, error, setState } = useCalibrationState(fetchCalibrationReview);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState("Review the stitched preview and inlier support.");

  const runAction = async (label: string, action: () => Promise<CalibrationState>) => {
    setBusyAction(label);
    setActionStatus(`${label} running...`);
    try {
      const nextState = await action();
      setState(nextState);
      setActionStatus(`${label} complete.`);
      navigate(nextState.route);
    } catch (err) {
      setActionStatus(`${label} failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyAction(null);
    }
  };

  const candidate = state?.review.candidate ?? {};

  return (
    <section className="page">
      <div className="hero">
        <div>
          <div className="eyebrow">Calibration</div>
          <h2>Review the candidate transform</h2>
          <p>Accepting calibration saves the artifact and moves to offline stitch review only. Runtime still starts from Dashboard.</p>
          <div className="action-status">{loading ? "Loading review..." : error || actionStatus}</div>
        </div>
        <div className="operator-actions">
          <button className="action-button secondary" disabled={busyAction !== null} onClick={() => void runAction("Back to calibration", cancelCalibrationReview)} type="button">
            Back to calibration
          </button>
          <button className="action-button" disabled={busyAction !== null} onClick={() => void runAction("Accept calibration", acceptCalibrationReview)} type="button">
            Accept calibration
          </button>
        </div>
      </div>
      <div className="metric-grid">
        <MetricCard label="Manual points" value={String(candidate.manual_points_count ?? 0)} detail={`inliers=${String(candidate.inliers_count ?? 0)}`} tone="accent" />
        <MetricCard label="Inlier ratio" value={Number(candidate.inlier_ratio ?? 0).toFixed(3)} detail={`ref=${String(candidate.homography_reference ?? "raw")}`} />
        <MetricCard label="Reprojection" value={Number(candidate.mean_reprojection_error ?? 0).toFixed(3)} detail="lower is better" tone="warn" />
        <MetricCard
          label="Output size"
          value={Array.isArray(candidate.output_resolution) ? candidate.output_resolution.join(" x ") : "-"}
          detail={String(state?.workflow.current_step ?? "calibration-review")}
        />
      </div>
      <div className="panel-grid calibration-grid">
        <section className="panel wide">
          <div className="panel-title">Calibration stitched preview</div>
          <CalibrationImage alt="calibration stitched preview" src={state?.review.preview_image_url ?? ""} />
        </section>
        <section className="panel wide">
          <div className="panel-title">Inlier matches</div>
          <CalibrationImage alt="calibration inlier matches" src={state?.review.inlier_image_url ?? ""} />
        </section>
      </div>
    </section>
  );
}
