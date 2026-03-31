import { Link } from "react-router-dom";

import { CalibrationImage } from "../components/CalibrationImage";
import { MetricCard } from "../components/MetricCard";
import { fetchStitchReview } from "../lib/api";
import { useCalibrationState } from "../lib/useCalibrationState";

export function CalibrationStitchReviewPage() {
  const { state, loading, error } = useCalibrationState(fetchStitchReview);

  return (
    <section className="page">
      <div className="hero">
        <div>
          <div className="eyebrow">Calibration</div>
          <h2>Offline stitch review</h2>
          <p>This page shows the saved geometry artifact offline. It does not start runtime or emit UDP by itself.</p>
          <div className="action-status">
            {loading ? "Loading stitch review..." : error || "If the preview looks good, move to Dashboard and run Prepare then Start."}
          </div>
        </div>
        <div className="operator-actions">
          <Link className="action-button secondary" to="/calibration/start">
            Back to start
          </Link>
          <Link className="action-button" to="/dashboard">
            Go to dashboard
          </Link>
        </div>
      </div>
      <div className="metric-grid">
        <MetricCard label="Current step" value={String(state?.workflow.current_step ?? "stitch-review")} detail="offline review only" tone="accent" />
        <MetricCard label="Probe receive" value={String(state?.stitch_review.probe_receive_uri ?? "-")} detail={String(state?.stitch_review.probe_sender_target ?? "-")} />
        <MetricCard
          label="Transmit receive"
          value={String(state?.stitch_review.transmit_receive_uri ?? "-")}
          detail={String(state?.stitch_review.transmit_sender_target ?? "-")}
        />
        <MetricCard
          label="Runtime start"
          value="Dashboard"
          detail="Prepare -> Start"
          tone={state?.stitch_review.transmit_receive_uri ? "accent" : "warn"}
        />
      </div>
      <div className="panel-grid">
        <section className="panel wide">
          <div className="panel-title">Stitch review preview</div>
          <CalibrationImage alt="stitch review preview" src={state?.stitch_review.preview_image_url ?? ""} />
        </section>
        <section className="panel">
          <div className="panel-title">External player notes</div>
          <div className="action-output">
            <div>Probe receive URI: {state?.stitch_review.probe_receive_uri || "-"}</div>
            <div>Transmit receive URI: {state?.stitch_review.transmit_receive_uri || "-"}</div>
            <div>Same-host VLC/ffplay should open the receive URI, not the sender target.</div>
            <div>
              Probe reachability: {state?.stitch_review.probe_loopback_only ? "loopback only on the same Windows host" : "network reachable target required"}
            </div>
            <div>
              Transmit reachability:{" "}
              {state?.stitch_review.transmit_loopback_only ? "loopback only on the same Windows host" : "network reachable target required"}
            </div>
          </div>
        </section>
      </div>
    </section>
  );
}
